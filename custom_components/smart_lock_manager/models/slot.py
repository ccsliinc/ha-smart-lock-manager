"""Code-slot model and slot-status definitions for Smart Lock Manager."""

from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Optional


class SlotStatus:
    """Represents a slot status with label, color, and description."""

    def __init__(self, name: str, label: str, color: str, description: str = ""):
        """Initialize SlotStatus with display properties."""
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
    "DISABLED_IN_LOCK": SlotStatus(
        "DISABLED_IN_LOCK",
        "Disabled in Lock",
        "#ff9800",
        "Code exists but disabled in physical lock",
    ),
    "UNKNOWN": SlotStatus("UNKNOWN", "Unknown Status", "#9e9e9e", "Status unclear"),
}

# Z-Wave userIdStatus values
USER_ID_STATUS_AVAILABLE = 0
USER_ID_STATUS_ENABLED = 1
USER_ID_STATUS_DISABLED = 2


def find_prefix_conflict(
    new_pin: Optional[str],
    existing_slots: List["CodeSlot"],
    target_slot: int,
    prefix_len: int = 4,
) -> Optional["CodeSlot"]:
    """Return existing slot that collides with ``new_pin`` on its prefix.

    Compares the first ``prefix_len`` digits of ``new_pin`` against every
    other active slot's code; returns the conflicting CodeSlot or ``None``.
    Many Kwikset Z-Wave deadbolts silently drop a user-code write when the
    new PIN shares its first 4 digits with any code already programmed on
    the lock. This helper lets callers pre-validate writes and reject them
    with a clear error instead of burning sync attempts.

    - Description: Detect first-N-digit PIN collisions against active slots.
    - Inputs:
        new_pin: candidate PIN string (digits) or None.
        existing_slots: iterable of CodeSlot objects on the same lock.
        target_slot: slot number being written (excluded from comparison).
        prefix_len: number of leading digits to compare (default 4). A value
            <= 0 disables the check (returns None).
    - Outputs: the conflicting CodeSlot, or None.
    - Example: ``find_prefix_conflict("040873", lock.code_slots.values(), 7)``
    """
    if prefix_len <= 0:
        return None
    if not new_pin or len(new_pin) < prefix_len:
        return None
    prefix = new_pin[:prefix_len]
    for slot in existing_slots:
        if slot.slot_number == target_slot:
            continue  # updating the same slot is always allowed
        if not slot.is_active or not slot.pin_code:
            continue
        if len(slot.pin_code) < prefix_len:
            continue
        if slot.pin_code[:prefix_len] == prefix:
            return slot
    return None


@dataclass
class CodeSlot:
    """Represents a single code slot in a smart lock.

    Includes advanced scheduling and usage tracking capabilities.
    """

    slot_number: int
    pin_code: Optional[str] = None
    is_active: bool = False
    is_synced: bool = False
    user_name: Optional[str] = None

    # Sync retry tracking
    sync_attempts: int = 0
    last_sync_attempt: Optional[datetime] = None
    sync_error: Optional[str] = None

    # Pre-sync validation tracking. Incremented when a write is rejected before
    # dispatch (e.g. Kwikset 4-digit prefix collision). Distinct from
    # ``sync_attempts`` which counts real Z-Wave write dispatches.
    validation_rejections: int = 0

    # Z-Wave userIdStatus (0=available, 1=enabled, 2=disabled)
    user_id_status: Optional[int] = None

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

    def reset_definition(self) -> None:
        """Clear this slot back to an empty, unconfigured definition.

        Resets identity, activation, sync state, schedule, usage limit and
        notification fields to their cleared defaults. Mirrors the wipe
        performed when a lock leaves a zone or a zone's codes are cleared.
        """
        self.pin_code = None
        self.user_name = None
        self.is_active = False
        self.is_synced = False
        self.sync_error = None
        self.sync_attempts = 0
        self.start_date = None
        self.end_date = None
        self.allowed_hours = None
        self.allowed_days = None
        self.max_uses = -1
        self.notify_on_use = False

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

        # Priority 1: Disabling - disabled but still needs to be cleared from lock
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
            # If not synced, check if this is a newly created code (give grace period)
            if not self.is_synced:
                # If created within last 30 seconds, show as syncing instead of error
                if (
                    self.created_at
                    and (datetime.now() - self.created_at).total_seconds() < 30
                ):
                    return SLOT_STATUSES["SYNCHRONIZING"]
                return SLOT_STATUSES["SYNC_ERROR"]
            # Check if code exists but is disabled in the physical lock
            if self.user_id_status == USER_ID_STATUS_DISABLED:
                return SLOT_STATUSES["DISABLED_IN_LOCK"]
            # All good - active, valid, and synced
            return SLOT_STATUSES["SYNCHRONIZED"]

        # Fallback for any unexpected state
        return SLOT_STATUSES["UNKNOWN"]
