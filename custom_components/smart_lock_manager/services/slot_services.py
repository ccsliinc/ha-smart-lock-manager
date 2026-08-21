"""Slot management services for Smart Lock Manager."""

import logging

from homeassistant.core import HomeAssistant, ServiceCall

from ..const import (
    ATTR_CODE_SLOT,
    ATTR_ENTITY_ID,
    ATTR_SLOT_COUNT,
    COORDINATOR,
)
from ..models.lock import SmartLockManagerLock
from .helpers import find_lock

_LOGGER = logging.getLogger(__name__)


async def clear_dropped_slots_before_resize(
    hass: HomeAssistant,
    lock: SmartLockManagerLock,
    entity_id: str,
    new_slot_count: int,
) -> bool:
    """Physically clear codes in slots a shrink would DELETE, before deletion.

    A shrink drops the ``CodeSlot`` objects for slots
    ``>= start_from + new_slot_count``. Any dropped slot still holding a
    ``pin_code`` would otherwise leave its PIN live on the deadbolt with no
    record of it. This clears each such slot from the hardware (mock-aware via
    ``_clear_usercode``) BEFORE the model deletes it, awaiting confirmation.

    - Inputs: hass (HomeAssistant), lock (the lock being resized), entity_id
      (str lock entity id), new_slot_count (int target slot count).
    - Outputs: True when it is safe to delete the dropped slots (nothing to
      clear, or every physical clear confirmed); False if ANY clear failed —
      in which case the caller MUST NOT shrink past the un-cleared slot, to
      avoid orphaning a live code.
    """
    if new_slot_count >= lock.slots:
        return True  # not shrinking; nothing to clear

    from .zwave_services import _clear_usercode

    threshold = lock.start_from + new_slot_count
    all_ok = True
    for slot_num in list(lock.code_slots):
        if slot_num < threshold:
            continue
        slot = lock.code_slots[slot_num]
        if not slot.pin_code:
            continue
        try:
            await _clear_usercode(hass, entity_id, slot_num)
        except Exception as e:
            all_ok = False
            slot.is_synced = False
            slot.sync_error = f"Failed to clear before resize: {e}"
            _LOGGER.error(
                "resize: physical clear FAILED for dropped slot %s on %s; "
                "aborting shrink to avoid orphaning the code: %s",
                slot_num,
                entity_id,
                e,
            )
    return all_ok


class SlotServices:
    """Service handler for slot management operations."""

    @staticmethod
    async def enable_slot(hass: HomeAssistant, service_call: ServiceCall) -> None:
        """Enable a code slot."""
        entity_id = service_call.data[ATTR_ENTITY_ID]
        code_slot = service_call.data[ATTR_CODE_SLOT]

        result = find_lock(hass, entity_id)
        if result is None:
            _LOGGER.error("No lock found for entity_id: %s", entity_id)
            return
        lock, entry_id, _entry_data = result
        success = lock.enable_slot(code_slot)
        if success:
            _LOGGER.info("Enabled slot %s in lock %s", code_slot, lock.lock_name)

            # Save changes to storage
            from .. import _save_lock_data

            await _save_lock_data(hass, lock, entry_id)

            # Zone model: propagate the change to the owning zone so
            # the coordinator mirror re-syncs it to every member.
            from .lock_services import _propagate_slot_to_zone

            await _propagate_slot_to_zone(hass, lock, code_slot)

        else:
            _LOGGER.error(
                "Failed to enable slot %s in lock %s (no PIN code?)",
                code_slot,
                lock.lock_name,
            )

    @staticmethod
    async def disable_slot(hass: HomeAssistant, service_call: ServiceCall) -> None:
        """Disable a code slot."""
        entity_id = service_call.data[ATTR_ENTITY_ID]
        code_slot = service_call.data[ATTR_CODE_SLOT]

        result = find_lock(hass, entity_id)
        if result is None:
            _LOGGER.error("No lock found for entity_id: %s", entity_id)
            return
        lock, entry_id, _entry_data = result
        success = lock.disable_slot(code_slot)
        if success:
            _LOGGER.info("Disabled slot %s in lock %s", code_slot, lock.lock_name)

            # Save changes to storage
            from .. import _save_lock_data

            await _save_lock_data(hass, lock, entry_id)

            # Zone model: propagate the change to the owning zone so
            # the coordinator mirror re-syncs it to every member.
            from .lock_services import _propagate_slot_to_zone

            await _propagate_slot_to_zone(hass, lock, code_slot)

        else:
            _LOGGER.error(
                "Failed to disable slot %s in lock %s",
                code_slot,
                lock.lock_name,
            )

    @staticmethod
    async def reset_slot_usage(hass: HomeAssistant, service_call: ServiceCall) -> None:
        """Reset usage counter for a code slot."""
        entity_id = service_call.data[ATTR_ENTITY_ID]
        code_slot = service_call.data[ATTR_CODE_SLOT]

        result = find_lock(hass, entity_id)
        if result is None:
            _LOGGER.error("No lock found for entity_id: %s", entity_id)
            return
        lock, entry_id, _entry_data = result
        success = lock.reset_slot_usage(code_slot)
        if success:
            _LOGGER.info(
                "Reset usage counter for slot %s in lock %s",
                code_slot,
                lock.lock_name,
            )

            # Save changes to storage
            from .. import _save_lock_data

            await _save_lock_data(hass, lock, entry_id)
        else:
            _LOGGER.error(
                "Failed to reset usage counter for slot %s in lock %s",
                code_slot,
                lock.lock_name,
            )

    @staticmethod
    async def reset_sync(hass: HomeAssistant, service_call: ServiceCall) -> None:
        """Reset sync state for a code slot, clearing error state for fresh sync."""
        entity_id = service_call.data[ATTR_ENTITY_ID]
        code_slot = service_call.data[ATTR_CODE_SLOT]

        result = find_lock(hass, entity_id)
        if result is None:
            _LOGGER.error("No lock found for entity_id: %s", entity_id)
            return
        lock, entry_id, entry_data = result
        slot = lock.code_slots.get(code_slot)
        if slot:
            slot.sync_attempts = 0
            slot.sync_error = None
            slot.is_synced = False
            slot.user_id_status = None  # Force re-read from Z-Wave

            _LOGGER.info(
                "Reset sync state for slot %s on %s",
                code_slot,
                entity_id,
            )

            # Save changes to storage
            from .. import _save_lock_data

            await _save_lock_data(hass, lock, entry_id)

            # Trigger coordinator refresh to start sync on next cycle
            coordinator = entry_data.get(COORDINATOR)
            if coordinator:
                await coordinator.async_request_refresh()
        else:
            _LOGGER.error(
                "Slot %s not found in lock %s",
                code_slot,
                lock.lock_name,
            )

    @staticmethod
    async def resize_slots(hass: HomeAssistant, service_call: ServiceCall) -> None:
        """Resize the number of available code slots."""
        entity_id = service_call.data[ATTR_ENTITY_ID]
        slot_count = service_call.data[ATTR_SLOT_COUNT]

        result = find_lock(hass, entity_id)
        if result is None:
            _LOGGER.error("No lock found for entity_id: %s", entity_id)
            return
        lock, _entry_id, entry_data = result
        # Validate slot count
        if slot_count < 1 or slot_count > 50:
            _LOGGER.error("Invalid slot count %s (must be 1-50)", slot_count)
            return

        old_count = lock.slots

        # Shrinking deletes the dropped CodeSlot objects; physically clear any
        # code they hold FIRST so a smaller slot count never orphans a live PIN.
        if slot_count < old_count:
            safe = await clear_dropped_slots_before_resize(
                hass, lock, entity_id, slot_count
            )
            if not safe:
                _LOGGER.error(
                    "Aborting resize of %s: could not clear all dropped codes "
                    "from the physical lock",
                    lock.lock_name,
                )
                # Persist the sync_error flags set on the un-cleared slots.
                store = entry_data.get("store")
                if store:
                    await store.async_save(lock.to_dict())
                return

        success = lock.resize_slots(slot_count)

        if success:
            _LOGGER.info(
                "Resized slots for lock %s from %s to %s",
                lock.lock_name,
                old_count,
                slot_count,
            )

            # Save data to storage
            store = entry_data.get("store")
            if store:
                await store.async_save(lock.to_dict())
        else:
            _LOGGER.error("Failed to resize slots for lock %s", lock.lock_name)
