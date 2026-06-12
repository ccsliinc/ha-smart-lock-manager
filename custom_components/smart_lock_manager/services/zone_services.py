"""Zone-management services for Smart Lock Manager (Phase 2).

These services own the lifecycle of a :class:`~..models.zone.Zone` and the
membership of physical locks within it. They replace the retired parent/child
services (``sync_child_locks`` / ``remove_child_lock``).

Locked behavioural rules (see ``.claude/notes/zone-redesign-spec.md``):

- **One zone per lock.** A lock may belong to at most one zone. ``add`` rejects
  a lock that is already homed.
- **Leaving a zone wipes codes.** Removing a lock from a zone (or deleting the
  zone) wipes the zone's PIN codes off that lock's hardware (mock-aware in dev)
  and returns the lock to the unhomed pool. The lock entry itself is preserved.
- **The zone owns the codes.** Joining a zone (or an explicit resync) projects
  the zone's canonical code set onto the member and pushes it to the hardware.

All Z-Wave writes/clears route through the existing mock-aware helpers in
``services.zwave_services`` (``_set_usercode_with_status`` / ``_clear_usercode``)
so production behaviour is unchanged and dev runs entirely against the
``MockValueDB``.
"""

from __future__ import annotations

import logging
from typing import List, Optional, cast

from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError

from ..const import DOMAIN, PRIMARY_LOCK
from ..models.lock import SmartLockManagerLock
from ..models.zone import Zone, new_zone_id
from ..storage import delete_zone_storage, save_zone
from ..zone_runtime import get_zone_for_lock, get_zone_registry
from .zwave_services import _clear_usercode, _set_usercode_with_status

_LOGGER = logging.getLogger(__name__)

# Service-call field carrying the target zone id.
ATTR_ZONE_ID = "zone_id"
# Service-call field carrying a zone display name.
ATTR_ZONE_NAME = "name"
# Service-call field carrying an optional initial member list (create_zone).
ATTR_INITIAL_MEMBERS = "member_lock_entity_ids"
# Service-call field carrying a single lock entity id (add/remove).
ATTR_LOCK_ENTITY_ID = "lock_entity_id"


def _find_lock(hass: HomeAssistant, entity_id: str) -> Optional[SmartLockManagerLock]:
    """Return the loaded lock object for ``entity_id``, or None.

    Scans the SLM config-entry registry for the lock whose entity id matches.

    - Inputs: hass (HomeAssistant), entity_id (str).
    - Outputs: the matching ``SmartLockManagerLock`` or None when not loaded.
    """
    for entry_data in hass.data.get(DOMAIN, {}).values():
        if isinstance(entry_data, dict):
            lock = entry_data.get(PRIMARY_LOCK)
            if lock is not None and lock.lock_entity_id == entity_id:
                return cast(SmartLockManagerLock, lock)
    return None


def _entry_id_for_lock(hass: HomeAssistant, entity_id: str) -> Optional[str]:
    """Return the config-entry id that owns ``entity_id``, or None.

    Needed so a mutated member lock can be persisted via its own ``store``.

    - Inputs: hass (HomeAssistant), entity_id (str).
    - Outputs: the entry id string, or None when the lock is not loaded.
    """
    for entry_id, entry_data in hass.data.get(DOMAIN, {}).items():
        if isinstance(entry_data, dict):
            lock = entry_data.get(PRIMARY_LOCK)
            if lock is not None and lock.lock_entity_id == entity_id:
                return cast(str, entry_id)
    return None


async def _refresh_all_coordinators(hass: HomeAssistant) -> None:
    """Request a refresh on every SLM coordinator so sensors re-render.

    Membership changes affect zone sensors that may live on a different config
    entry than the lock that moved, so refresh them all.

    - Inputs: hass (HomeAssistant).
    - Outputs: None.
    """
    for entry_data in hass.data.get(DOMAIN, {}).values():
        if isinstance(entry_data, dict):
            coordinator = entry_data.get("coordinator")
            if coordinator is not None:
                await coordinator.async_request_refresh()


async def _wipe_zone_codes_from_lock(
    hass: HomeAssistant, zone: Zone, entity_id: str
) -> int:
    """Clear the zone's PIN codes off one member lock's hardware and memory.

    Clears every slot the zone configures (the canonical set) from the physical
    lock via the mock-aware ``_clear_usercode`` helper, then blanks the matching
    in-memory slots on the member lock and persists it. Per-lock usage counters
    and the access log are left intact — only code material is wiped.

    - Inputs: hass (HomeAssistant), zone (Zone whose codes are removed),
      entity_id (str member lock entity id).
    - Outputs: count of slots wiped from hardware.
    """
    lock = _find_lock(hass, entity_id)
    entry_id = _entry_id_for_lock(hass, entity_id)
    wiped = 0

    for slot_num, zone_slot in zone.code_slots.items():
        # Only bother wiping slots the zone actually populated.
        if not zone_slot.pin_code:
            continue
        try:
            await _clear_usercode(hass, entity_id, slot_num)
            wiped += 1
        except Exception as exc:  # pragma: no cover - hardware/mock failure
            _LOGGER.error(
                "Failed to wipe slot %s off %s while leaving zone '%s': %s",
                slot_num,
                entity_id,
                zone.name,
                exc,
            )

    # Blank the member lock's in-memory copy so the coordinator does not try to
    # re-push the (now orphaned) codes, then persist.
    if lock is not None:
        for slot_num in zone.code_slots:
            member_slot = lock.code_slots.get(slot_num)
            if member_slot is not None and member_slot.pin_code:
                member_slot.pin_code = None
                member_slot.user_name = None
                member_slot.is_active = False
                member_slot.is_synced = False
                member_slot.sync_error = None
                member_slot.sync_attempts = 0
                member_slot.start_date = None
                member_slot.end_date = None
                member_slot.allowed_hours = None
                member_slot.allowed_days = None
                member_slot.max_uses = -1
                member_slot.notify_on_use = False
        if entry_id is not None:
            from .. import _save_lock_data

            await _save_lock_data(hass, lock, entry_id)

    _LOGGER.info(
        "Wiped %d code(s) of zone '%s' off member %s", wiped, zone.name, entity_id
    )
    return wiped


async def _apply_zone_to_member(hass: HomeAssistant, zone: Zone, entity_id: str) -> int:
    """Push the zone's canonical code set onto one member lock's hardware.

    Mirrors the zone's code definitions onto the member (in memory) and writes
    each active, PIN-bearing slot to the physical lock via the mock-aware
    ``_set_usercode_with_status`` helper. Idempotent: re-writing an identical
    code is harmless.

    - Inputs: hass (HomeAssistant), zone (Zone), entity_id (str member id).
    - Outputs: count of slots written to hardware.
    """
    lock = _find_lock(hass, entity_id)
    if lock is None:
        _LOGGER.error(
            "Cannot apply zone '%s' codes: lock %s not loaded", zone.name, entity_id
        )
        return 0

    # Project the zone's canonical definitions onto the member in memory.
    zone.mirror_to_member(lock)

    written = 0
    for slot_num, slot in lock.code_slots.items():
        if slot.is_active and slot.pin_code:
            try:
                await _set_usercode_with_status(
                    hass, entity_id, slot_num, slot.pin_code
                )
                slot.is_synced = True
                slot.sync_error = None
                written += 1
            except Exception as exc:  # pragma: no cover - hardware/mock failure
                slot.is_synced = False
                slot.sync_error = str(exc)
                _LOGGER.error(
                    "Failed to write slot %s to %s applying zone '%s': %s",
                    slot_num,
                    entity_id,
                    zone.name,
                    exc,
                )

    entry_id = _entry_id_for_lock(hass, entity_id)
    if entry_id is not None:
        from .. import _save_lock_data

        await _save_lock_data(hass, lock, entry_id)

    _LOGGER.info(
        "Applied %d code(s) of zone '%s' to member %s", written, zone.name, entity_id
    )
    return written


class ZoneServices:
    """Service handlers for zone lifecycle and membership operations."""

    @staticmethod
    async def create_zone(hass: HomeAssistant, service_call: ServiceCall) -> None:
        """Create a new zone, optionally pre-populated with member locks.

        Generates a fresh ``zone_id``, persists the zone, registers it in the
        in-memory registry, and applies the (empty) code set to any initial
        members. Empty zones are allowed as a template to fill later. Initial
        members must be unhomed; an already-homed lock is rejected.

        - Inputs (service_call.data): ``name`` (str, required),
          ``member_lock_entity_ids`` (list[str], optional).
        - Outputs: None.
        - Raises: HomeAssistantError if any requested initial member is already
          homed in another zone.
        """
        name = service_call.data[ATTR_ZONE_NAME]
        initial_members: List[str] = list(
            service_call.data.get(ATTR_INITIAL_MEMBERS) or []
        )

        # Validate every requested member is unhomed before mutating anything.
        for entity_id in initial_members:
            existing = get_zone_for_lock(hass, entity_id)
            if existing is not None:
                raise HomeAssistantError(
                    f"Cannot create zone '{name}': lock {entity_id} is already in "
                    f"zone '{existing.name}'. Remove it first."
                )

        zone = Zone(zone_id=new_zone_id(), name=name)
        registry = get_zone_registry(hass)
        registry[zone.zone_id] = zone
        await save_zone(hass, zone)

        for entity_id in initial_members:
            if zone.add_member(entity_id):
                await _apply_zone_to_member(hass, zone, entity_id)
        if initial_members:
            await save_zone(hass, zone)

        _LOGGER.info(
            "Created zone '%s' (%s) with %d initial member(s)",
            zone.name,
            zone.zone_id,
            len(zone.member_lock_entity_ids),
        )

        hass.bus.async_fire(
            "smart_lock_manager_zone_created",
            {"zone_id": zone.zone_id, "name": zone.name},
        )
        await _refresh_all_coordinators(hass)

    @staticmethod
    async def delete_zone(hass: HomeAssistant, service_call: ServiceCall) -> None:
        """Delete a zone, unhoming and wiping every member.

        Every member lock has the zone's codes wiped off its hardware and is
        returned to the unhomed pool; the lock entries themselves are NOT
        destroyed. The zone is then removed from memory and storage.

        - Inputs (service_call.data): ``zone_id`` (str, required).
        - Outputs: None.
        - Raises: HomeAssistantError if the zone id is unknown.
        """
        zone_id = service_call.data[ATTR_ZONE_ID]
        registry = get_zone_registry(hass)
        zone = registry.get(zone_id)
        if zone is None:
            raise HomeAssistantError(f"Unknown zone_id: {zone_id}")

        members = list(zone.member_lock_entity_ids)
        for entity_id in members:
            await _wipe_zone_codes_from_lock(hass, zone, entity_id)
            zone.remove_member(entity_id)

        registry.pop(zone_id, None)
        await delete_zone_storage(hass, zone_id)

        _LOGGER.info(
            "Deleted zone '%s' (%s); unhomed %d member(s)",
            zone.name,
            zone_id,
            len(members),
        )

        hass.bus.async_fire(
            "smart_lock_manager_zone_deleted",
            {"zone_id": zone_id, "name": zone.name, "unhomed_members": members},
        )
        await _refresh_all_coordinators(hass)

    @staticmethod
    async def add_lock_to_zone(hass: HomeAssistant, service_call: ServiceCall) -> None:
        """Move an unhomed lock into a zone and apply the zone's codes.

        Enforces one-zone-per-lock: a lock already homed in any zone is
        rejected. On join, the zone's canonical code set is pushed to the new
        member's hardware.

        - Inputs (service_call.data): ``zone_id`` (str), ``lock_entity_id`` (str).
        - Outputs: None.
        - Raises: HomeAssistantError for unknown zone, unknown lock, or a lock
          that is already homed.
        """
        zone_id = service_call.data[ATTR_ZONE_ID]
        entity_id = service_call.data[ATTR_LOCK_ENTITY_ID]

        registry = get_zone_registry(hass)
        zone = registry.get(zone_id)
        if zone is None:
            raise HomeAssistantError(f"Unknown zone_id: {zone_id}")

        if _find_lock(hass, entity_id) is None:
            raise HomeAssistantError(f"Unknown lock entity: {entity_id}")

        existing = get_zone_for_lock(hass, entity_id)
        if existing is not None:
            raise HomeAssistantError(
                f"Lock {entity_id} is already in zone '{existing.name}'. A lock "
                f"can belong to only one zone — remove it from '{existing.name}' "
                f"first."
            )

        zone.add_member(entity_id)
        await save_zone(hass, zone)
        written = await _apply_zone_to_member(hass, zone, entity_id)

        _LOGGER.info(
            "Added lock %s to zone '%s'; applied %d code(s)",
            entity_id,
            zone.name,
            written,
        )

        hass.bus.async_fire(
            "smart_lock_manager_zone_member_added",
            {"zone_id": zone_id, "name": zone.name, "lock_entity_id": entity_id},
        )
        await _refresh_all_coordinators(hass)

    @staticmethod
    async def remove_lock_from_zone(
        hass: HomeAssistant, service_call: ServiceCall
    ) -> None:
        """Remove a lock from its zone, wiping the zone's codes off it.

        Wipes the zone's PIN codes off the lock's hardware and returns the lock
        to the unhomed pool. The lock entry is preserved.

        - Inputs (service_call.data): ``zone_id`` (str), ``lock_entity_id`` (str).
        - Outputs: None.
        - Raises: HomeAssistantError for unknown zone, or a lock that is not a
          member of that zone.
        """
        zone_id = service_call.data[ATTR_ZONE_ID]
        entity_id = service_call.data[ATTR_LOCK_ENTITY_ID]

        registry = get_zone_registry(hass)
        zone = registry.get(zone_id)
        if zone is None:
            raise HomeAssistantError(f"Unknown zone_id: {zone_id}")

        if not zone.has_member(entity_id):
            raise HomeAssistantError(
                f"Lock {entity_id} is not a member of zone '{zone.name}'."
            )

        wiped = await _wipe_zone_codes_from_lock(hass, zone, entity_id)
        zone.remove_member(entity_id)
        await save_zone(hass, zone)

        _LOGGER.info(
            "Removed lock %s from zone '%s'; wiped %d code(s)",
            entity_id,
            zone.name,
            wiped,
        )

        hass.bus.async_fire(
            "smart_lock_manager_zone_member_removed",
            {"zone_id": zone_id, "name": zone.name, "lock_entity_id": entity_id},
        )
        await _refresh_all_coordinators(hass)

    @staticmethod
    async def apply_zone_codes(hass: HomeAssistant, service_call: ServiceCall) -> None:
        """Re-push the zone's canonical code set to ALL member locks.

        Idempotent resync: useful after editing codes while a member was
        offline, or to recover from a drifted hardware state.

        - Inputs (service_call.data): ``zone_id`` (str, required).
        - Outputs: None.
        - Raises: HomeAssistantError if the zone id is unknown.
        """
        zone_id = service_call.data[ATTR_ZONE_ID]
        registry = get_zone_registry(hass)
        zone = registry.get(zone_id)
        if zone is None:
            raise HomeAssistantError(f"Unknown zone_id: {zone_id}")

        total = 0
        for entity_id in list(zone.member_lock_entity_ids):
            total += await _apply_zone_to_member(hass, zone, entity_id)

        _LOGGER.info(
            "Applied zone '%s' codes to %d member(s); %d write(s) total",
            zone.name,
            len(zone.member_lock_entity_ids),
            total,
        )

        hass.bus.async_fire(
            "smart_lock_manager_zone_codes_applied",
            {"zone_id": zone_id, "name": zone.name, "writes": total},
        )
        await _refresh_all_coordinators(hass)

    @staticmethod
    async def update_zone(hass: HomeAssistant, service_call: ServiceCall) -> None:
        """Update a zone's editable settings (currently its display name).

        Renames the zone and persists the change. Structured so future
        zone-level settings (slot geometry, collision-prefix length) can be
        threaded through the same service by adding optional fields.

        - Inputs (service_call.data): ``zone_id`` (str, required), ``name``
          (str, required) — the new display name.
        - Outputs: None.
        - Raises: HomeAssistantError if the zone id is unknown.
        """
        zone_id = service_call.data[ATTR_ZONE_ID]
        name = service_call.data[ATTR_ZONE_NAME]

        registry = get_zone_registry(hass)
        zone = registry.get(zone_id)
        if zone is None:
            raise HomeAssistantError(f"Unknown zone_id: {zone_id}")

        old_name = zone.name
        zone.name = name
        await save_zone(hass, zone)

        _LOGGER.info("Renamed zone %s from '%s' to '%s'", zone_id, old_name, zone.name)

        hass.bus.async_fire(
            "smart_lock_manager_zone_updated",
            {"zone_id": zone_id, "name": zone.name, "previous_name": old_name},
        )
        await _refresh_all_coordinators(hass)

    @staticmethod
    async def clear_zone_codes(hass: HomeAssistant, service_call: ServiceCall) -> None:
        """Clear EVERY code slot on a zone and wipe them off all members.

        Wipes the zone's PIN codes off every member lock's hardware (mock-aware
        in dev, mirroring ``remove_lock_from_zone`` / ``delete_zone``), then
        blanks the zone's own canonical code slots so nothing is re-pushed.
        Members remain in the zone; only code material is removed.

        - Inputs (service_call.data): ``zone_id`` (str, required).
        - Outputs: None.
        - Raises: HomeAssistantError if the zone id is unknown.
        """
        zone_id = service_call.data[ATTR_ZONE_ID]
        registry = get_zone_registry(hass)
        zone = registry.get(zone_id)
        if zone is None:
            raise HomeAssistantError(f"Unknown zone_id: {zone_id}")

        # Wipe the zone's codes off every member's hardware and in-memory copy.
        wiped = 0
        for entity_id in list(zone.member_lock_entity_ids):
            wiped += await _wipe_zone_codes_from_lock(hass, zone, entity_id)

        # Blank the zone's canonical slots so the coordinator does not re-push
        # the (now cleared) codes back onto members.
        for slot in zone.code_slots.values():
            slot.pin_code = None
            slot.user_name = None
            slot.is_active = False
            slot.is_synced = False
            slot.sync_error = None
            slot.sync_attempts = 0
            slot.start_date = None
            slot.end_date = None
            slot.allowed_hours = None
            slot.allowed_days = None
            slot.max_uses = -1
            slot.notify_on_use = False
        await save_zone(hass, zone)

        _LOGGER.info(
            "Cleared all codes on zone '%s' (%s); wiped %d code(s) across %d "
            "member(s)",
            zone.name,
            zone_id,
            wiped,
            len(zone.member_lock_entity_ids),
        )

        hass.bus.async_fire(
            "smart_lock_manager_zone_codes_cleared",
            {"zone_id": zone_id, "name": zone.name, "wiped": wiped},
        )
        await _refresh_all_coordinators(hass)
