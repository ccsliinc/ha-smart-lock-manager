"""Lock class for Smart Lock Manager."""

import logging
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
from typing import Any, Dict, List, Optional

from ..const import MAX_SYNC_ATTEMPTS
from .lock_serialization import LockSerializationMixin
from .slot import (  # noqa: F401  (re-exported for backwards-compat import paths)
    SLOT_STATUSES,
    USER_ID_STATUS_AVAILABLE,
    USER_ID_STATUS_DISABLED,
    USER_ID_STATUS_ENABLED,
    CodeSlot,
    SlotStatus,
    find_prefix_conflict,
)

_LOGGER = logging.getLogger(__name__)

# Maximum number of access-log entries retained per lock in persistent
# storage. Oldest entries are dropped once this bound is exceeded so the
# ``.storage`` file cannot grow without limit.
ACCESS_LOG_MAX_ENTRIES = 100


@dataclass
class LockSettings:
    """Settings for a Smart Lock Manager lock."""

    friendly_name: str
    auto_lock_time: Optional[time] = None
    auto_unlock_time: Optional[time] = None
    timezone: str = "UTC"


@dataclass
class SmartLockManagerLock(LockSerializationMixin):
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

    # Vendor-specific PIN collision prefix length. Some Z-Wave deadbolts
    # (notably Kwikset 9xx series) silently DROP user-code writes when the
    # new PIN shares its first N digits with any existing code on that lock.
    # Default of 4 reflects the dominant Kwikset behavior; set to 0 to disable.
    code_collision_prefix_length: int = 4

    # Lock status and connection state (NO SENSORS!)
    is_connected: bool = True
    connection_status: str = (
        "Connected"  # "Connected", "Connecting", "Disconnected", "Disconnecting"
    )
    last_updated: Optional[datetime] = None

    # Access log: ordered list of lock/unlock/jam events with user attribution.
    # Each entry is a dict; see ``add_access_log_entry``. Bounded to
    # ``ACCESS_LOG_MAX_ENTRIES`` (oldest dropped) so persistent storage does
    # not grow unbounded. NEVER contains PIN codes — only user_name + slot.
    access_log: List[Dict[str, Any]] = field(default_factory=list)

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
        """Set a PIN code for a slot with advanced scheduling.

        Returns True if successful.
        """
        if slot_number not in self.code_slots:
            return False

        # Validate PIN code format for Z-Wave locks
        if pin_code:
            if not pin_code.isdigit():
                _LOGGER.error(
                    "PIN code for slot %s must be numeric only (length: %d)",
                    slot_number,
                    len(pin_code),
                )
                return False
            if len(pin_code) < 4 or len(pin_code) > 8:
                _LOGGER.error(
                    "PIN code for slot %s must be 4-8 digits (length: %d)",
                    slot_number,
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
        slot.user_id_status = USER_ID_STATUS_AVAILABLE
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
        """Get count of configured code slots.

        Includes slots with PIN codes regardless of active status.
        """
        configured_slots = [
            slot_num for slot_num, slot in self.code_slots.items() if slot.pin_code
        ]
        return len(configured_slots)

    def find_prefix_conflict(
        self, new_pin: Optional[str], target_slot: int
    ) -> Optional[CodeSlot]:
        """Return CodeSlot that collides with ``new_pin`` on prefix, or None.

        Convenience wrapper around the module-level ``find_prefix_conflict``
        that uses this lock's ``code_collision_prefix_length`` setting.
        """
        return find_prefix_conflict(
            new_pin,
            list(self.code_slots.values()),
            target_slot,
            self.code_collision_prefix_length,
        )

    def get_all_active_slots(self) -> Dict[int, CodeSlot]:
        """Get all active code slots."""
        return {num: slot for num, slot in self.code_slots.items() if slot.is_active}

    def get_valid_slots_now(self) -> Dict[int, CodeSlot]:
        """Get all slots that are currently valid based on time/usage rules."""
        return {
            num: slot for num, slot in self.code_slots.items() if slot.is_valid_now()
        }

    def check_and_update_slot_validity(self) -> List[int]:
        """Check all slots for validity changes and auto-disable expired ones.

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
        """Get slots that need Z-Wave synchronization based on actual lock state.

        Returns dict with 'add', 'remove', and 'retry' lists.
        """
        add_slots = []  # Codes to add to lock
        remove_slots = []  # Codes to remove from lock
        retry_slots = []  # Failed syncs to retry

        # Get current Z-Wave codes if provided
        zwave_codes = zwave_codes or {}

        now = datetime.now()
        for slot_number, slot in self.code_slots.items():
            # Grace period: skip slots that were recently written (within 60s)
            if slot.last_sync_attempt and (now - slot.last_sync_attempt) < timedelta(
                seconds=60
            ):
                continue

            zwave_code = zwave_codes.get(slot_number, {}).get("code")

            # Smart Lock Manager wants this slot active
            if slot.is_active and slot.pin_code and slot.is_valid_now():
                # Check if code matches what's in the lock
                if not zwave_code or zwave_code != slot.pin_code:
                    if slot.sync_attempts < MAX_SYNC_ATTEMPTS:
                        add_slots.append(slot_number)
                    else:
                        retry_slots.append(slot_number)
                        slot.sync_error = (
                            f"Failed to sync after {MAX_SYNC_ATTEMPTS} attempts"
                        )
                # Code matches — it's synced, don't re-write regardless of in_use

            # Only remove codes that SLM intentionally disabled
            # (is_active=False AND pin_code is set means SLM explicitly
            # disabled this slot)
            elif not slot.is_active and slot.pin_code:
                if zwave_code:
                    remove_slots.append(slot_number)

            # Do NOT remove codes where SLM has no pin_code
            # (SLM never managed this slot)
            # Do NOT remove codes based on is_valid_now()
            # (time-based disabling handled separately)

        return {"add": add_slots, "remove": remove_slots, "retry": retry_slots}

    def update_sync_status(
        self, zwave_codes: Optional[Dict[int, Dict[Any, Any]]] = None
    ) -> None:
        """Update slot sync status based on actual Z-Wave codes."""
        zwave_codes = zwave_codes or {}

        for slot_number, slot in self.code_slots.items():
            zwave_code = zwave_codes.get(slot_number, {}).get("code")
            zwave_in_use = zwave_codes.get(slot_number, {}).get("in_use", False)

            # Map Z-Wave in_use to user_id_status
            if slot_number in zwave_codes:
                if zwave_in_use:
                    slot.user_id_status = USER_ID_STATUS_ENABLED
                elif zwave_code:
                    slot.user_id_status = USER_ID_STATUS_DISABLED
                else:
                    slot.user_id_status = USER_ID_STATUS_AVAILABLE

            # Check if slot is properly synced
            if slot.is_active and slot.pin_code:
                if zwave_code == slot.pin_code:
                    # Code matches in lock — that's what matters for sync
                    slot.is_synced = True
                    slot.sync_attempts = 0
                    slot.sync_error = None
                    # user_id_status is informational only, not a sync gate
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

    def add_access_log_entry(
        self,
        action: str,
        source: str,
        user_name: Optional[str] = None,
        slot: Optional[int] = None,
        timestamp: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        """Append a lock/unlock/jam event to the bounded access log.

        - Description: Record a physical lock event with user attribution and
          keep only the most recent ``ACCESS_LOG_MAX_ENTRIES`` entries.
        - Inputs:
            action: "locked", "unlocked", or "jammed".
            source: "keypad", "manual", "rf", or "auto".
            user_name: resolved person name for keypad events, else None.
            slot: Z-Wave user slot number for keypad events, else None.
            timestamp: event time (defaults to ``datetime.now()``).
        - Outputs: the entry dict that was appended.
        - Example: ``lock.add_access_log_entry("unlocked", "keypad", "Joe", 1)``

        SECURITY: never stores PIN codes — only user_name and slot number.

        Each entry also records which physical lock produced the event:
        ``lock_name`` (friendly name), ``lock_entity_id``, and ``role``
        ("parent" or "child", derived from ``parent_lock_id``) so a parent
        card can aggregate child-lock events and badge each row by door.
        """
        role = "child" if self.parent_lock_id else "parent"
        entry: Dict[str, Any] = {
            "timestamp": (timestamp or datetime.now()).isoformat(),
            "action": action,
            "source": source,
            "user_name": user_name,
            "slot": slot,
            "lock_name": self.settings.friendly_name or self.lock_name,
            "lock_entity_id": self.lock_entity_id,
            "role": role,
        }
        self.access_log.append(entry)

        # Bound the list: drop oldest entries beyond the cap.
        if len(self.access_log) > ACCESS_LOG_MAX_ENTRIES:
            self.access_log = self.access_log[-ACCESS_LOG_MAX_ENTRIES:]

        return entry

    def enable_slot(self, slot_number: int) -> bool:
        """Enable a slot (make it active)."""
        if slot_number not in self.code_slots:
            return False

        slot = self.code_slots[slot_number]
        if slot.pin_code:  # Only enable if has a PIN code
            slot.is_active = True
            slot.is_synced = False  # Mark as needing sync to lock
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
        # Reset sync attempts for DISABLING status tracking
        slot.sync_attempts = 0

        return True

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


# Legacy compatibility
# Legacy compatibility removed - use SmartLockManagerLock directly


__all__ = [
    "ACCESS_LOG_MAX_ENTRIES",
    "CodeSlot",
    "LockSettings",
    "SLOT_STATUSES",
    "SlotStatus",
    "SmartLockManagerLock",
    "USER_ID_STATUS_AVAILABLE",
    "USER_ID_STATUS_DISABLED",
    "USER_ID_STATUS_ENABLED",
    "find_prefix_conflict",
]
