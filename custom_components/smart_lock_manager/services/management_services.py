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
        is_main_lock = service_call.data.get("is_main_lock")
        parent_lock_id = service_call.data.get("parent_lock_id")

        _LOGGER.info("=== BACKEND DEBUGGING: update_lock_settings called ===")
        _LOGGER.info(f"🔧 Backend Debug - Service call data: {service_call.data}")
        _LOGGER.info(f"🔧 Backend Debug - Entity ID: {entity_id}")
        _LOGGER.info(f"🔧 Backend Debug - Friendly name: '{friendly_name}'")
        _LOGGER.info(f"🔧 Backend Debug - Slot count: {slot_count}")
        _LOGGER.info(f"🔧 Backend Debug - Is main lock: {is_main_lock}")
        _LOGGER.info(f"🔧 Backend Debug - Parent lock ID: {parent_lock_id}")

        for entry_id, entry_data in hass.data[DOMAIN].items():
            if isinstance(entry_data, dict):  # Skip global_settings
                lock = entry_data.get(PRIMARY_LOCK)
                _LOGGER.info(
                    f"🔍 Backend Debug - Checking entry {entry_id}: lock={lock}, entity_id={lock.lock_entity_id if lock else 'None'}"
                )

                if lock and lock.lock_entity_id == entity_id:
                    _LOGGER.info(
                        f"🎯 Backend Debug - Found matching lock: {lock.lock_name}"
                    )
                    _LOGGER.info(f"🔍 Backend Debug - Current lock state:")
                    _LOGGER.info(
                        f"  - Current friendly name: '{lock.settings.friendly_name}'"
                    )
                    _LOGGER.info(f"  - Current slot count: {lock.slots}")
                    _LOGGER.info(f"  - Current is_main_lock: {lock.is_main_lock}")
                    _LOGGER.info(f"  - Current parent_lock_id: {lock.parent_lock_id}")

                    updated = False

                    # Update friendly name if provided
                    if friendly_name and friendly_name != lock.settings.friendly_name:
                        _LOGGER.info(
                            f"✅ Backend Debug - Friendly name will be updated from '{lock.settings.friendly_name}' to '{friendly_name}'"
                        )
                        lock.settings.friendly_name = friendly_name
                        updated = True
                        _LOGGER.info(
                            "Updated friendly name for lock %s to: %s",
                            lock.lock_name,
                            friendly_name,
                        )
                    else:
                        _LOGGER.info(f"❌ Backend Debug - Friendly name NOT updated:")
                        _LOGGER.info(
                            f"  - Provided friendly_name: '{friendly_name}' (truthy: {bool(friendly_name)})"
                        )
                        _LOGGER.info(
                            f"  - Current friendly_name: '{lock.settings.friendly_name}'"
                        )
                        _LOGGER.info(
                            f"  - Are they equal? {friendly_name == lock.settings.friendly_name}"
                        )
                        _LOGGER.info(
                            f"  - Condition result: friendly_name={bool(friendly_name)}, different={friendly_name != lock.settings.friendly_name}"
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

                    # Update parent/child lock settings if provided
                    if is_main_lock is not None and is_main_lock != lock.is_main_lock:
                        lock.is_main_lock = is_main_lock
                        updated = True
                        _LOGGER.info(
                            "Updated lock %s type to: %s",
                            lock.lock_name,
                            "Parent Lock" if is_main_lock else "Child Lock",
                        )

                        # If converting to child lock, clear child lock IDs
                        if not is_main_lock:
                            lock.child_lock_ids = []

                    # Update parent lock ID if provided (for child locks)
                    if (
                        parent_lock_id is not None
                        and parent_lock_id != lock.parent_lock_id
                    ):
                        old_parent = lock.parent_lock_id
                        lock.parent_lock_id = parent_lock_id if parent_lock_id else None
                        updated = True

                        # Update child lock lists on old and new parent locks
                        await ManagementServices._update_parent_child_relationships(
                            hass, entity_id, old_parent, parent_lock_id
                        )

                        _LOGGER.info(
                            "Updated parent lock for %s from %s to %s",
                            lock.lock_name,
                            old_parent or "None",
                            parent_lock_id or "None",
                        )

                    # Save changes if any updates were made
                    if updated:
                        _LOGGER.info(f"💾 Backend Debug - Saving changes to storage")
                        store = entry_data.get("store")
                        if store:
                            lock_dict = lock.to_dict()
                            _LOGGER.info(
                                f"💾 Backend Debug - Saving lock data: friendly_name='{lock_dict.get('settings', {}).get('friendly_name')}'"
                            )
                            await store.async_save(lock_dict)
                            _LOGGER.info(f"💾 Backend Debug - Storage save completed")

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
                        _LOGGER.info(
                            f"🔥 Backend Debug - Fired settings_updated event for {lock.lock_name}"
                        )

                        # Trigger coordinator refresh to update sensor attributes
                        coordinator = entry_data.get("coordinator")
                        if coordinator:
                            await coordinator.async_request_refresh()
                            _LOGGER.debug(
                                "Triggered coordinator refresh after settings update"
                            )

                        # Update all sensor entities that reference this lock by triggering coordinator refresh
                        # This ensures all sensors using this lock get the updated object reference

                        # Force immediate coordinator update (this will refresh all sensors using this lock)
                        coordinator = entry_data.get("coordinator")
                        if coordinator:
                            # Force immediate refresh
                            await coordinator.async_request_refresh()
                            # Wait a moment for the refresh to complete, then force state update
                            import asyncio

                            await asyncio.sleep(0.1)

                        # Force coordinator refresh which should update all sensors
                        coordinator = entry_data.get("coordinator")
                        if coordinator:
                            # Force multiple refreshes to ensure state propagation
                            await coordinator.async_request_refresh()
                            await asyncio.sleep(0.2)
                            await coordinator.async_refresh()
                            _LOGGER.info(
                                f"Forced coordinator refresh for friendly name update"
                            )

                        # Fire event to notify frontend about the change
                        hass.bus.async_fire(
                            "smart_lock_manager_friendly_name_updated",
                            {
                                "entity_id": entity_id,
                                "lock_name": lock.lock_name,
                                "friendly_name": lock.settings.friendly_name,
                            },
                        )

                        _LOGGER.info(
                            f"Updated lock settings for {lock.lock_name}, friendly_name: {lock.settings.friendly_name}"
                        )
                    else:
                        _LOGGER.info(
                            "No changes made to lock %s settings", lock.lock_name
                        )

                    return

        _LOGGER.error("No lock found for entity_id: %s", entity_id)

    @staticmethod
    async def _update_parent_child_relationships(
        hass: HomeAssistant,
        child_entity_id: str,
        old_parent_id: Optional[str],
        new_parent_id: Optional[str],
    ) -> None:
        """Update parent-child lock relationships when parent changes."""

        # Remove child from old parent's child list
        if old_parent_id:
            for entry_id, entry_data in hass.data[DOMAIN].items():
                if isinstance(entry_data, dict):
                    old_parent_lock = entry_data.get(PRIMARY_LOCK)
                    if (
                        old_parent_lock
                        and old_parent_lock.lock_entity_id == old_parent_id
                    ):
                        if child_entity_id in old_parent_lock.child_lock_ids:
                            old_parent_lock.child_lock_ids.remove(child_entity_id)

                        # Save old parent lock
                        store = entry_data.get("store")
                        if store:
                            await store.async_save(old_parent_lock.to_dict())
                        break

        # Add child to new parent's child list
        if new_parent_id:
            for entry_id, entry_data in hass.data[DOMAIN].items():
                if isinstance(entry_data, dict):
                    new_parent_lock = entry_data.get(PRIMARY_LOCK)
                    if (
                        new_parent_lock
                        and new_parent_lock.lock_entity_id == new_parent_id
                    ):
                        if child_entity_id not in new_parent_lock.child_lock_ids:
                            new_parent_lock.child_lock_ids.append(child_entity_id)

                        # Save new parent lock
                        store = entry_data.get("store")
                        if store:
                            await store.async_save(new_parent_lock.to_dict())
                        break

    @staticmethod
    async def remove_child_lock(hass: HomeAssistant, service_call: ServiceCall) -> None:
        """Remove a child lock and convert it back to a main lock."""
        child_entity_id = service_call.data[ATTR_ENTITY_ID]

        _LOGGER.info(f"Removing child lock: {child_entity_id}")

        # Find the child lock
        child_lock = None
        child_entry_data = None
        for entry_id, entry_data in hass.data[DOMAIN].items():
            if isinstance(entry_data, dict):
                lock = entry_data.get(PRIMARY_LOCK)
                if lock and lock.lock_entity_id == child_entity_id:
                    child_lock = lock
                    child_entry_data = entry_data
                    break

        if not child_lock:
            _LOGGER.error(f"Child lock not found: {child_entity_id}")
            return

        # Get the current parent
        old_parent_id = child_lock.parent_lock_id

        if not old_parent_id:
            _LOGGER.warning(f"Lock {child_entity_id} is not a child lock")
            return

        # Convert child back to main lock
        child_lock.is_main_lock = True
        child_lock.parent_lock_id = None
        child_lock.child_lock_ids = []

        # Remove child from parent's child list
        if old_parent_id:
            for entry_id, entry_data in hass.data[DOMAIN].items():
                if isinstance(entry_data, dict):
                    parent_lock = entry_data.get(PRIMARY_LOCK)
                    if parent_lock and parent_lock.lock_entity_id == old_parent_id:
                        if child_entity_id in parent_lock.child_lock_ids:
                            parent_lock.child_lock_ids.remove(child_entity_id)
                            _LOGGER.info(
                                f"Removed {child_entity_id} from parent {old_parent_id} child list"
                            )

                        # Save parent lock
                        parent_store = entry_data.get("store")
                        if parent_store:
                            await parent_store.async_save(parent_lock.to_dict())
                        break

        # Save the now-independent child lock
        if child_entry_data:
            child_store = child_entry_data.get("store")
            if child_store:
                await child_store.async_save(child_lock.to_dict())

        # Fire event to notify about the change
        hass.bus.async_fire(
            "smart_lock_manager_child_removed",
            {
                "entity_id": child_entity_id,
                "former_parent": old_parent_id,
                "lock_name": child_lock.lock_name,
            },
        )

        # Trigger coordinator refresh for both locks
        if child_entry_data:
            coordinator = child_entry_data.get("coordinator")
            if coordinator:
                await coordinator.async_request_refresh()

        _LOGGER.info(
            f"Successfully removed child lock {child_entity_id} from parent {old_parent_id}"
        )

    @staticmethod
    async def clear_all_slots(hass: HomeAssistant, service_call: ServiceCall) -> None:
        """Clear all code slots for a lock."""
        entity_id = service_call.data[ATTR_ENTITY_ID]

        for entry_id, entry_data in hass.data[DOMAIN].items():
            if isinstance(entry_data, dict):  # Skip global_settings
                lock = entry_data.get(PRIMARY_LOCK)
                if lock and lock.lock_entity_id == entity_id:
                    cleared_slots = []

                    # Clear all configured slots
                    for slot_num, slot in lock.code_slots.items():
                        if slot.pin_code:  # Only clear slots that have codes
                            old_user_name = slot.user_name
                            slot.pin_code = None
                            slot.user_name = None
                            slot.is_active = False
                            slot.is_synced = False  # Mark for Z-Wave removal
                            slot.use_count = 0
                            slot.created_at = None
                            slot.expires_at = None
                            slot.last_used = None
                            slot.start_date = None
                            slot.end_date = None
                            slot.allowed_hours = None
                            slot.allowed_days = None
                            slot.max_uses = -1
                            slot.notify_on_use = False
                            cleared_slots.append((slot_num, old_user_name))

                    if cleared_slots:
                        # Save the updated lock data
                        from .. import _save_lock_data

                        await _save_lock_data(hass, lock, entry_id)

                        # Fire event to notify about the mass clear
                        hass.bus.async_fire(
                            "smart_lock_manager_all_slots_cleared",
                            {
                                "entity_id": entity_id,
                                "lock_name": lock.lock_name,
                                "cleared_slots": cleared_slots,
                                "total_cleared": len(cleared_slots),
                            },
                        )

                        _LOGGER.info(
                            "Cleared all %s slots for lock %s: %s",
                            len(cleared_slots),
                            lock.lock_name,
                            [f"Slot {num}: {name}" for num, name in cleared_slots],
                        )
                    else:
                        _LOGGER.info(
                            "No configured slots to clear for lock %s", lock.lock_name
                        )

                    return

        _LOGGER.error("No lock found for entity_id: %s", entity_id)
