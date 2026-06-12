"""Zone model for Smart Lock Manager.

A ``Zone`` is the canonical owner of a code-slot set in the post-refactor
model. Every physical lock belongs to exactly ONE zone; a single lock is a
1-member zone. The zone holds the authoritative ``CodeSlot`` list (and, in
future phases, all access rules) — every member lock obeys uniformly with no
per-member overrides.

Member locks keep a MIRROR of the zone's ``code_slots`` (see
``mirror_to_member``) so the existing per-lock coordinator sync loop can push
the zone's codes to each member's Z-Wave node using the unchanged write
helpers. Per-lock usage counters, ``last_used`` and the access log live on the
member lock and are NEVER overwritten by the mirror.

This module reuses the existing :class:`CodeSlot` from ``models/lock.py`` — it
does NOT redefine a slot type.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

from .lock import CodeSlot, SmartLockManagerLock

_LOGGER = logging.getLogger(__name__)

# Default number of code slots a freshly-created zone exposes. Matches the
# historical per-lock default so migrated zones keep the same slot geometry.
DEFAULT_ZONE_SLOTS = 10
DEFAULT_ZONE_START_FROM = 1

# CodeSlot fields that define the CODE itself (pushed uniformly to every member
# lock). Deliberately EXCLUDES usage/sync state that must remain per-lock:
# ``use_count``, ``last_used``, ``sync_attempts``, ``last_sync_attempt``,
# ``is_synced``, ``sync_error``, ``user_id_status``, ``validation_rejections``.
_CODE_DEFINITION_FIELDS = (
    "pin_code",
    "user_name",
    "is_active",
    "start_date",
    "end_date",
    "allowed_hours",
    "allowed_days",
    "max_uses",
    "notify_on_use",
)


def _slot_to_dict(slot: CodeSlot) -> Dict[str, Any]:
    """Serialize one CodeSlot to a JSON-safe dict.

    - Description: Mirror of the per-slot serialization in
      ``SmartLockManagerLock.to_dict`` so zone storage round-trips identically.
    - Inputs: slot (CodeSlot).
    - Outputs: dict with ISO-formatted datetimes.
    """
    return {
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
            slot.last_sync_attempt.isoformat() if slot.last_sync_attempt else None
        ),
    }


def _slot_from_dict(data: Dict[str, Any]) -> CodeSlot:
    """Rebuild a CodeSlot from its serialized dict.

    - Description: Inverse of :func:`_slot_to_dict`; tolerant of missing keys.
    - Inputs: data (dict).
    - Outputs: CodeSlot.
    """

    def _dt(key: str) -> Optional[datetime]:
        raw = data.get(key)
        return datetime.fromisoformat(raw) if raw else None

    slot_number = int(data.get("slot_number") or 0)
    return CodeSlot(
        slot_number=slot_number,
        pin_code=data.get("pin_code"),
        user_name=data.get("user_name"),
        is_active=data.get("is_active", False),
        is_synced=data.get("is_synced", False),
        sync_attempts=data.get("sync_attempts", 0),
        sync_error=data.get("sync_error"),
        validation_rejections=data.get("validation_rejections", 0),
        user_id_status=data.get("user_id_status"),
        start_date=_dt("start_date"),
        end_date=_dt("end_date"),
        allowed_hours=data.get("allowed_hours"),
        allowed_days=data.get("allowed_days"),
        max_uses=data.get("max_uses", -1),
        use_count=data.get("use_count", 0),
        notify_on_use=data.get("notify_on_use", False),
        created_at=_dt("created_at"),
        last_used=_dt("last_used"),
        last_sync_attempt=_dt("last_sync_attempt"),
    )


def new_zone_id() -> str:
    """Return a fresh immutable zone id (hex UUID4).

    - Outputs: str — 32-char hex id, safe for use in a storage key.
    """
    return uuid.uuid4().hex


@dataclass
class Zone:
    """Canonical owner of a code-slot set shared by one or more member locks.

    Attributes:
        zone_id: immutable unique id (UUID hex). Used in the storage key
            ``smart_lock_manager_zone_{zone_id}``.
        name: free-text, user-editable display name.
        member_lock_entity_ids: HA ``entity_id`` of each member lock. Order is
            preserved; duplicates are not allowed.
        code_slots: canonical slot map keyed by slot number. ALL rules live
            here; member locks mirror this.
        slots: configured slot count (geometry).
        start_from: first slot number.
        code_collision_prefix_length: vendor PIN-prefix guard length, mirrored
            onto members so the existing collision check keeps working.
    """

    zone_id: str
    name: str
    member_lock_entity_ids: List[str] = field(default_factory=list)
    code_slots: Dict[int, CodeSlot] = field(default_factory=dict)
    slots: int = DEFAULT_ZONE_SLOTS
    start_from: int = DEFAULT_ZONE_START_FROM
    code_collision_prefix_length: int = 4
    created_at: Optional[datetime] = None

    def __post_init__(self) -> None:
        """Populate empty code slots for the configured geometry if missing."""
        if not self.code_slots:
            for slot_num in range(self.start_from, self.start_from + self.slots):
                self.code_slots[slot_num] = CodeSlot(slot_number=slot_num)
        if self.created_at is None:
            self.created_at = datetime.now()

    # -- membership helpers -------------------------------------------------

    def has_member(self, entity_id: str) -> bool:
        """Return True if ``entity_id`` is a member of this zone."""
        return entity_id in self.member_lock_entity_ids

    def add_member(self, entity_id: str) -> bool:
        """Add a lock entity to this zone.

        - Inputs: entity_id (str).
        - Outputs: True if added, False if already a member.
        """
        if entity_id in self.member_lock_entity_ids:
            return False
        self.member_lock_entity_ids.append(entity_id)
        return True

    def remove_member(self, entity_id: str) -> bool:
        """Remove a lock entity from this zone.

        - Inputs: entity_id (str).
        - Outputs: True if removed, False if it was not a member.
        """
        if entity_id not in self.member_lock_entity_ids:
            return False
        self.member_lock_entity_ids.remove(entity_id)
        return True

    def is_empty(self) -> bool:
        """Return True if the zone has no member locks."""
        return not self.member_lock_entity_ids

    # -- code helpers -------------------------------------------------------

    def get_active_codes_count(self) -> int:
        """Return the count of active (PIN-bearing, enabled) code slots."""
        return len([s for s in self.code_slots.values() if s.is_active])

    def get_configured_codes_count(self) -> int:
        """Return the count of slots with a PIN code regardless of active flag."""
        return len([s for s in self.code_slots.values() if s.pin_code])

    def mirror_to_member(self, member: SmartLockManagerLock) -> None:
        """Project this zone's code DEFINITIONS onto a member lock.

        Copies only the code-defining fields (PIN, name, active flag, schedule,
        usage limits, notify) onto the member's matching slots. Deliberately
        leaves the member's PER-LOCK state untouched: ``use_count``,
        ``last_used``, ``sync_attempts``, ``last_sync_attempt``, ``is_synced``,
        ``sync_error``, ``user_id_status`` and ``validation_rejections`` all
        remain the member's own, so the existing per-lock coordinator sync loop
        drives each member's Z-Wave writes independently while obeying the
        zone's canonical code set.

        - Inputs: member (SmartLockManagerLock).
        - Outputs: None (mutates ``member.code_slots`` in place).
        """
        # Keep the collision-guard length aligned so the existing prefix check
        # behaves identically on every member.
        member.code_collision_prefix_length = self.code_collision_prefix_length

        for slot_num, zone_slot in self.code_slots.items():
            member_slot = member.code_slots.get(slot_num)
            if member_slot is None:
                member_slot = CodeSlot(slot_number=slot_num)
                member.code_slots[slot_num] = member_slot

            # Detect a code-definition change so we can force a re-sync of just
            # that slot on this member (without clobbering its sync counters
            # when nothing changed).
            changed = any(
                getattr(member_slot, fname) != getattr(zone_slot, fname)
                for fname in _CODE_DEFINITION_FIELDS
            )

            for fname in _CODE_DEFINITION_FIELDS:
                setattr(member_slot, fname, getattr(zone_slot, fname))

            if changed:
                # Definition diverged from what the member last knew: mark it
                # unsynced so the coordinator pushes the new code to this node.
                member_slot.is_synced = False
                member_slot.sync_attempts = 0
                member_slot.sync_error = None
                if member_slot.created_at is None:
                    member_slot.created_at = datetime.now()

    # -- serialization ------------------------------------------------------

    def to_dict(self) -> Dict[str, Any]:
        """Serialize the zone for persistent storage.

        - Outputs: JSON-safe dict (``code_slots`` keyed by str slot number).
        """
        return {
            "zone_id": self.zone_id,
            "name": self.name,
            "member_lock_entity_ids": list(self.member_lock_entity_ids),
            "slots": self.slots,
            "start_from": self.start_from,
            "code_collision_prefix_length": self.code_collision_prefix_length,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "code_slots": {
                str(num): _slot_to_dict(slot) for num, slot in self.code_slots.items()
            },
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Zone":
        """Rebuild a Zone from its serialized dict.

        - Inputs: data (dict) as produced by :meth:`to_dict`.
        - Outputs: Zone.
        """
        code_slots: Dict[int, CodeSlot] = {}
        for num_str, slot_data in (data.get("code_slots") or {}).items():
            slot = _slot_from_dict(slot_data)
            code_slots[int(num_str)] = slot

        created_raw = data.get("created_at")
        zone = cls(
            zone_id=data["zone_id"],
            name=data.get("name", "Zone"),
            member_lock_entity_ids=list(data.get("member_lock_entity_ids") or []),
            code_slots=code_slots,
            slots=data.get("slots", DEFAULT_ZONE_SLOTS),
            start_from=data.get("start_from", DEFAULT_ZONE_START_FROM),
            code_collision_prefix_length=data.get("code_collision_prefix_length", 4),
            created_at=datetime.fromisoformat(created_raw) if created_raw else None,
        )
        return zone

    @classmethod
    def from_main_lock(
        cls, main_lock: SmartLockManagerLock, member_entity_ids: List[str]
    ) -> "Zone":
        """Build a zone by LIFTING codes from a former main/standalone lock.

        Used by the one-time parent/child -> zone migration. The new zone is
        named after the main lock, takes its slot geometry, and copies its
        code slots verbatim (deep-ish per-slot copy so later edits do not alias
        the original lock object). Membership = the main lock plus every member
        entity id supplied (its former children).

        - Inputs:
            main_lock: the former ``is_main_lock`` (or standalone) lock whose
                codes become the zone's canonical set.
            member_entity_ids: ordered entity ids of ALL members (main first).
        - Outputs: a fully-populated Zone.
        """
        # Deep-copy each slot via the dict round-trip so the zone owns its own
        # CodeSlot objects, decoupled from the original lock's in-memory slots.
        lifted: Dict[int, CodeSlot] = {
            num: _slot_from_dict(_slot_to_dict(slot))
            for num, slot in main_lock.code_slots.items()
        }
        zone = cls(
            zone_id=new_zone_id(),
            name=main_lock.settings.friendly_name or main_lock.lock_name,
            member_lock_entity_ids=list(member_entity_ids),
            code_slots=lifted,
            slots=main_lock.slots,
            start_from=main_lock.start_from,
            code_collision_prefix_length=main_lock.code_collision_prefix_length,
        )
        return zone
