"""Slot management services for Smart Lock Manager."""

import logging

from homeassistant.core import HomeAssistant, ServiceCall

from ..const import (
    ATTR_CODE_SLOT,
    ATTR_ENTITY_ID,
    ATTR_SLOT_COUNT,
    DOMAIN,
    PRIMARY_LOCK,
)

_LOGGER = logging.getLogger(__name__)


class SlotServices:
    """Service handler for slot management operations."""

    @staticmethod
    async def enable_slot(hass: HomeAssistant, service_call: ServiceCall) -> None:
        """Enable a code slot."""
        entity_id = service_call.data[ATTR_ENTITY_ID]
        code_slot = service_call.data[ATTR_CODE_SLOT]

        for entry_id, entry_data in hass.data[DOMAIN].items():
            if isinstance(entry_data, dict):  # Skip global_settings
                lock = entry_data.get(PRIMARY_LOCK)
                if lock and lock.lock_entity_id == entity_id:
                    success = lock.enable_slot(code_slot)
                    if success:
                        _LOGGER.info(
                            "Enabled slot %s in lock %s", code_slot, lock.lock_name
                        )

                        # Save changes to storage
                        from .. import _save_lock_data

                        await _save_lock_data(hass, lock, entry_id)

                    else:
                        _LOGGER.error(
                            "Failed to enable slot %s in lock %s (no PIN code?)",
                            code_slot,
                            lock.lock_name,
                        )
                    return

        _LOGGER.error("No lock found for entity_id: %s", entity_id)

    @staticmethod
    async def disable_slot(hass: HomeAssistant, service_call: ServiceCall) -> None:
        """Disable a code slot."""
        entity_id = service_call.data[ATTR_ENTITY_ID]
        code_slot = service_call.data[ATTR_CODE_SLOT]

        _LOGGER.info(
            "🔄 DISABLE_SLOT DEBUG - Called with entity_id=%s, code_slot=%s",
            entity_id,
            code_slot,
        )

        for entry_id, entry_data in hass.data[DOMAIN].items():
            if isinstance(entry_data, dict):  # Skip global_settings
                lock = entry_data.get(PRIMARY_LOCK)
                if lock and lock.lock_entity_id == entity_id:
                    _LOGGER.info(
                        "🔄 DISABLE_SLOT DEBUG - Found lock %s, checking slot %s",
                        lock.lock_name,
                        code_slot,
                    )

                    # Check slot state before disabling
                    slot = lock.code_slots.get(code_slot)
                    if slot:
                        _LOGGER.info(
                            "🔄 DISABLE_SLOT DEBUG - Slot %s BEFORE: is_active=%s, pin_code=%s, user_name=%s",
                            code_slot,
                            slot.is_active,
                            bool(slot.pin_code),
                            slot.user_name,
                        )

                    success = lock.disable_slot(code_slot)

                    # Check slot state after disabling
                    if slot:
                        _LOGGER.info(
                            "🔄 DISABLE_SLOT DEBUG - Slot %s AFTER: is_active=%s, pin_code=%s, user_name=%s",
                            code_slot,
                            slot.is_active,
                            bool(slot.pin_code),
                            slot.user_name,
                        )

                    if success:
                        _LOGGER.info(
                            "🔄 DISABLE_SLOT DEBUG - Successfully disabled slot %s in lock %s",
                            code_slot,
                            lock.lock_name,
                        )

                        # Save changes to storage
                        from .. import _save_lock_data

                        await _save_lock_data(hass, lock, entry_id)
                        _LOGGER.info(
                            "🔄 DISABLE_SLOT DEBUG - Saved slot data to storage"
                        )

                    else:
                        _LOGGER.error(
                            "🔄 DISABLE_SLOT DEBUG - Failed to disable slot %s in lock %s",
                            code_slot,
                            lock.lock_name,
                        )
                    return

        _LOGGER.error(
            "🔄 DISABLE_SLOT DEBUG - No lock found for entity_id: %s", entity_id
        )

    @staticmethod
    async def reset_slot_usage(hass: HomeAssistant, service_call: ServiceCall) -> None:
        """Reset usage counter for a code slot."""
        entity_id = service_call.data[ATTR_ENTITY_ID]
        code_slot = service_call.data[ATTR_CODE_SLOT]

        for entry_id, entry_data in hass.data[DOMAIN].items():
            if isinstance(entry_data, dict):  # Skip global_settings
                lock = entry_data.get(PRIMARY_LOCK)
                if lock and lock.lock_entity_id == entity_id:
                    success = lock.reset_slot_usage(code_slot)
                    if success:
                        _LOGGER.info(
                            "Reset usage counter for slot %s in lock %s",
                            code_slot,
                            lock.lock_name,
                        )
                    else:
                        _LOGGER.error(
                            "Failed to reset usage counter for slot %s in lock %s",
                            code_slot,
                            lock.lock_name,
                        )
                    return

        _LOGGER.error("No lock found for entity_id: %s", entity_id)

    @staticmethod
    async def resize_slots(hass: HomeAssistant, service_call: ServiceCall) -> None:
        """Resize the number of available code slots."""
        entity_id = service_call.data[ATTR_ENTITY_ID]
        slot_count = service_call.data[ATTR_SLOT_COUNT]

        for entry_id, entry_data in hass.data[DOMAIN].items():
            if isinstance(entry_data, dict):  # Skip global_settings
                lock = entry_data.get(PRIMARY_LOCK)
                if lock and lock.lock_entity_id == entity_id:
                    # Validate slot count
                    if slot_count < 1 or slot_count > 50:
                        _LOGGER.error(
                            "Invalid slot count %s (must be 1-50)", slot_count
                        )
                        return

                    old_count = lock.slots
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
                        _LOGGER.error(
                            "Failed to resize slots for lock %s", lock.lock_name
                        )
                    return

        _LOGGER.error("No lock found for entity_id: %s", entity_id)
