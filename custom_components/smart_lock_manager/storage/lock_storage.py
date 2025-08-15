"""Persistent storage management for Smart Lock Manager."""

import logging
from typing import Any, Dict

from homeassistant.core import HomeAssistant

from ..const import DOMAIN
from ..models.lock import SmartLockManagerLock

_LOGGER = logging.getLogger(__name__)


async def save_lock_data(
    hass: HomeAssistant, lock: SmartLockManagerLock, entry_id: str
) -> None:
    """Save lock slot data to persistent storage."""
    try:
        store = hass.data[DOMAIN][entry_id]["store"]

        # Convert slot data to serializable format
        slot_data = {}
        for slot_num, slot in lock.code_slots.items():
            slot_data[str(slot_num)] = {
                "slot_number": slot.slot_number,
                "pin_code": slot.pin_code,
                "user_name": slot.user_name,
                "is_active": slot.is_active,
                "start_date": slot.start_date.isoformat() if slot.start_date else None,
                "end_date": slot.end_date.isoformat() if slot.end_date else None,
                "allowed_hours": slot.allowed_hours,
                "allowed_days": slot.allowed_days,
                "max_uses": slot.max_uses,
                "use_count": slot.use_count,
                "notify_on_use": slot.notify_on_use,
            }

        data_to_save = {
            "code_slots": slot_data,
            "lock_name": lock.lock_name,
            "lock_entity_id": lock.lock_entity_id,
        }

        await store.async_save(data_to_save)
        _LOGGER.debug("Saved slot data for %s", lock.lock_name)

    except Exception as e:
        _LOGGER.error("Failed to save slot data for %s: %s", lock.lock_name, e)


async def load_lock_data(hass: HomeAssistant, store) -> Dict[str, Any]:
    """Load lock data from persistent storage."""
    try:
        stored_data = await store.async_load() or {}
        _LOGGER.debug(
            "Loaded stored data: %s slots", len(stored_data.get("code_slots", {}))
        )
        return stored_data
    except Exception as e:
        _LOGGER.error("Failed to load lock data: %s", e)
        return {}
