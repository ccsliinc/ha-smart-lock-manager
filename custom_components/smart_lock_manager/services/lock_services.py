"""Core lock code management services."""

import logging
from datetime import datetime
from typing import Optional

from homeassistant.core import HomeAssistant, ServiceCall

from ..const import (
    ATTR_CODE_SLOT,
    ATTR_CODE_SLOT_NAME,
    ATTR_ENTITY_ID,
    ATTR_USER_CODE,
    DOMAIN,
    PRIMARY_LOCK,
)
from ..models.lock import SmartLockManagerLock

_LOGGER = logging.getLogger(__name__)


class LockServices:
    """Service handler for basic lock operations."""

    @staticmethod
    async def set_code(hass: HomeAssistant, service_call: ServiceCall) -> None:
        """Set a user code (basic version)."""
        entity_id = service_call.data[ATTR_ENTITY_ID]
        code_slot = service_call.data[ATTR_CODE_SLOT]
        user_code = service_call.data[ATTR_USER_CODE]
        user_name = service_call.data.get(ATTR_CODE_SLOT_NAME)

        _LOGGER.info(
            "🔄 SET_CODE DEBUG - Set code service called: slot %s, user %s",
            code_slot,
            user_name,
        )

        # Find the lock object for this entity_id
        for entry_id, entry_data in hass.data[DOMAIN].items():
            if isinstance(entry_data, dict):  # Skip global_settings
                lock = entry_data.get(PRIMARY_LOCK)
                if lock and lock.lock_entity_id == entity_id:
                    _LOGGER.info(
                        "🔄 SET_CODE DEBUG - Found lock %s, setting slot %s",
                        lock.lock_name,
                        code_slot,
                    )

                    success = lock.set_code(code_slot, user_code, user_name)
                    if success:
                        _LOGGER.info(
                            "🔄 SET_CODE DEBUG - Successfully set code for slot %s in lock %s",
                            code_slot,
                            lock.lock_name,
                        )

                        # Save changes to storage
                        from .. import _save_lock_data

                        await _save_lock_data(hass, lock, entry_id)
                        _LOGGER.info("🔄 SET_CODE DEBUG - Saved slot data to storage")

                    else:
                        _LOGGER.error(
                            "🔄 SET_CODE DEBUG - Failed to set code for slot %s in lock %s",
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
        start_date = service_call.data.get("start_date")
        end_date = service_call.data.get("end_date")
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
            lock = entry_data.get(PRIMARY_LOCK)
            if lock and lock.lock_entity_id == entity_id:
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

        _LOGGER.info("Clear code service called: slot %s", code_slot)

        # Find the lock object for this entity_id
        for entry_id, entry_data in hass.data[DOMAIN].items():
            lock = entry_data.get(PRIMARY_LOCK)
            if lock and lock.lock_entity_id == entity_id:
                # Clear from Smart Lock Manager storage
                success = lock.clear_code(code_slot)
                if success:
                    _LOGGER.info(
                        "Successfully cleared code for slot %s in Smart Lock Manager %s",
                        code_slot,
                        lock.lock_name,
                    )

                    # Also clear from physical Z-Wave lock
                    try:
                        await hass.services.async_call(
                            "zwave_js",
                            "clear_lock_usercode",
                            {"entity_id": entity_id, "code_slot": code_slot},
                            blocking=True,
                        )
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

                else:
                    _LOGGER.error(
                        "Failed to clear code for slot %s in lock %s",
                        code_slot,
                        lock.lock_name,
                    )
                return

        _LOGGER.error("No lock found for entity_id: %s", entity_id)
