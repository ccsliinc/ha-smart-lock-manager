"""Lock class for Smart Lock Manager."""

import logging
from dataclasses import dataclass, field
from datetime import datetime, time
from enum import Enum
from typing import Any, Dict, List, Optional

_LOGGER = logging.getLogger(__name__)


class SlotStatus:
    """Represents a slot status with label, color, and description."""

    def __init__(self, name: str, label: str, color: str, description: str = ""):
        self.name = name
        self.label = label  # Display text for UI
        self.color = color  # Hex color code
        self.description = description  # Detailed reason/explanation

    def to_dict(self) -> Dict[str, str]:
        """Convert to dictionary for JSON serialization."""
        return {
            "name": self.name,
            "label": self.label,
            "color": self.color,
            "description": self.description,
        }


# Define all possible slot statuses
SLOT_STATUSES = {
    "EMPTY": SlotStatus(
        "EMPTY", "Click to configure", "#9e9e9e", "No PIN code configured"
    ),
    "DISABLING": SlotStatus(
        "DISABLING", "Disabling", "#ff9800", "Clearing code from physical lock"
    ),
    "DISABLED": SlotStatus(
        "DISABLED", "Disabled", "#9e9e9e", "Slot is disabled and cleared from lock"
    ),
    "OUTSIDE_HOURS": SlotStatus(
        "OUTSIDE_HOURS", "Outside Hours", "#2196f3", "Time restrictions active"
    ),
    "SYNCHRONIZING": SlotStatus(
        "SYNCHRONIZING", "Synchronizing", "#ff9800", "Syncing with physical lock"
    ),
    "SYNC_ERROR": SlotStatus(
        "SYNC_ERROR", "Sync Error", "#f44336", "Code not found in physical lock"
    ),
    "SYNCHRONIZED": SlotStatus(
        "SYNCHRONIZED", "Synchronized", "#4caf50", "Code active and synced with lock"
    ),
    "UNKNOWN": SlotStatus("UNKNOWN", "Unknown Status", "#9e9e9e", "Status unclear"),
}


@dataclass
class CodeSlot:
    """Represents a single code slot in a smart lock with advanced scheduling and usage tracking."""

    slot_number: int
    pin_code: Optional[str] = None
    is_active: bool = False
    is_synced: bool = False
    user_name: Optional[str] = None

    # Sync retry tracking
    sync_attempts: int = 0
    last_sync_attempt: Optional[datetime] = None
    sync_error: Optional[str] = None

    # Time-based access control
    start_date: Optional[datetime] = None
    end_date: Optional[datetime] = None
    allowed_hours: Optional[List[int]] = None  # Hours 0-23 when access allowed
    allowed_days: Optional[List[int]] = None  # Days 0-6 when allowed (0=Monday)

    # Usage tracking and limits
    use_count: int = 0
    max_uses: int = -1  # -1 = unlimited uses

    # Notifications
    notify_on_use: bool = False

    # Metadata
    created_at: Optional[datetime] = None
    last_used: Optional[datetime] = None
    expires_at: Optional[datetime] = None  # Kept for backwards compatibility

    def is_valid_now(self) -> bool:
        """Check if this slot should be active based on current time and rules."""
        now = datetime.now()

        # Check date range
        if self.start_date and now < self.start_date:
            return False
        if self.end_date and now > self.end_date:
            return False

        # Check allowed hours (0-23)
        if self.allowed_hours and now.hour not in self.allowed_hours:
            return False

        # Check allowed days (0=Monday, 6=Sunday)
        if self.allowed_days and now.weekday() not in self.allowed_days:
            return False

        # Check usage limits
        if self.max_uses > 0 and self.use_count >= self.max_uses:
            return False

        return self.is_active and bool(self.pin_code)

    def increment_usage(self) -> None:
        """Increment usage counter and update last used timestamp."""
        self.use_count += 1
        self.last_used = datetime.now()

    def reset_usage(self) -> None:
        """Reset usage counter to 0."""
        self.use_count = 0

    def should_disable(self) -> bool:
        """Check if slot should be automatically disabled due to rules."""
        # Check if expired
        if self.end_date and datetime.now() > self.end_date:
            return True

        # Check if max uses reached
        if self.max_uses > 0 and self.use_count >= self.max_uses:
            return True

        return False

    def get_status(self, is_valid_now: bool) -> SlotStatus:
        """Get the current status of this slot."""
        # Empty slot - no PIN code configured
        if not self.pin_code:
            return SLOT_STATUSES["EMPTY"]

        # Priority 1: Disabling - disabled but still needs to be cleared from physical lock
        # Show "DISABLING" (amber) when disabled but not yet synced (cleared from lock)
        if not self.is_active and self.pin_code and not self.is_synced:
            return SLOT_STATUSES["DISABLING"]

        # Priority 2: Check if slot should be auto-disabled due to expiration/usage
        if self.should_disable():
            return SLOT_STATUSES["DISABLED"]

        # Priority 3: Disabled - manually disabled and confirmed cleared from lock
        if not self.is_active:
            return SLOT_STATUSES["DISABLED"]

        # Priority 4: Outside allowed hours/days (time-restricted)
        if self.is_active and not is_valid_now:
            return SLOT_STATUSES["OUTSIDE_HOURS"]

        # Priority 5: Active and should be valid, check sync status
        if self.is_active and is_valid_now:
            # Check for synchronizing state (has sync attempts means actively syncing)
            if self.sync_attempts > 0:
                return SLOT_STATUSES["SYNCHRONIZING"]
            # If not synced but no active attempts, it's an error
            if not self.is_synced:
                return SLOT_STATUSES["SYNC_ERROR"]
            # All good - active, valid, and synced
            return SLOT_STATUSES["SYNCHRONIZED"]

        # Fallback for any unexpected state
        return SLOT_STATUSES["UNKNOWN"]


@dataclass
class LockSettings:
    """Settings for a Smart Lock Manager lock."""

    friendly_name: str
    auto_lock_time: Optional[time] = None
    auto_unlock_time: Optional[time] = None
    timezone: str = "UTC"


@dataclass
class SmartLockManagerLock:
    """Class to represent a Smart Lock Manager lock with all data stored in objects."""

    # Basic lock information
    lock_name: str
    lock_entity_id: str
    slots: int = 10
    start_from: int = 1

    # Lock settings
    settings: LockSettings = field(
        default_factory=lambda: LockSettings(friendly_name="Smart Lock")
    )

    # Lock hierarchy (parent-child relationships)
    is_main_lock: bool = True
    parent_lock_id: Optional[str] = None
    child_lock_ids: List[str] = field(default_factory=list)

    # Z-Wave related (optional for future Z-Wave integration)
    alarm_level_or_user_code_entity_id: Optional[str] = None
    alarm_type_or_access_control_entity_id: Optional[str] = None
    door_sensor_entity_id: Optional[str] = None

    # All code slots stored as Python objects (NO SENSORS!)
    code_slots: Dict[int, CodeSlot] = field(default_factory=dict)

    # Lock status and connection state (NO SENSORS!)
    is_connected: bool = True
    connection_status: str = (
        "Connected"  # "Connected", "Connecting", "Disconnected", "Disconnecting"
    )
    last_updated: Optional[datetime] = None

    def __post_init__(self) -> None:
        """Initialize code slots after dataclass creation."""
        if not self.code_slots:
            # Create all code slots as Python objects
            for slot_num in range(self.start_from, self.start_from + self.slots):
                self.code_slots[slot_num] = CodeSlot(slot_number=slot_num)

    def set_code(
        self,
        slot_number: int,
        pin_code: str,
        user_name: Optional[str] = None,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
        allowed_hours: Optional[List[int]] = None,
        allowed_days: Optional[List[int]] = None,
        max_uses: int = -1,
        notify_on_use: bool = False,
    ) -> bool:
        """Set a PIN code for a slot with advanced scheduling. Returns True if successful."""
        if slot_number not in self.code_slots:
            return False

        # Validate PIN code format for Z-Wave locks
        if pin_code:
            if not pin_code.isdigit():
                _LOGGER.error("PIN code must be numeric only: %s", pin_code)
                return False
            if len(pin_code) < 4 or len(pin_code) > 8:
                _LOGGER.error(
                    "PIN code must be 4-8 digits: %s (length: %d)",
                    pin_code,
                    len(pin_code),
                )
                return False

        slot = self.code_slots[slot_number]
        slot.pin_code = pin_code
        slot.user_name = user_name
        slot.is_active = bool(pin_code)
        slot.is_synced = False  # Mark as unsynced until Z-Wave confirms
        slot.created_at = datetime.now()

        # Set scheduling parameters
        slot.start_date = start_date
        slot.end_date = end_date
        slot.allowed_hours = allowed_hours
        slot.allowed_days = allowed_days
        slot.max_uses = max_uses
        slot.notify_on_use = notify_on_use

        # Reset usage when setting new code
        slot.use_count = 0
        slot.last_used = None

        return True

    def clear_code(self, slot_number: int) -> bool:
        """Clear a PIN code from a slot. Returns True if successful."""
        if slot_number not in self.code_slots:
            return False

        slot = self.code_slots[slot_number]
        slot.pin_code = None
        slot.user_name = None
        slot.is_active = False
        slot.is_synced = True
        slot.created_at = None
        slot.expires_at = None

        # Clear all scheduling and usage data
        slot.start_date = None
        slot.end_date = None
        slot.allowed_hours = None
        slot.allowed_days = None
        slot.max_uses = -1
        slot.notify_on_use = False
        slot.use_count = 0
        slot.last_used = None

        return True

    def get_active_codes_count(self) -> int:
        """Get count of active code slots."""
        active_slots = [
            slot_num for slot_num, slot in self.code_slots.items() if slot.is_active
        ]
        return len(active_slots)

    def get_configured_codes_count(self) -> int:
        """Get count of configured code slots (with PIN codes, regardless of active status)."""
        configured_slots = [
            slot_num for slot_num, slot in self.code_slots.items() if slot.pin_code
        ]
        return len(configured_slots)

    def get_slot_info(self, slot_number: int) -> Optional[CodeSlot]:
        """Get information about a specific slot."""
        return self.code_slots.get(slot_number)

    def get_all_active_slots(self) -> Dict[int, CodeSlot]:
        """Get all active code slots."""
        return {num: slot for num, slot in self.code_slots.items() if slot.is_active}

    def get_valid_slots_now(self) -> Dict[int, CodeSlot]:
        """Get all slots that are currently valid based on time/usage rules."""
        return {
            num: slot for num, slot in self.code_slots.items() if slot.is_valid_now()
        }

    def check_and_update_slot_validity(self) -> List[int]:
        """
        Check all slots for validity changes and auto-disable expired ones.
        Returns list of slot numbers that had validity changes.
        """
        changed_slots = []

        for slot_number, slot in self.code_slots.items():
            if not slot.is_active:
                continue

            # Check if slot should be disabled due to expiration or max uses
            should_disable = slot.should_disable()
            was_valid = slot.is_valid_now()

            if should_disable and slot.is_active:
                # Auto-disable expired/overused slots
                slot.is_active = False
                changed_slots.append(slot_number)
                _LOGGER.warning(
                    "Auto-disabled slot %s in %s: %s",
                    slot_number,
                    self.lock_name,
                    (
                        "expired"
                        if (slot.end_date and datetime.now() > slot.end_date)
                        else "max uses reached"
                    ),
                )

            # Check for validity state changes (for real-time sync)
            elif was_valid != slot.is_valid_now():
                changed_slots.append(slot_number)

        return changed_slots

    def get_slots_needing_sync(
        self, zwave_codes: Optional[Dict[int, Dict[Any, Any]]] = None
    ) -> Dict[str, List[int]]:
        """
        Get slots that need Z-Wave synchronization based on actual lock state.
        Returns dict with 'add', 'remove', and 'retry' lists.
        """
        add_slots = []  # Codes to add to lock
        remove_slots = []  # Codes to remove from lock
        retry_slots = []  # Failed syncs to retry

        # Get current Z-Wave codes if provided
        zwave_codes = zwave_codes or {}

        for slot_number, slot in self.code_slots.items():
            zwave_code = zwave_codes.get(slot_number, {}).get("code")

            # Smart Lock Manager wants this slot active
            if slot.is_active and slot.pin_code and slot.is_valid_now():
                # Check if code matches what's in the lock
                if not zwave_code or zwave_code != slot.pin_code:
                    if slot.sync_attempts < 10:
                        add_slots.append(slot_number)
                    else:
                        retry_slots.append(slot_number)
                        slot.sync_error = f"Failed to sync after 10 attempts"

            # Smart Lock Manager wants this slot disabled/removed
            elif not slot.is_active or not slot.pin_code or not slot.is_valid_now():
                # Check if there's still a code in the lock that shouldn't be there
                if zwave_code:
                    remove_slots.append(slot_number)

        # Find rogue codes in lock that Smart Lock Manager doesn't know about
        for slot_number, zwave_data in zwave_codes.items():
            if slot_number not in self.code_slots:
                # Rogue code - remove it
                remove_slots.append(slot_number)
            elif not self.code_slots[slot_number].is_active:
                # Code exists but slot should be inactive - remove it
                remove_slots.append(slot_number)

        return {"add": add_slots, "remove": remove_slots, "retry": retry_slots}

    def update_sync_status(
        self, zwave_codes: Optional[Dict[int, Dict[Any, Any]]] = None
    ) -> None:
        """Update slot sync status based on actual Z-Wave codes."""
        zwave_codes = zwave_codes or {}

        for slot_number, slot in self.code_slots.items():
            zwave_code = zwave_codes.get(slot_number, {}).get("code")

            # Check if slot is properly synced
            if slot.is_active and slot.pin_code:
                if zwave_code == slot.pin_code:
                    slot.is_synced = True
                    slot.sync_attempts = 0  # Reset on success
                    slot.sync_error = None
                else:
                    slot.is_synced = False
            elif not slot.is_active or not slot.pin_code:
                # Slot should be empty - mark as synced only if lock is also empty
                if not zwave_code:
                    slot.is_synced = True
                    slot.sync_attempts = 0
                    slot.sync_error = None
                else:
                    slot.is_synced = False
            else:
                slot.is_synced = False

    def resize_slots(self, new_slot_count: int) -> bool:
        """Change the number of slots, clearing higher slots if reducing."""
        if new_slot_count < 1:
            return False

        old_count = self.slots
        self.slots = new_slot_count

        # If reducing slots, clear and remove higher numbered slots
        if new_slot_count < old_count:
            slots_to_remove = []
            for slot_num in self.code_slots:
                if slot_num >= self.start_from + new_slot_count:
                    slots_to_remove.append(slot_num)

            for slot_num in slots_to_remove:
                del self.code_slots[slot_num]

        # If increasing slots, add new empty slots
        elif new_slot_count > old_count:
            for slot_num in range(
                self.start_from + old_count, self.start_from + new_slot_count
            ):
                self.code_slots[slot_num] = CodeSlot(slot_number=slot_num)

        return True

    def reset_slot_usage(self, slot_number: int) -> bool:
        """Reset usage counter for a specific slot."""
        if slot_number not in self.code_slots:
            return False

        self.code_slots[slot_number].reset_usage()
        return True

    def increment_slot_usage(self, slot_number: int) -> bool:
        """Increment usage counter for a slot (called when lock is used)."""
        if slot_number not in self.code_slots:
            return False

        slot = self.code_slots[slot_number]
        slot.increment_usage()

        # Check if slot should be disabled after use
        if slot.should_disable():
            slot.is_active = False

        return True

    def enable_slot(self, slot_number: int) -> bool:
        """Enable a slot (make it active)."""
        if slot_number not in self.code_slots:
            return False

        slot = self.code_slots[slot_number]
        if slot.pin_code:  # Only enable if has a PIN code
            slot.is_active = True
            return True
        return False

    def disable_slot(self, slot_number: int) -> bool:
        """Disable a slot (make it inactive and mark as needing removal from lock)."""
        if slot_number not in self.code_slots:
            return False

        slot = self.code_slots[slot_number]
        slot.is_active = False
        # Mark as unsynced so it gets removed from the physical lock
        slot.is_synced = False
        # Reset sync attempts - the "DISABLING" status relies on is_active=False + pin_code + not is_synced
        slot.sync_attempts = 0

        return True

    def sync_to_child_locks(self, child_locks: List["SmartLockManagerLock"]) -> None:
        """Sync this main lock's codes to all child locks."""
        if not self.is_main_lock:
            return

        for child_lock in child_locks:
            if child_lock.parent_lock_id == self.lock_entity_id:
                # Copy all active slots to child lock
                for slot_num, slot in self.code_slots.items():
                    if slot_num in child_lock.code_slots:
                        child_slot = child_lock.code_slots[slot_num]
                        child_slot.pin_code = slot.pin_code
                        child_slot.user_name = slot.user_name
                        child_slot.is_active = slot.is_active
                        child_slot.start_date = slot.start_date
                        child_slot.end_date = slot.end_date
                        child_slot.allowed_hours = slot.allowed_hours
                        child_slot.allowed_days = slot.allowed_days
                        child_slot.max_uses = slot.max_uses
                        child_slot.notify_on_use = slot.notify_on_use
                        # Don't sync usage counters - each lock tracks its own usage

    def get_usage_statistics(self) -> Dict[str, Any]:
        """Get usage statistics for this lock."""
        total_uses = sum(slot.use_count for slot in self.code_slots.values())
        active_users = len(
            [slot for slot in self.code_slots.values() if slot.is_active]
        )
        most_used_slot = max(
            self.code_slots.values(), key=lambda s: s.use_count, default=None
        )

        return {
            "total_uses": total_uses,
            "active_users": active_users,
            "most_used_slot": most_used_slot.slot_number if most_used_slot else None,
            "most_used_count": most_used_slot.use_count if most_used_slot else 0,
            "slots_with_limits": len(
                [s for s in self.code_slots.values() if s.max_uses > 0]
            ),
            "expired_slots": len(
                [
                    s
                    for s in self.code_slots.values()
                    if s.end_date and datetime.now() > s.end_date
                ]
            ),
        }

    def to_dict(self) -> Dict[str, Any]:
        """Convert lock to dictionary for storage."""
        # Convert slot data to serializable format
        slot_data = {}
        for slot_num, slot in self.code_slots.items():
            slot_data[str(slot_num)] = {
                "slot_number": slot.slot_number,
                "pin_code": slot.pin_code,
                "user_name": slot.user_name,
                "is_active": slot.is_active,
                "is_synced": slot.is_synced,
                "sync_attempts": slot.sync_attempts,
                "sync_error": slot.sync_error,
                "start_date": slot.start_date.isoformat() if slot.start_date else None,
                "end_date": slot.end_date.isoformat() if slot.end_date else None,
                "allowed_hours": slot.allowed_hours,
                "allowed_days": slot.allowed_days,
                "max_uses": slot.max_uses,
                "use_count": slot.use_count,
                "notify_on_use": slot.notify_on_use,
                "created_at": slot.created_at.isoformat() if slot.created_at else None,
                "last_used": slot.last_used.isoformat() if slot.last_used else None,
            }

        return {
            "lock_name": self.lock_name,
            "lock_entity_id": self.lock_entity_id,
            "slots": self.slots,
            "start_from": self.start_from,
            "is_main_lock": self.is_main_lock,
            "parent_lock_id": self.parent_lock_id,
            "child_lock_ids": self.child_lock_ids,
            "settings": {
                "friendly_name": self.settings.friendly_name,
                "auto_lock_time": (
                    self.settings.auto_lock_time.isoformat()
                    if self.settings.auto_lock_time
                    else None
                ),
                "auto_unlock_time": (
                    self.settings.auto_unlock_time.isoformat()
                    if self.settings.auto_unlock_time
                    else None
                ),
                "timezone": self.settings.timezone,
            },
            "code_slots": slot_data,
            "is_connected": self.is_connected,
            "connection_status": self.connection_status,
            "last_updated": (
                self.last_updated.isoformat() if self.last_updated else None
            ),
        }


# Legacy compatibility
# Legacy compatibility removed - use SmartLockManagerLock directly
