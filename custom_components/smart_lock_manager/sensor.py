"""Smart Lock Manager Summary Sensor."""

import logging
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
from .models.lock import (
    USER_ID_STATUS_AVAILABLE,
    USER_ID_STATUS_DISABLED,
    USER_ID_STATUS_ENABLED,
    CodeSlot,
    SmartLockManagerLock,
)

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

    entities: list = []

    # Per-lock hardware-state sensor (lock/unlock, battery, jam) + per-lock
    # access log. Now zone-aware (exposes zone_id/zone_name/member_locks).
    entities.append(SmartLockManagerSensor(hass, entry, lock, coordinator))

    # Zone model (Phase 1): the PER-ZONE sensor is the primary surface. Create
    # it on the config entry that owns the zone's FIRST member (the former main
    # lock), so each zone gets exactly one zone sensor across the fleet.
    from .zone_runtime import get_zone_registry

    for zone in get_zone_registry(hass).values():
        members = zone.member_lock_entity_ids
        if members and members[0] == lock.lock_entity_id:
            entities.append(
                SmartLockManagerZoneSensor(hass, entry, zone.zone_id, coordinator)
            )

    async_add_entities(entities, True)


class SmartLockManagerSensor(CoordinatorEntity, SensorEntity):
    """Summary sensor that exposes lock object data as attributes."""

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
                    break

        if found_lock:
            return found_lock  # type: ignore[no-any-return]
        return self._lock

    def _get_owning_zone(self, entity_id: str) -> Any:
        """Return the Zone that owns ``entity_id``, or None if unhomed.

        - Description: Looks up the in-memory zone registry for the zone that
          lists ``entity_id`` as a member. Returns None when the lock belongs
          to no zone (unhomed).
        - Inputs: entity_id (str lock entity id).
        - Outputs: Zone object or None.
        """
        from .zone_runtime import get_zone_for_lock

        return get_zone_for_lock(self._hass, entity_id)

    @property
    def extra_state_attributes(self) -> Dict[str, Any]:
        """Return the attributes of the sensor with all object data."""
        # Always get the latest lock object
        lock_to_use = self._get_current_lock()

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
            # Get the unified status object
            is_valid_now = slot_num in valid_slots_now
            status = slot.get_status(is_valid_now)

            slot_details[f"slot_{slot_num}"] = {
                # Basic slot info
                "slot_number": slot_num,
                "user_name": slot.user_name,
                "pin_code": slot.pin_code,  # Required for frontend color logic
                "is_active": slot.is_active,
                "is_synced": slot.is_synced,
                "is_valid_now": is_valid_now,
                # Always show usage, even for disabled slots
                "use_count": slot.use_count,
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
                # Z-Wave user ID status
                "user_id_status": slot.user_id_status,
                "user_id_status_text": self._get_user_id_status_text(
                    slot.user_id_status
                ),
                # NEW: Unified status system
                "status": status.to_dict(),
                # Legacy fields for backward compatibility (until frontend is updated)
                "display_title": self._get_slot_display_title(slot_num, slot),
                "slot_status": status.label,
                "status_color": status.color,
                "status_reason": status.description,
            }

        # Get usage statistics and zone membership info
        usage_stats = lock_to_use.get_usage_statistics()
        valid_slot_numbers = list(valid_slots_now.keys())
        final_friendly_name = lock_to_use.settings.friendly_name

        # Zone model (Phase 1): resolve the zone that owns this lock so the
        # sensor exposes zone identity/membership instead of the retired
        # parent/child hierarchy attributes.
        zone = self._get_owning_zone(lock_to_use.lock_entity_id)

        return {
            # Basic lock info
            "lock_name": lock_to_use.lock_name,
            "lock_entity_id": lock_to_use.lock_entity_id,
            "total_slots": lock_to_use.slots,
            "start_from": lock_to_use.start_from,
            # Lock settings
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
            # Zone membership (replaces is_main_lock/parent_lock_id/child_lock_ids)
            "zone_id": zone.zone_id if zone else None,
            "zone_name": zone.name if zone else None,
            "member_locks": list(zone.member_lock_entity_ids) if zone else [],
            # Unhomed = loaded lock that belongs to no zone (Phase-3 "+" pool).
            "is_unhomed": zone is None,
            # Status and counts (perfect for automations!)
            "active_codes_count": lock_to_use.get_active_codes_count(),
            "configured_codes_count": lock_to_use.get_configured_codes_count(),
            "valid_codes_count": len(valid_slots_now),
            "is_connected": lock_to_use.is_connected,
            "connection_status": lock_to_use.connection_status,
            "last_updated": (
                lock_to_use.last_updated.isoformat()
                if lock_to_use.last_updated
                else None
            ),
            # Slot information (for easy template access)
            "active_slots": active_slot_numbers,
            "valid_slots_now": valid_slot_numbers,
            # Usage statistics
            "usage_stats": usage_stats,
            # Detailed slot information (for advanced automations)
            "slot_details": slot_details,
            # Recent access log (most-recent 25 surfaced; full history in storage).
            # Most-recent-first ordering for direct frontend consumption.
            "access_log": list(reversed(lock_to_use.access_log[-25:])),
            # Integration info
            "integration_version": "1.0.0",
            "architecture": "object_oriented_advanced",
        }

    def _get_user_id_status_text(self, user_id_status: Optional[int]) -> str:
        """Map user_id_status integer to human-readable string.

        Description: Converts USER_ID_STATUS_* constant to display text.
        Inputs: user_id_status (Optional[int]) - 0=available, 1=enabled,
            2=disabled, None=unknown
        Outputs: str - Human-readable status label
        Example: _get_user_id_status_text(1) -> "Enabled"
        """
        if user_id_status == USER_ID_STATUS_AVAILABLE:
            return "Available"
        if user_id_status == USER_ID_STATUS_ENABLED:
            return "Enabled"
        if user_id_status == USER_ID_STATUS_DISABLED:
            return "Disabled"
        return "Unknown"

    def _get_slot_display_title(self, slot_num: int, slot: CodeSlot) -> str:
        """Generate display title for slot (e.g., 'Slot 1: John Doe' or 'Slot 2:')."""
        if slot.user_name:
            return f"Slot {slot_num}: {slot.user_name}"
        else:
            return f"Slot {slot_num}:"

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


class SmartLockManagerZoneSensor(CoordinatorEntity, SensorEntity):
    """Primary per-ZONE sensor exposing zone identity, members, and code summary.

    The zone owns the canonical code set; this sensor is the automation/UI
    surface for it. It resolves the live ``Zone`` from the in-memory registry
    on every read so renames and membership changes are reflected immediately.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        zone_id: str,
        coordinator: DataUpdateCoordinator,
    ) -> None:
        """Initialize the zone sensor.

        - Inputs: hass, entry (the config entry that owns the zone's first
          member), zone_id (str), coordinator.
        """
        super().__init__(coordinator)
        self._hass = hass
        self._entry = entry
        self._zone_id = zone_id
        self._attr_unique_id = f"smart_lock_manager_zone_{zone_id}"
        self._attr_icon = "mdi:home-group"

    def _get_zone(self) -> Any:
        """Return the live Zone object from the registry, or None if deleted."""
        from .zone_runtime import get_zone_registry

        return get_zone_registry(self._hass).get(self._zone_id)

    @property
    def name(self) -> str:
        """Return the zone's display name."""
        zone = self._get_zone()
        return f"Zone {zone.name}" if zone else f"Zone {self._zone_id[:8]}"

    @property
    def state(self) -> str:
        """Return a compact summary state: '<name> - N codes / M members'."""
        zone = self._get_zone()
        if not zone:
            return "deleted"
        return (
            f"{zone.name} - {zone.get_configured_codes_count()} codes"
            f" / {len(zone.member_lock_entity_ids)} members"
        )

    @property
    def extra_state_attributes(self) -> Dict[str, Any]:
        """Return zone identity, members, and a per-slot code summary."""
        from .zone_runtime import get_unhomed_lock_entity_ids

        zone = self._get_zone()
        if not zone:
            return {"zone_id": self._zone_id, "deleted": True}

        slot_summary: Dict[str, Any] = {}
        for slot_num, slot in zone.code_slots.items():
            slot_summary[f"slot_{slot_num}"] = {
                "slot_number": slot_num,
                "user_name": slot.user_name,
                "is_active": slot.is_active,
                "has_code": bool(slot.pin_code),
                "max_uses": slot.max_uses,
                "allowed_hours": slot.allowed_hours,
                "allowed_days": slot.allowed_days,
            }

        return {
            "zone_id": zone.zone_id,
            "zone_name": zone.name,
            "member_locks": list(zone.member_lock_entity_ids),
            "member_count": len(zone.member_lock_entity_ids),
            "total_slots": zone.slots,
            "start_from": zone.start_from,
            "active_codes_count": zone.get_active_codes_count(),
            "configured_codes_count": zone.get_configured_codes_count(),
            "slot_summary": slot_summary,
            # Fleet-wide unhomed pool — the locks the "+" picker can add here.
            "unhomed_locks": get_unhomed_lock_entity_ids(self._hass),
            "integration_version": "1.0.0",
            "architecture": "zone_model",
        }

    @property
    def device_info(self) -> Dict[str, Any]:
        """Return device info grouping the zone sensor under its own device."""
        zone = self._get_zone()
        name = f"Zone {zone.name}" if zone else f"Zone {self._zone_id[:8]}"
        return {
            "identifiers": {(DOMAIN, f"zone_{self._zone_id}")},
            "name": name,
            "manufacturer": "Smart Lock Manager",
            "model": "Zone",
            "sw_version": "1.0.0",
        }

    @property
    def available(self) -> bool:
        """True when the coordinator last update succeeded and the zone exists."""
        return self.coordinator.last_update_success and self._get_zone() is not None
