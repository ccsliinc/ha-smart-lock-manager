"""Zone membership/application helpers for Smart Lock Manager.

Split out of ``zone_services.py`` to keep that module under the 500-line
limit. Holds the lock-lookup and per-member zone apply/wipe helpers used by
:class:`~.zone_services.ZoneServices`. The ``_save_lock_data`` import inside
``_wipe_zone_codes_from_lock`` / ``_apply_zone_to_member`` is kept lazy
(in-function) to avoid a package import cycle — do not hoist it.
"""

from __future__ import annotations

import logging
from typing import Optional, cast

from homeassistant.core import HomeAssistant

from ..const import DOMAIN, PRIMARY_LOCK
from ..models.lock import SmartLockManagerLock
from ..models.zone import Zone
from .zwave_services import _clear_usercode, _set_usercode_with_status

_LOGGER = logging.getLogger(__name__)


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
                member_slot.reset_definition()
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
