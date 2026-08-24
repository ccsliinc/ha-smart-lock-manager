"""Core lock code management services."""

import logging
from datetime import datetime
from typing import Optional

from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError
from homeassistant.util import dt as dt_util

from ..const import (
    ATTR_CODE_SLOT,
    ATTR_CODE_SLOT_NAME,
    ATTR_ENTITY_ID,
    ATTR_USER_CODE,
)
from ..models.lock import SmartLockManagerLock
from .helpers import find_lock


def _check_prefix_collision(
    lock: SmartLockManagerLock, code_slot: int, user_code: Optional[str]
) -> None:
    """Raise HomeAssistantError if ``user_code`` collides on the lock's prefix.

    Kwikset Z-Wave deadbolts silently drop user-code writes that share their
    first N digits (default 4) with an existing code. Reject early with a
    clear, user-facing error and tag the slot's ``sync_error`` for the UI.

    SECURITY: for a 4-digit lock the "prefix" IS the whole PIN, so NO slice of
    ``user_code`` is ever put into the error message or the log — the collision
    is identified by the two slot numbers alone.
    """
    if not user_code:
        return
    conflict = lock.find_prefix_conflict(user_code, code_slot)
    if conflict is None:
        return

    prefix_len = lock.code_collision_prefix_length
    message = (
        f"Cannot set slot {code_slot}: its first {prefix_len} digits match "
        f"slot {conflict.slot_number} ({conflict.user_name or 'unnamed'}). "
        f"Kwikset locks silently reject such writes — choose a PIN whose first "
        f"{prefix_len} digits differ."
    )

    target_slot = lock.code_slots.get(code_slot)
    if target_slot is not None:
        target_slot.sync_error = message
        target_slot.validation_rejections += 1
        target_slot.is_synced = False

    _LOGGER.error(
        "Prefix-collision rejection on %s: slot %s vs slot %s (prefix_len=%s)",
        lock.lock_name,
        code_slot,
        conflict.slot_number,
        prefix_len,
    )
    raise HomeAssistantError(message)


_LOGGER = logging.getLogger(__name__)


def _parse_ingested_datetime(
    field_name: str, raw: Optional[object]
) -> Optional[datetime]:
    """Parse a frontend date string into an aware UTC datetime, or None.

    Accepts a naive datetime-local (``YYYY-MM-DDTHH:MM``) or an offset-carrying
    ISO string. A naive value is assumed to be in Home Assistant's configured
    time zone and converted to UTC; an aware value is converted to UTC. An
    unparseable value logs a warning and yields None (unchanged from the prior
    behavior), never a raise.

    - Inputs: field_name (str, for logging), raw (the incoming value or None).
    - Outputs: aware UTC datetime, or None.
    """
    if not raw:
        return None
    parsed = dt_util.parse_datetime(str(raw))
    if parsed is None:
        _LOGGER.warning("Invalid %s format: %s", field_name, raw)
        return None
    # Annotated local: ``dt_util.as_utc`` is untyped to mypy under
    # --ignore-missing-imports and would otherwise make this an implicit Any
    # return, defeating the declared return type. No runtime effect.
    aware: datetime = dt_util.as_utc(parsed)
    _LOGGER.debug("Parsed %s: %s", field_name, aware)
    return aware


async def _propagate_slot_to_zone(
    hass: HomeAssistant, lock: SmartLockManagerLock, code_slot: int
) -> None:
    """Mirror a member lock's just-edited slot up to its owning zone.

    Zone model (Phase 1): the zone owns the canonical code set. When a code is
    set/cleared on any member lock via the existing per-lock services, copy the
    resulting slot DEFINITION onto the owning zone's matching slot and persist
    the zone. The coordinator's per-cycle mirror then pushes the change down to
    EVERY member lock, so a single set_code applies uniformly across the zone.

    No-op when the lock is unhomed (in no zone).

    - Inputs: hass (HomeAssistant), lock (the edited member lock),
      code_slot (the slot number that changed).
    - Outputs: None.
    """
    # Imported lazily to avoid a circular import at module load time.
    from ..models.zone import _CODE_DEFINITION_FIELDS
    from ..storage import save_zone
    from ..zone_runtime import get_zone_for_lock

    zone = get_zone_for_lock(hass, lock.lock_entity_id)
    if zone is None:
        return

    source_slot = lock.code_slots.get(code_slot)
    zone_slot = zone.code_slots.get(code_slot)
    if source_slot is None or zone_slot is None:
        return

    for fname in _CODE_DEFINITION_FIELDS:
        setattr(zone_slot, fname, getattr(source_slot, fname))

    await save_zone(hass, zone)
    _LOGGER.info(
        "Zone '%s' slot %s updated from %s; will sync to members %s",
        zone.name,
        code_slot,
        lock.lock_entity_id,
        zone.member_lock_entity_ids,
    )


class LockServices:
    """Service handler for basic lock operations."""

    @staticmethod
    async def set_code(hass: HomeAssistant, service_call: ServiceCall) -> None:
        """Set a user code (basic version)."""
        entity_id = service_call.data[ATTR_ENTITY_ID]
        code_slot = service_call.data[ATTR_CODE_SLOT]
        user_code = service_call.data[ATTR_USER_CODE]
        user_name = service_call.data.get(ATTR_CODE_SLOT_NAME)

        _LOGGER.debug(
            "set_code called for slot %s, user %s",
            code_slot,
            user_name,
        )

        # Find the lock object for this entity_id
        result = find_lock(hass, entity_id)
        if result is None:
            _LOGGER.error(
                "🔄 SET_CODE DEBUG - No lock found for entity_id: %s", entity_id
            )
            return
        lock, entry_id, _entry_data = result
        _LOGGER.debug(
            "found lock %s, setting slot %s",
            lock.lock_name,
            code_slot,
        )

        # Pre-flight: reject PIN-prefix collisions before any write
        _check_prefix_collision(lock, code_slot, user_code)

        success = lock.set_code(code_slot, user_code, user_name)
        if success:
            _LOGGER.debug(
                "set code success slot %s in lock %s",
                code_slot,
                lock.lock_name,
            )

            # Save changes to storage
            from .. import _save_lock_data

            await _save_lock_data(hass, lock, entry_id)
            _LOGGER.debug("SET_CODE: saved slot data to storage")

            # Zone model: propagate the change to the owning zone so
            # it reaches every member lock on the next coordinator
            # cycle (replaces the legacy main->child fan-out).
            await _propagate_slot_to_zone(hass, lock, code_slot)

        else:
            _LOGGER.error(
                "🔄 SET_CODE DEBUG - Failed to set code for " "slot %s in lock %s",
                code_slot,
                lock.lock_name,
            )

    @staticmethod
    async def set_code_advanced(hass: HomeAssistant, service_call: ServiceCall) -> None:
        """Set a user code with advanced scheduling features."""
        entity_id = service_call.data[ATTR_ENTITY_ID]
        code_slot = service_call.data[ATTR_CODE_SLOT]
        user_code = service_call.data[ATTR_USER_CODE]
        user_name = service_call.data.get("code_slot_name")
        # Handle date string parsing from frontend
        start_date_raw = service_call.data.get("start_date")
        end_date_raw = service_call.data.get("end_date")

        # Parse datetime strings from frontend and NORMALIZE to aware UTC. The
        # frontend may send a naive datetime-local (YYYY-MM-DDTHH:MM) OR an
        # offset-carrying ISO string; either way we store an aware UTC value so
        # every later comparison against ``dt_util``/UTC ``now`` is aware-vs-aware
        # (a naive-vs-aware compare raised TypeError inside the coordinator and
        # aborted the whole sync cycle). A naive input is assumed to be in HA's
        # configured time zone (``dt_util.as_utc`` semantics).
        start_date = _parse_ingested_datetime("start_date", start_date_raw)
        end_date = _parse_ingested_datetime("end_date", end_date_raw)
        allowed_hours = service_call.data.get("allowed_hours")
        allowed_days = service_call.data.get("allowed_days")
        max_uses = service_call.data.get("max_uses", -1)
        notify_on_use = service_call.data.get("notify_on_use", False)

        _LOGGER.info(
            "Set advanced code for entity %s, slot %s with user %s",
            entity_id,
            code_slot,
            user_name,
        )

        # Find the lock object for this entity_id
        result = find_lock(hass, entity_id)
        if result is None:
            _LOGGER.error("No lock found for entity_id: %s", entity_id)
            return
        lock, entry_id, _entry_data = result
        # Metadata-only fast path: if the incoming PIN matches the PIN
        # already stored for this slot, the caller only edited the
        # username/scheduling metadata. Update those fields in place
        # WITHOUT marking the slot unsynced — this avoids a spurious
        # Z-Wave re-write (which can surface transient Kwikset errors)
        # even if a caller redundantly re-sends the existing PIN.
        existing_slot = lock.code_slots.get(code_slot)
        if (
            existing_slot is not None
            and existing_slot.pin_code
            and user_code
            and existing_slot.pin_code == user_code
        ):
            _LOGGER.info(
                "Metadata-only update for slot %s in lock %s "
                "(PIN unchanged) — skipping Z-Wave re-write",
                code_slot,
                lock.lock_name,
            )
            existing_slot.user_name = user_name
            existing_slot.start_date = start_date
            existing_slot.end_date = end_date
            existing_slot.allowed_hours = allowed_hours
            existing_slot.allowed_days = allowed_days
            existing_slot.max_uses = max_uses
            existing_slot.notify_on_use = notify_on_use
            # Deliberately do NOT touch is_synced — the physical lock
            # already holds this PIN, so no re-sync is required.

            from ..storage.lock_storage import save_lock_data

            await save_lock_data(hass, lock, entry_id)

            # Zone model: a metadata-only edit on a zoned member must STILL be
            # propagated to the owning zone. The coordinator's per-cycle mirror
            # copies the ZONE's canonical slot back onto every member, so
            # without this the next mirror (within ~30s) would overwrite these
            # freshly-edited fields with the stale zone definition and silently
            # revert the change. Propagating updates the zone's canonical slot
            # (unchanged PIN, new schedule/limit/notify) so the mirror is a
            # no-op instead of a revert. No-op when the lock is unhomed.
            await _propagate_slot_to_zone(hass, lock, code_slot)
            return

        # Pre-flight: reject PIN-prefix collisions before any write
        _check_prefix_collision(lock, code_slot, user_code)

        success = lock.set_code(
            code_slot,
            user_code,
            user_name,
            start_date,
            end_date,
            allowed_hours,
            allowed_days,
            max_uses,
            notify_on_use,
        )
        if success:
            _LOGGER.info(
                "Successfully set advanced code for slot %s in lock %s",
                code_slot,
                lock.lock_name,
            )
            # Save slot data to persistent storage
            from ..storage.lock_storage import save_lock_data

            await save_lock_data(hass, lock, entry_id)

            # Zone model: propagate the change to the owning zone so it
            # reaches every member lock on the next coordinator cycle.
            await _propagate_slot_to_zone(hass, lock, code_slot)
        else:
            _LOGGER.error(
                "Failed to set advanced code for slot %s in lock %s",
                code_slot,
                lock.lock_name,
            )

    @staticmethod
    async def clear_code(hass: HomeAssistant, service_call: ServiceCall) -> None:
        """Clear a user code from both Smart Lock Manager and physical Z-Wave lock."""
        entity_id = service_call.data[ATTR_ENTITY_ID]
        code_slot = service_call.data[ATTR_CODE_SLOT]

        _LOGGER.debug("Clear code service called: slot %s", code_slot)

        # Find the lock object for this entity_id
        result = find_lock(hass, entity_id)
        if result is None:
            _LOGGER.error("No lock found for entity_id: %s", entity_id)
            return
        lock, entry_id, _entry_data = result

        slot = lock.code_slots.get(code_slot)
        if slot is None:
            _LOGGER.error(
                "Cannot clear slot %s in lock %s: no such slot",
                code_slot,
                lock.lock_name,
            )
            return

        from ..storage.lock_storage import save_lock_data

        # Physical clear FIRST. Never null the in-memory code until the deadbolt
        # confirms it is gone: a silent no-op / supervision failure must leave
        # the slot marked unsynced WITH the PIN retained, so the coordinator's
        # remove path retries it — not a green empty slot with a live code still
        # accepted by the lock. Mock-aware: under SLM_DEV_MOCK this clears the
        # in-memory MockValueDB (and honors injected failures).
        try:
            from .zwave_services import _clear_usercode

            await _clear_usercode(hass, entity_id, code_slot)
        except Exception as e:
            lock.mark_slot_removal_failed(code_slot, f"Physical clear failed: {e}")
            await save_lock_data(hass, lock, entry_id)
            _LOGGER.error(
                "Physical clear FAILED for slot %s on %s; PIN retained and slot "
                "left unsynced for the coordinator to retry: %s",
                code_slot,
                entity_id,
                e,
            )
            return

        # Physical clear confirmed — finalize the in-memory wipe.
        lock.clear_code(code_slot)
        _LOGGER.info(
            "Cleared slot %s from Smart Lock Manager and Z-Wave lock %s",
            code_slot,
            lock.lock_name,
        )

        # Save changes to storage
        await save_lock_data(hass, lock, entry_id)

        # Zone model: propagate the clear to the owning zone so it
        # reaches every member lock on the next coordinator cycle.
        await _propagate_slot_to_zone(hass, lock, code_slot)
