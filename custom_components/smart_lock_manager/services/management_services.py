"""Lock management and analytics services for Smart Lock Manager."""

import logging
from typing import Optional

from homeassistant.core import HomeAssistant, ServiceCall

from ..const import ATTR_ENTITY_ID, DOMAIN, PRIMARY_LOCK

_LOGGER = logging.getLogger(__name__)


class ManagementServices:
    """Service handler for lock management and analytics operations."""

    @staticmethod
    async def sync_child_locks(hass: HomeAssistant, service_call: ServiceCall) -> None:
        """Sync codes from a main lock to its child locks."""
        entity_id = service_call.data[ATTR_ENTITY_ID]

        # Find the main lock
        main_lock = None
        for entry_id, entry_data in hass.data[DOMAIN].items():
            if isinstance(entry_data, dict):  # Skip global_settings
                lock = entry_data.get(PRIMARY_LOCK)
                if lock and lock.lock_entity_id == entity_id:
                    main_lock = lock
                    break

        if not main_lock:
            _LOGGER.error("No lock found for entity_id: %s", entity_id)
            return

        if not main_lock.is_main_lock:
            _LOGGER.error(
                "Lock %s is not configured as a main lock", main_lock.lock_name
            )
            return

        if not main_lock.child_lock_ids:
            _LOGGER.info(
                "No child locks configured for main lock %s", main_lock.lock_name
            )
            return

        # Find child locks
        child_locks = []
        for entry_id, entry_data in hass.data[DOMAIN].items():
            if isinstance(entry_data, dict):  # Skip global_settings
                lock = entry_data.get(PRIMARY_LOCK)
                if lock and lock.lock_entity_id in main_lock.child_lock_ids:
                    child_locks.append(lock)

        if not child_locks:
            _LOGGER.error("No child locks found for main lock %s", main_lock.lock_name)
            return

        # Sync codes to child locks
        synced_count = 0
        for child_lock in child_locks:
            try:
                main_lock.sync_to_child_locks([child_lock])
                synced_count += 1
                _LOGGER.info(
                    "Synced codes from %s to child lock %s",
                    main_lock.lock_name,
                    child_lock.lock_name,
                )

                # Save child lock data
                for entry_id, entry_data in hass.data[DOMAIN].items():
                    if (
                        isinstance(entry_data, dict)
                        and entry_data.get(PRIMARY_LOCK) == child_lock
                    ):
                        store = entry_data.get("store")
                        if store:
                            await store.async_save(child_lock.to_dict())
                        break

            except Exception as e:
                _LOGGER.error(
                    "Failed to sync codes to child lock %s: %s", child_lock.lock_name, e
                )

        _LOGGER.info(
            "Completed sync from main lock %s to %s child locks",
            main_lock.lock_name,
            synced_count,
        )

    @staticmethod
    async def get_usage_stats(hass: HomeAssistant, service_call: ServiceCall) -> None:
        """Get comprehensive usage statistics for a lock."""
        entity_id = service_call.data[ATTR_ENTITY_ID]

        for entry_id, entry_data in hass.data[DOMAIN].items():
            if isinstance(entry_data, dict):  # Skip global_settings
                lock = entry_data.get(PRIMARY_LOCK)
                if lock and lock.lock_entity_id == entity_id:
                    stats = lock.get_usage_statistics()

                    # Fire event with detailed statistics
                    hass.bus.async_fire(
                        "smart_lock_manager_usage_stats",
                        {
                            "entity_id": entity_id,
                            "lock_name": lock.lock_name,
                            "statistics": stats,
                            "slot_details": {
                                f"slot_{slot_num}": {
                                    "user_name": slot.user_name,
                                    "use_count": slot.use_count,
                                    "max_uses": slot.max_uses,
                                    "last_used": (
                                        slot.last_used.isoformat()
                                        if slot.last_used
                                        else None
                                    ),
                                    "created_at": (
                                        slot.created_at.isoformat()
                                        if slot.created_at
                                        else None
                                    ),
                                    "is_active": slot.is_active,
                                    "usage_percentage": (
                                        (slot.use_count / slot.max_uses * 100)
                                        if slot.max_uses > 0
                                        else 0
                                    ),
                                }
                                for slot_num, slot in lock.code_slots.items()
                                if slot.is_active
                            },
                        },
                    )

                    _LOGGER.info(
                        "Usage statistics generated for lock %s: %s total uses across %s active users",
                        lock.lock_name,
                        stats.get("total_uses", 0),
                        stats.get("active_users", 0),
                    )
                    return

        _LOGGER.error("No lock found for entity_id: %s", entity_id)

    @staticmethod
    async def update_lock_settings(
        hass: HomeAssistant, service_call: ServiceCall
    ) -> None:
        """Update lock configuration settings."""
        entity_id = service_call.data[ATTR_ENTITY_ID]
        friendly_name = service_call.data.get("friendly_name")
        slot_count = service_call.data.get("slot_count")

        for entry_id, entry_data in hass.data[DOMAIN].items():
            if isinstance(entry_data, dict):  # Skip global_settings
                lock = entry_data.get(PRIMARY_LOCK)
                if lock and lock.lock_entity_id == entity_id:
                    updated = False

                    # Update friendly name if provided
                    if friendly_name and friendly_name != lock.settings.friendly_name:
                        lock.settings.friendly_name = friendly_name
                        updated = True
                        _LOGGER.info(
                            "Updated friendly name for lock %s to: %s",
                            lock.lock_name,
                            friendly_name,
                        )

                    # Update slot count if provided
                    if slot_count and slot_count != lock.slots:
                        if 1 <= slot_count <= 50:
                            old_count = lock.slots
                            success = lock.resize_slots(slot_count)
                            if success:
                                updated = True
                                _LOGGER.info(
                                    "Updated slot count for lock %s from %s to %s",
                                    lock.lock_name,
                                    old_count,
                                    slot_count,
                                )
                            else:
                                _LOGGER.error(
                                    "Failed to resize slots for lock %s", lock.lock_name
                                )
                        else:
                            _LOGGER.error(
                                "Invalid slot count %s (must be 1-50)", slot_count
                            )

                    # Save changes if any updates were made
                    if updated:
                        store = entry_data.get("store")
                        if store:
                            await store.async_save(lock.to_dict())

                        # Fire event to notify about settings change
                        hass.bus.async_fire(
                            "smart_lock_manager_settings_updated",
                            {
                                "entity_id": entity_id,
                                "lock_name": lock.lock_name,
                                "friendly_name": lock.settings.friendly_name,
                                "slot_count": lock.slots,
                            },
                        )
                    else:
                        _LOGGER.info(
                            "No changes made to lock %s settings", lock.lock_name
                        )

                    return

        _LOGGER.error("No lock found for entity_id: %s", entity_id)
