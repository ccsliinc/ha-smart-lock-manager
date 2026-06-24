"""Serialization mixin for SmartLockManagerLock."""

from datetime import datetime
from typing import TYPE_CHECKING, Any, Dict, List, Optional

if TYPE_CHECKING:
    from .lock import LockSettings
    from .slot import CodeSlot


class LockSerializationMixin:
    """Provides ``to_dict`` serialization for SmartLockManagerLock.

    Split out of ``lock.py`` to keep that module under the 500-line limit.
    Mixed into ``SmartLockManagerLock`` so the public class identity and
    import path are unchanged. The attribute annotations below are
    type-checker-only declarations of the fields supplied by the concrete
    ``SmartLockManagerLock`` dataclass; they carry no runtime effect (the
    dataclass owns the real fields) and let mypy resolve ``self.*`` access.
    """

    if TYPE_CHECKING:
        lock_name: str
        lock_entity_id: str
        slots: int
        start_from: int
        is_main_lock: bool
        parent_lock_id: Optional[str]
        child_lock_ids: List[str]
        code_collision_prefix_length: int
        settings: "LockSettings"
        code_slots: Dict[int, "CodeSlot"]
        access_log: List[Dict[str, Any]]
        is_connected: bool
        connection_status: str
        last_updated: Optional[datetime]

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
                "validation_rejections": slot.validation_rejections,
                "user_id_status": slot.user_id_status,
                "start_date": slot.start_date.isoformat() if slot.start_date else None,
                "end_date": slot.end_date.isoformat() if slot.end_date else None,
                "allowed_hours": slot.allowed_hours,
                "allowed_days": slot.allowed_days,
                "max_uses": slot.max_uses,
                "use_count": slot.use_count,
                "notify_on_use": slot.notify_on_use,
                "created_at": slot.created_at.isoformat() if slot.created_at else None,
                "last_used": slot.last_used.isoformat() if slot.last_used else None,
                "last_sync_attempt": (
                    slot.last_sync_attempt.isoformat()
                    if slot.last_sync_attempt
                    else None
                ),
            }

        return {
            "lock_name": self.lock_name,
            "lock_entity_id": self.lock_entity_id,
            "slots": self.slots,
            "start_from": self.start_from,
            "is_main_lock": self.is_main_lock,
            "parent_lock_id": self.parent_lock_id,
            "child_lock_ids": self.child_lock_ids,
            "code_collision_prefix_length": self.code_collision_prefix_length,
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
            "access_log": self.access_log,
            "is_connected": self.is_connected,
            "connection_status": self.connection_status,
            "last_updated": (
                self.last_updated.isoformat() if self.last_updated else None
            ),
        }
