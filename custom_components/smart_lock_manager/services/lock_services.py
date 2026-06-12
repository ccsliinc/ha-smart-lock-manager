"""Core lock code management services."""

import logging
from datetime import datetime
from typing import Optional

from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError

from ..const import (
    ATTR_CODE_SLOT,
    ATTR_CODE_SLOT_NAME,
    ATTR_ENTITY_ID,
    ATTR_USER_CODE,
    DOMAIN,
    PRIMARY_LOCK,
)
from ..models.lock import SmartLockManagerLock


def _check_prefix_collision(
    lock: SmartLockManagerLock, code_slot: int, user_code: Optional[str]
) -> None:
    """Raise HomeAssistantError if ``user_code`` collides on the lock's prefix.

    Kwikset Z-Wave deadbolts silently drop user-code writes that share their
    first N digits (default 4) with an existing code. Reject early with a
    clear, user-facing error and tag the slot's ``sync_error`` for the UI.
    PIN values are NEVER included in the error message — only the prefix.
    """
    if not user_code:
        return
    conflict = lock.find_prefix_conflict(user_code, code_slot)
    if conflict is None:
        return

    prefix_len = lock.code_collision_prefix_length
    prefix = user_code[:prefix_len]
    message = (
        f"Cannot set slot {code_slot}: PIN starts with same {prefix_len} digits "
        f"({prefix}) as slot {conflict.slot_number} "
        f"({conflict.user_name or 'unnamed'}). Kwikset locks silently reject "
        f"such writes — pick a PIN with a different first {prefix_len} digits."
    )

    target_slot = lock.code_slots.get(code_slot)
    if target_slot is not None:
        target_slot.sync_error = message
        target_slot.validation_rejections += 1
        target_slot.is_synced = False

    _LOGGER.error(
        "Prefix-collision rejection on %s: slot %s vs slot %s (prefix=%s)",
        lock.lock_name,
        code_slot,
        conflict.slot_number,
        prefix,
    )
    raise HomeAssistantError(message)


_LOGGER = logging.getLogger(__name__)


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
        for entry_id, entry_data in hass.data[DOMAIN].items():
            if isinstance(entry_data, dict):  # Skip global_settings
                lock = entry_data.get(PRIMARY_LOCK)
                if lock and lock.lock_entity_id == entity_id:
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
                            "🔄 SET_CODE DEBUG - Failed to set code for "
                            "slot %s in lock %s",
                            code_slot,
                            lock.lock_name,
                        )
                    return

        _LOGGER.error("🔄 SET_CODE DEBUG - No lock found for entity_id: %s", entity_id)

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

        # Parse datetime strings from frontend (ISO format)
        start_date = None
        if start_date_raw:
            try:
                # Handle datetime-local format from frontend (YYYY-MM-DDTHH:MM)
                start_date = datetime.fromisoformat(str(start_date_raw))
                _LOGGER.debug("Parsed start_date: %s", start_date)
            except (ValueError, TypeError) as e:
                _LOGGER.warning(
                    "Invalid start_date format: %s, error: %s", start_date_raw, e
                )

        end_date = None
        if end_date_raw:
            try:
                # Handle datetime-local format from frontend (YYYY-MM-DDTHH:MM)
                end_date = datetime.fromisoformat(str(end_date_raw))
                _LOGGER.debug("Parsed end_date: %s", end_date)
            except (ValueError, TypeError) as e:
                _LOGGER.warning(
                    "Invalid end_date format: %s, error: %s", end_date_raw, e
                )
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
        for entry_id, entry_data in hass.data[DOMAIN].items():
            # Skip non-dict entries (e.g. stray runtime callbacks) so a polluted
            # registry can never crash this loop with an AttributeError.
            if not isinstance(entry_data, dict):
                continue
            lock = entry_data.get(PRIMARY_LOCK)
            if lock and lock.lock_entity_id == entity_id:
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

                    # Propagate metadata to child locks if applicable.
                    if lock.is_main_lock and lock.child_lock_ids:
                        try:
                            from ..const import SERVICE_SYNC_CHILD_LOCKS

                            await hass.services.async_call(
                                DOMAIN,
                                SERVICE_SYNC_CHILD_LOCKS,
                                {ATTR_ENTITY_ID: lock.lock_entity_id},
                            )
                        except Exception as e:
                            _LOGGER.error(
                                "Failed child sync after metadata-only update "
                                "for %s: %s",
                                lock.lock_name,
                                e,
                            )
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
                return

        _LOGGER.error("No lock found for entity_id: %s", entity_id)

    @staticmethod
    async def clear_code(hass: HomeAssistant, service_call: ServiceCall) -> None:
        """Clear a user code from both Smart Lock Manager and physical Z-Wave lock."""
        entity_id = service_call.data[ATTR_ENTITY_ID]
        code_slot = service_call.data[ATTR_CODE_SLOT]

        _LOGGER.debug("Clear code service called: slot %s", code_slot)

        # Find the lock object for this entity_id
        for entry_id, entry_data in hass.data[DOMAIN].items():
            # Skip non-dict entries so a polluted registry can never crash here.
            if not isinstance(entry_data, dict):
                continue
            lock = entry_data.get(PRIMARY_LOCK)
            if lock and lock.lock_entity_id == entity_id:
                # Clear from Smart Lock Manager storage
                success = lock.clear_code(code_slot)
                if success:
                    _LOGGER.info(
                        "Successfully cleared code for slot %s in Smart Lock "
                        "Manager %s",
                        code_slot,
                        lock.lock_name,
                    )

                    # Also clear from physical Z-Wave lock (mock-aware: under
                    # SLM_DEV_MOCK this clears the in-memory MockValueDB).
                    try:
                        from .zwave_services import _clear_usercode

                        await _clear_usercode(hass, entity_id, code_slot)
                        _LOGGER.info(
                            "Successfully cleared code from Z-Wave lock %s slot %s",
                            entity_id,
                            code_slot,
                        )
                    except Exception as e:
                        _LOGGER.warning(
                            "Failed to clear Z-Wave lock code for slot %s: %s",
                            code_slot,
                            e,
                        )

                    # Save changes to storage
                    from ..storage.lock_storage import save_lock_data

                    await save_lock_data(hass, lock, entry_id)

                    # Zone model: propagate the clear to the owning zone so it
                    # reaches every member lock on the next coordinator cycle.
                    await _propagate_slot_to_zone(hass, lock, code_slot)

                else:
                    _LOGGER.error(
                        "Failed to clear code for slot %s in lock %s",
                        code_slot,
                        lock.lock_name,
                    )
                return

        _LOGGER.error("No lock found for entity_id: %s", entity_id)
