"""Smart Lock Manager Summary Sensor."""

import logging
from datetime import datetime
from typing import Any, Dict, Optional

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import (
    CoordinatorEntity,
    DataUpdateCoordinator,
)

from .const import DOMAIN, PRIMARY_LOCK
from .models.lock import CodeSlot, SmartLockManagerLock

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Smart Lock Manager sensor."""

    # Get the lock object and coordinator from hass.data
    lock = hass.data[DOMAIN][entry.entry_id][PRIMARY_LOCK]
    coordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]

    # Create ONE summary sensor per lock with rich attributes
    sensor = SmartLockManagerSensor(hass, entry, lock, coordinator)
    async_add_entities([sensor], True)


class SmartLockManagerSensor(CoordinatorEntity, SensorEntity):
    """Summary sensor that exposes lock object data as attributes for automation access."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        lock: SmartLockManagerLock,
        coordinator: DataUpdateCoordinator,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self._hass = hass
        self._entry = entry
        self._lock = lock
        self._attr_unique_id = f"smart_lock_manager_{entry.entry_id}"
        self._attr_icon = "mdi:lock-smart"

    @property
    def name(self) -> str:
        """Return the name of the sensor using current friendly name."""
        current_lock = self._get_current_lock()
        # Use friendly name if available, otherwise fall back to lock name
        display_name = current_lock.settings.friendly_name or current_lock.lock_name
        return display_name.strip()

    @property
    def state(self) -> str:
        """Return the state of the sensor (connection status with friendly name)."""
        current_lock = self._get_current_lock()
        # Use friendly name if available, otherwise fall back to lock name
        display_name = current_lock.settings.friendly_name or current_lock.lock_name
        return f"{current_lock.connection_status} - {display_name.strip()}"

    def _get_current_lock(self) -> SmartLockManagerLock:
        """Get the current lock object from hass.data (in case it was updated)."""
        found_lock = None
        for entry_id, entry_data in self._hass.data[DOMAIN].items():
            if isinstance(entry_data, dict) and entry_data.get(PRIMARY_LOCK):
                lock = entry_data[PRIMARY_LOCK]
                if lock.lock_entity_id == self._lock.lock_entity_id:
                    found_lock = lock
                    _LOGGER.debug(
                        f"Found updated lock for {self._lock.lock_entity_id}: friendly_name={lock.settings.friendly_name}"
                    )
                    break
        
        if found_lock:
            return found_lock
        else:
            _LOGGER.debug(
                f"Using original lock for {self._lock.lock_entity_id}: friendly_name={self._lock.settings.friendly_name}"
            )
            return self._lock

    @property
    def extra_state_attributes(self) -> Dict[str, Any]:
        """Return the attributes of the sensor with ALL object data for automation access."""
        # Always get the latest lock object
        lock_to_use = self._get_current_lock()
        
        # Debug log what friendly name we're about to return
        _LOGGER.info(f"🔍 Sensor Debug - extra_state_attributes for {lock_to_use.lock_entity_id}:")
        _LOGGER.info(f"  - Lock object friendly_name: '{lock_to_use.settings.friendly_name}'")
        _LOGGER.info(f"  - Lock object lock_name: '{lock_to_use.lock_name}'")
        

        # Get all active slots with their details
        active_slots = lock_to_use.get_all_active_slots()

        # Build slot summary for attributes
        slot_details = {}
        active_slot_numbers = []

        # Get all slots (not just active) with their full details
        all_slots = lock_to_use.code_slots
        valid_slots_now = lock_to_use.get_valid_slots_now()

        for slot_num, slot in active_slots.items():
            active_slot_numbers.append(slot_num)

        # Build comprehensive slot details for ALL slots (including empty ones)
        for slot_num, slot in all_slots.items():
            slot_details[f"slot_{slot_num}"] = {
                # Basic slot info
                "slot_number": slot_num,
                "user_name": slot.user_name,
                "pin_code": slot.pin_code,  # Required for frontend color logic
                "is_active": slot.is_active,
                "is_synced": slot.is_synced,
                "is_valid_now": slot_num in valid_slots_now,
                "use_count": slot.use_count,  # Always show usage, even for disabled slots
                # Timestamps
                "created_at": slot.created_at.isoformat() if slot.created_at else None,
                "expires_at": slot.expires_at.isoformat() if slot.expires_at else None,
                "last_used": slot.last_used.isoformat() if slot.last_used else None,
                # Advanced scheduling attributes
                "start_date": slot.start_date.isoformat() if slot.start_date else None,
                "end_date": slot.end_date.isoformat() if slot.end_date else None,
                "allowed_hours": slot.allowed_hours,
                "allowed_days": slot.allowed_days,
                "max_uses": slot.max_uses,
                "notify_on_use": slot.notify_on_use,
                "should_disable": slot.should_disable(),
                # Backend-calculated display fields (NO FRONTEND LOGIC!)
                "display_title": self._get_slot_display_title(slot_num, slot),
                "slot_status": self._get_slot_status_text(
                    slot, slot_num in valid_slots_now
                ),
                "status_color": self._get_slot_status_color(
                    slot, slot_num in valid_slots_now
                ),
                "status_reason": self._get_slot_status_reason(
                    slot, slot_num in valid_slots_now
                ),
            }

        # Get usage statistics and lock hierarchy info
        usage_stats = lock_to_use.get_usage_statistics()
        valid_slot_numbers = list(valid_slots_now.keys())

        # Debug log the final friendly_name that will be returned
        final_friendly_name = lock_to_use.settings.friendly_name
        _LOGGER.info(f"📤 Sensor Debug - Returning friendly_name: '{final_friendly_name}' for {lock_to_use.lock_entity_id}")

        return {
            # Basic lock info
            "lock_name": lock_to_use.lock_name,
            "lock_entity_id": lock_to_use.lock_entity_id,
            "total_slots": lock_to_use.slots,
            "start_from": lock_to_use.start_from,
            # Lock settings and hierarchy  
            "custom_friendly_name": final_friendly_name,
            "auto_lock_time": (
                lock_to_use.settings.auto_lock_time.isoformat()
                if lock_to_use.settings.auto_lock_time
                else None
            ),
            "auto_unlock_time": (
                lock_to_use.settings.auto_unlock_time.isoformat()
                if lock_to_use.settings.auto_unlock_time
                else None
            ),
            "is_main_lock": lock_to_use.is_main_lock,
            "parent_lock_id": lock_to_use.parent_lock_id,
            "child_lock_ids": lock_to_use.child_lock_ids,
            # Status and counts (perfect for automations!)
            "active_codes_count": lock_to_use.get_active_codes_count(),
            "valid_codes_count": len(valid_slots_now),
            "is_connected": lock_to_use.is_connected,
            "connection_status": lock_to_use.connection_status,
            "last_updated": (
                lock_to_use.last_updated.isoformat() if lock_to_use.last_updated else None
            ),
            # Slot information (for easy template access)
            "active_slots": active_slot_numbers,
            "valid_slots_now": valid_slot_numbers,
            # Usage statistics
            "usage_stats": usage_stats,
            # Detailed slot information (for advanced automations)
            "slot_details": slot_details,
            # Integration info
            "integration_version": "1.0.0",
            "architecture": "object_oriented_advanced",
        }

    def _get_slot_display_title(self, slot_num: int, slot: CodeSlot) -> str:
        """Generate the display title for slot (e.g., 'Slot 1: John Doe' or 'Slot 2:')."""
        if slot.user_name:
            return f"Slot {slot_num}: {slot.user_name}"
        else:
            return f"Slot {slot_num}:"

    def _get_slot_status_text(self, slot: CodeSlot, is_valid_now: bool) -> str:
        """Calculate definitive slot status text in backend."""
        # Empty slot - no PIN code configured
        if not slot.pin_code:
            return "Click to configure"

        # Priority 1: Disabled - manually disabled (highest priority)
        if not slot.is_active:
            return "Disabled"

        # Priority 2: Check if slot should be auto-disabled due to expiration/usage
        if slot.should_disable():
            if slot.end_date and datetime.now() > slot.end_date:
                return "Disabled"
            if slot.max_uses > 0 and slot.use_count >= slot.max_uses:
                return "Disabled"

        # Priority 3: Outside allowed hours/days (time-restricted)
        if slot.is_active and not is_valid_now:
            return "Outside Hours"

        # Priority 4: Active and should be valid, check sync status
        if slot.is_active and is_valid_now:
            if not slot.is_synced:
                return f"{slot.use_count} uses • Sync Error"
            return f"{slot.use_count} uses • Synchronized"

        return "Unknown Status"

    def _get_slot_status_color(self, slot: CodeSlot, is_valid_now: bool) -> str:
        """Calculate definitive slot status color in backend."""
        # Grey - empty slot (no PIN code)
        if not slot.pin_code:
            return "#9e9e9e"  # Grey

        # Priority 1: Grey - disabled (manually or auto-disabled)
        if not slot.is_active or slot.should_disable():
            return "#9e9e9e"  # Grey

        # Priority 2: Blue - outside allowed hours/days (time-restricted)
        if slot.is_active and not is_valid_now:
            return "#2196f3"  # Blue

        # Priority 3: Red - sync error (should be in lock but isn't)
        if slot.is_active and is_valid_now and not slot.is_synced:
            return "#f44336"  # Red

        # Priority 4: Amber - awaiting Z-Wave update (syncing)
        if slot.is_active and is_valid_now and slot.sync_attempts > 0:
            return "#ff9800"  # Amber

        # Priority 5: Green - active and properly synced
        if slot.is_active and is_valid_now and slot.is_synced:
            return "#4caf50"  # Green

        return "#9e9e9e"  # Default grey

    def _get_slot_status_reason(self, slot: CodeSlot, is_valid_now: bool) -> str:
        """Provide detailed status explanation for debugging."""
        if not slot.is_active or not slot.pin_code:
            return "No PIN code configured"

        if slot.should_disable():
            if slot.end_date and datetime.now() > slot.end_date:
                return f"Expired on {slot.end_date.strftime('%Y-%m-%d')}"
            if slot.max_uses > 0 and slot.use_count >= slot.max_uses:
                return f"Used {slot.use_count}/{slot.max_uses} times"

        if slot.is_active and not is_valid_now:
            reasons = []
            if slot.allowed_days:
                days_str = ", ".join(
                    [
                        ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"][d]
                        for d in slot.allowed_days
                    ]
                )
                reasons.append(f"Only allowed on: {days_str}")
            if slot.allowed_hours:
                hours_str = f"{min(slot.allowed_hours):02d}:00-{max(slot.allowed_hours)+1:02d}:00"
                reasons.append(f"Only allowed during: {hours_str}")
            return "; ".join(reasons) if reasons else "Time restrictions active"

        if slot.is_active and is_valid_now:
            if not slot.is_synced:
                return "Code not found in physical lock"
            return "Code active and synced with lock"

        return "Status unclear"

    @property
    def device_info(self) -> Dict[str, Any]:
        """Return device information for device registry."""
        current_lock = self._get_current_lock()
        display_name = current_lock.settings.friendly_name or current_lock.lock_name
        return {
            "identifiers": {(DOMAIN, self._entry.entry_id)},
            "name": display_name.strip(),
            "manufacturer": "Smart Lock Manager",
            "model": "Lock Manager",
            "sw_version": "1.0.0",
        }

    @property
    def available(self) -> bool:
        """Return if entity is available."""
        return self.coordinator.last_update_success and self._lock.is_connected
