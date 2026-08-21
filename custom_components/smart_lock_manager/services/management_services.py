"""Lock management and analytics services for Smart Lock Manager."""

import logging

from homeassistant.core import HomeAssistant, ServiceCall

from ..const import ATTR_ENTITY_ID, DOMAIN, PRIMARY_LOCK
from .helpers import find_lock

_LOGGER = logging.getLogger(__name__)


class ManagementServices:
    """Service handler for lock management and analytics operations."""

    @staticmethod
    async def get_usage_stats(hass: HomeAssistant, service_call: ServiceCall) -> None:
        """Get comprehensive usage statistics for a lock."""
        entity_id = service_call.data[ATTR_ENTITY_ID]

        result = find_lock(hass, entity_id)
        if result is None:
            _LOGGER.error("No lock found for entity_id: %s", entity_id)
            return
        lock = result[0]
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
                            slot.last_used.isoformat() if slot.last_used else None
                        ),
                        "created_at": (
                            slot.created_at.isoformat() if slot.created_at else None
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

        _LOGGER.debug(
            "Usage statistics generated for lock %s: %s total uses "
            "across %s active users",
            lock.lock_name,
            stats.get("total_uses", 0),
            stats.get("active_users", 0),
        )

    @staticmethod
    async def update_lock_settings(
        hass: HomeAssistant, service_call: ServiceCall
    ) -> None:
        """Update lock configuration settings."""
        entity_id = service_call.data[ATTR_ENTITY_ID]
        friendly_name = service_call.data.get("friendly_name")
        slot_count = service_call.data.get("slot_count")

        _LOGGER.debug("=== BACKEND DEBUGGING: update_lock_settings called ===")
        _LOGGER.debug(f"🔧 Backend Debug - Service call data: {service_call.data}")
        _LOGGER.debug(f"🔧 Backend Debug - Entity ID: {entity_id}")
        _LOGGER.debug(f"🔧 Backend Debug - Friendly name: '{friendly_name}'")
        _LOGGER.debug(f"🔧 Backend Debug - Slot count: {slot_count}")

        for entry_id, entry_data in hass.data[DOMAIN].items():
            if isinstance(entry_data, dict):  # Skip global_settings
                lock = entry_data.get(PRIMARY_LOCK)
                _LOGGER.debug(
                    "🔍 Backend Debug - Checking entry %s: lock=%s, entity_id=%s",
                    entry_id,
                    lock,
                    lock.lock_entity_id if lock else "None",
                )

                if lock and lock.lock_entity_id == entity_id:
                    _LOGGER.debug(
                        f"🎯 Backend Debug - Found matching lock: {lock.lock_name}"
                    )
                    _LOGGER.debug("🔍 Backend Debug - Current lock state:")
                    _LOGGER.debug(
                        f"  - Current friendly name: '{lock.settings.friendly_name}'"
                    )
                    _LOGGER.debug(f"  - Current slot count: {lock.slots}")

                    updated = False

                    # Update friendly name if provided
                    if friendly_name and friendly_name != lock.settings.friendly_name:
                        _LOGGER.debug(
                            "✅ Backend Debug - Friendly name will be updated "
                            "from '%s' to '%s'",
                            lock.settings.friendly_name,
                            friendly_name,
                        )
                        lock.settings.friendly_name = friendly_name
                        updated = True
                        _LOGGER.debug(
                            "Updated friendly name for lock %s to: %s",
                            lock.lock_name,
                            friendly_name,
                        )
                    else:
                        _LOGGER.debug("❌ Backend Debug - Friendly name NOT updated:")
                        _LOGGER.debug(
                            "  - Provided friendly_name: '%s' (truthy: %s)",
                            friendly_name,
                            bool(friendly_name),
                        )
                        _LOGGER.debug(
                            "  - Current friendly_name: '%s'",
                            lock.settings.friendly_name,
                        )
                        _LOGGER.debug(
                            "  - Are they equal? %s",
                            friendly_name == lock.settings.friendly_name,
                        )
                        _LOGGER.debug(
                            "  - Condition result: friendly_name=%s, different=%s",
                            bool(friendly_name),
                            friendly_name != lock.settings.friendly_name,
                        )

                    # Update slot count if provided
                    if slot_count and slot_count != lock.slots:
                        if 1 <= slot_count <= 50:
                            old_count = lock.slots
                            # Shrinking deletes the dropped CodeSlot objects;
                            # physically clear any code they hold FIRST so a
                            # smaller slot count never orphans a live PIN.
                            safe_to_resize = True
                            if slot_count < old_count:
                                from .slot_services import (
                                    clear_dropped_slots_before_resize,
                                )

                                safe_to_resize = (
                                    await clear_dropped_slots_before_resize(
                                        hass, lock, entity_id, slot_count
                                    )
                                )
                            if not safe_to_resize:
                                _LOGGER.error(
                                    "Aborting slot resize for %s: could not clear "
                                    "all dropped codes from the physical lock",
                                    lock.lock_name,
                                )
                            elif lock.resize_slots(slot_count):
                                updated = True
                                _LOGGER.debug(
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
                        _LOGGER.debug("💾 Backend Debug - Saving changes to storage")
                        store = entry_data.get("store")
                        if store:
                            lock_dict = lock.to_dict()
                            _LOGGER.debug(
                                "💾 Backend Debug - Saving lock data: "
                                "friendly_name='%s'",
                                lock_dict.get("settings", {}).get("friendly_name"),
                            )
                            await store.async_save(lock_dict)
                            _LOGGER.debug("💾 Backend Debug - Storage save completed")

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
                        _LOGGER.debug(
                            "🔥 Backend Debug - Fired settings_updated event for %s",
                            lock.lock_name,
                        )

                        # Trigger coordinator refresh to update sensor attributes
                        coordinator = entry_data.get("coordinator")
                        if coordinator:
                            await coordinator.async_request_refresh()
                            _LOGGER.debug(
                                "Triggered coordinator refresh after settings update"
                            )

                        # Refresh the coordinator so every sensor referencing this
                        # lock picks up the updated object reference.

                        # Force immediate coordinator update to refresh all sensors
                        coordinator = entry_data.get("coordinator")
                        if coordinator:
                            # Force immediate refresh
                            await coordinator.async_request_refresh()
                            # Wait for the refresh to complete, then force state update
                            import asyncio

                            await asyncio.sleep(0.1)

                        # Force coordinator refresh which should update all sensors
                        coordinator = entry_data.get("coordinator")
                        if coordinator:
                            # Force multiple refreshes to ensure state propagation
                            await coordinator.async_request_refresh()
                            await asyncio.sleep(0.2)
                            await coordinator.async_refresh()
                            _LOGGER.debug(
                                "Forced coordinator refresh for friendly name update"
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

                        _LOGGER.debug(
                            "Updated lock settings for %s, friendly_name: %s",
                            lock.lock_name,
                            lock.settings.friendly_name,
                        )
                    else:
                        _LOGGER.debug(
                            "No changes made to lock %s settings", lock.lock_name
                        )

                    return

        _LOGGER.error("No lock found for entity_id: %s", entity_id)

    @staticmethod
    async def clear_all_slots(hass: HomeAssistant, service_call: ServiceCall) -> None:
        """Clear all code slots for a lock."""
        entity_id = service_call.data[ATTR_ENTITY_ID]

        result = find_lock(hass, entity_id)
        if result is None:
            _LOGGER.error("No lock found for entity_id: %s", entity_id)
            return
        lock, entry_id, _entry_data = result
        cleared_slots = []
        failed_slots = []

        from ..storage.lock_storage import save_lock_data
        from .zwave_services import _clear_usercode

        # Physically clear each configured slot from the deadbolt FIRST, then
        # null the in-memory definition only on a confirmed clear. A failed
        # clear keeps the PIN and marks the slot unsynced (via
        # ``mark_slot_removal_failed``) so the coordinator's remove path retries
        # it — never a green empty slot with a live code still on the lock. The
        # dict keys are not mutated here (only fields are nulled), so iterating
        # over ``.items()`` while awaiting is safe.
        for slot_num, slot in lock.code_slots.items():
            if not slot.pin_code:  # Only clear slots that have codes
                continue
            old_user_name = slot.user_name
            try:
                await _clear_usercode(hass, entity_id, slot_num)
            except Exception as e:
                lock.mark_slot_removal_failed(
                    slot_num, f"Physical clear failed during clear_all: {e}"
                )
                failed_slots.append(slot_num)
                _LOGGER.error(
                    "clear_all_slots: physical clear FAILED for slot %s on %s; "
                    "PIN retained and slot left unsynced for retry: %s",
                    slot_num,
                    entity_id,
                    e,
                )
                continue
            # Confirmed physical clear -> finalize the in-memory wipe.
            lock.clear_code(slot_num)
            cleared_slots.append((slot_num, old_user_name))

        if cleared_slots or failed_slots:
            # Persist both the cleared slots and the retained-but-unsynced ones.
            await save_lock_data(hass, lock, entry_id)

        if cleared_slots:
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

            _LOGGER.debug(
                "Cleared all %s slots for lock %s: %s",
                len(cleared_slots),
                lock.lock_name,
                [f"Slot {num}: {name}" for num, name in cleared_slots],
            )

        if failed_slots:
            _LOGGER.warning(
                "clear_all_slots: %s slot(s) could not be cleared from lock %s "
                "and were left unsynced for retry: %s",
                len(failed_slots),
                lock.lock_name,
                failed_slots,
            )

        if not cleared_slots and not failed_slots:
            _LOGGER.debug("No configured slots to clear for lock %s", lock.lock_name)
