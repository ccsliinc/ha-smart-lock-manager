"""PIN-free payload serializers for the Smart Lock Manager zones API.

Pure helpers that turn the in-memory zone model + live HA entity state into
JSON-safe dicts for the zones DATA endpoint. Split out of ``zones.py`` so the
``HomeAssistantView`` there stays a thin transport layer over these.

SECURITY: raw PIN codes are NEVER emitted. Each code slot reports only a
``has_code`` boolean (and non-sensitive scheduling/limit metadata).
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from homeassistant.core import HomeAssistant, State

from ..const import MAX_SYNC_ATTEMPTS
from ..models.lock import CodeSlot, SmartLockManagerLock
from ..models.zone import Zone

# Per-slot sync-status values the API reports to the panel. Derived LIVE from
# member locks every request (never the zone's frozen mirror copy):
#   - "synced":  every member that carries this slot has confirmed it synced.
#   - "pending": at least one member has not synced yet, with NO failure (an
#                in-flight write — must NOT raise a warning in the panel).
#   - "error":   a member reported a genuine sync failure (``sync_error`` set,
#                or ``sync_attempts`` reached the retry cap without success).
SYNC_STATUS_SYNCED = "synced"
SYNC_STATUS_PENDING = "pending"
SYNC_STATUS_ERROR = "error"

# Lock entity states Home Assistant reports. Anything else is passed through
# verbatim so the panel can render unexpected values without guessing.
_KNOWN_LOCK_STATES = {"locked", "unlocked", "jammed", "unavailable", "unknown"}


def _resolve_node_id(hass: HomeAssistant, entity_id: str) -> Optional[int]:
    """Return the Z-Wave node id backing ``entity_id``, or None.

    Reuses the same resolution path as the access-log matcher: the seeded
    dev-mock table under ``SLM_DEV_MOCK``, otherwise the zwave_js helper. Any
    failure (entity not a zwave_js lock, helper unavailable) yields None.

    - Inputs: hass (HomeAssistant), entity_id (str lock entity id).
    - Outputs: int node id, or None when it cannot be determined.
    """
    from ..dev_mock import is_dev_mock, mock_node_for_entity

    try:
        if is_dev_mock():
            node = mock_node_for_entity(entity_id)
        else:
            from homeassistant.components.zwave_js.helpers import (
                async_get_node_from_entity_id,
            )

            node = async_get_node_from_entity_id(hass, entity_id)
    except Exception:  # pragma: no cover - defensive; missing zwave_js, etc.
        return None
    node_id = getattr(node, "node_id", None)
    return int(node_id) if node_id is not None else None


def _lock_friendly_name(
    hass: HomeAssistant, entity_id: str, lock: Optional[SmartLockManagerLock]
) -> str:
    """Return the best human-readable name for a member lock.

    SLM's stored ``settings.friendly_name`` WINS when set (the panel's Edit
    modal writes it, so the user's chosen name is authoritative everywhere).
    Falls back to the live HA entity ``friendly_name`` attribute, then the SLM
    lock object's ``lock_name``, then the raw entity id as a last resort.

    - Inputs: hass, entity_id (str), lock (SmartLockManagerLock or None).
    - Outputs: str display name (never empty).
    """
    if lock is not None:
        slm_name = lock.settings.friendly_name
        if slm_name:
            return str(slm_name)
    state = hass.states.get(entity_id)
    if state is not None:
        name = state.attributes.get("friendly_name")
        if name:
            return str(name)
    if lock is not None:
        return lock.lock_name or entity_id
    return entity_id


def _lock_state_value(state: Optional[State]) -> str:
    """Normalize a lock entity's state string for the payload.

    - Inputs: state (HA State or None).
    - Outputs: one of locked/unlocked/jammed/unavailable/unknown (or the raw
      state string if HA reports something outside the known set).
    """
    if state is None:
        return "unavailable"
    value = (state.state or "unknown").lower()
    return value if value in _KNOWN_LOCK_STATES else value


def _battery_level(
    hass: HomeAssistant, entity_id: str, state: Optional[State]
) -> Optional[int]:
    """Return the member lock's battery percentage, or None if unknown.

    Looks first at a ``battery_level`` attribute on the lock entity, then at a
    companion ``sensor.<object_id>_battery`` / ``sensor.<object_id>_battery_level``
    entity. Non-numeric or missing values yield None.

    - Inputs: hass, entity_id (str), state (the lock's State or None).
    - Outputs: int 0-100, or None.
    """
    if state is not None:
        raw = state.attributes.get("battery_level")
        level = _coerce_int(raw)
        if level is not None:
            return level

    object_id = entity_id.split(".", 1)[-1]
    for candidate in (
        f"sensor.{object_id}_battery",
        f"sensor.{object_id}_battery_level",
    ):
        companion = hass.states.get(candidate)
        if companion is not None:
            level = _coerce_int(companion.state)
            if level is not None:
                return level
    return None


def _is_jammed(hass: HomeAssistant, entity_id: str, lock_state: str) -> bool:
    """Return True if the member lock is currently jammed.

    A lock is jammed when its own entity state is ``jammed`` OR a companion
    ``binary_sensor.<object_id>_jammed`` reports ``on``.

    - Inputs: hass, entity_id (str), lock_state (normalized state string).
    - Outputs: bool.
    """
    if lock_state == "jammed":
        return True
    object_id = entity_id.split(".", 1)[-1]
    jam = hass.states.get(f"binary_sensor.{object_id}_jammed")
    if jam is not None and (jam.state or "").lower() == "on":
        return True
    return False


def _coerce_int(value: Any) -> Optional[int]:
    """Best-effort convert a value to int, or None on failure.

    - Inputs: value (any).
    - Outputs: int, or None when the value is missing/non-numeric.
    """
    if value is None:
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _member_slot_is_error(member_slot: CodeSlot) -> bool:
    """Return True if a member's slot is in a GENUINE sync-failure state.

    A real failure — distinct from an in-flight pending write — is when the
    member recorded a ``sync_error`` OR exhausted the retry cap
    (``sync_attempts`` >= :data:`MAX_SYNC_ATTEMPTS`) without confirming sync.

    - Inputs: member_slot (CodeSlot) — the member lock's copy of the slot.
    - Outputs: bool — True only for a hard failure, never for pending writes.
    """
    if member_slot.is_synced:
        return False
    if member_slot.sync_error:
        return True
    return member_slot.sync_attempts >= MAX_SYNC_ATTEMPTS


def _derive_slot_sync(
    zone: Zone,
    slot_number: int,
    member_locks: List[SmartLockManagerLock],
) -> Dict[str, Any]:
    """Derive a zone slot's LIVE sync status from its member locks.

    Fixes the stale-mirror bug: the zone's own ``code_slots[n].is_synced`` is a
    frozen copy written when the code was last applied and is never refreshed as
    members sync. This computes the slot's true status from the member locks'
    live per-lock state in ``hass.data`` instead.

    Only members that actually CARRY this slot's code (matching pin + active
    intent) count toward the aggregate; a member that does not yet have the code
    mirrored is treated as pending, not synced.

    Aggregation rule:
      - ERROR  if ANY contributing member is in a hard sync-failure state.
      - SYNCED if no error AND every contributing member confirmed synced.
      - PENDING otherwise (in-flight, no failure).
    An empty/codeless zone slot reports SYNCED (nothing to push).

    - Inputs: zone (Zone), slot_number (int), member_locks (live lock objects).
    - Outputs: dict {status, sync_error} where status is one of
      ``SYNC_STATUS_*`` and sync_error is the first member failure detail (or
      None).
    """
    zone_slot = zone.code_slots.get(slot_number)
    if zone_slot is None or not zone_slot.pin_code:
        return {"status": SYNC_STATUS_SYNCED, "sync_error": None}

    has_member = False
    all_synced = True
    first_error: Optional[str] = None
    for lock in member_locks:
        member_slot = lock.code_slots.get(slot_number)
        if member_slot is None:
            # Member has not even allocated this slot yet -> pending.
            all_synced = False
            continue
        has_member = True
        if _member_slot_is_error(member_slot):
            if first_error is None:
                first_error = (
                    member_slot.sync_error
                    or f"Failed to sync after {MAX_SYNC_ATTEMPTS} attempts"
                )
        if not member_slot.is_synced:
            all_synced = False

    if first_error is not None:
        return {"status": SYNC_STATUS_ERROR, "sync_error": first_error}
    if has_member and all_synced:
        return {"status": SYNC_STATUS_SYNCED, "sync_error": None}
    return {"status": SYNC_STATUS_PENDING, "sync_error": None}


def _serialize_slot(slot: CodeSlot, sync: Dict[str, Any]) -> Dict[str, Any]:
    """Serialize one zone code slot WITHOUT exposing the PIN.

    Emits ``has_code`` (whether a PIN is configured) plus non-sensitive
    scheduling/limit metadata. The raw PIN value is intentionally never
    referenced here.

    The slot's sync state is supplied by :func:`_derive_slot_sync` (computed
    LIVE from member locks) rather than read off the zone's frozen mirror.
    ``is_synced`` is retained as a derived boolean for backward compatibility;
    ``sync_status`` (synced/pending/error) is the authoritative field and
    ``sync_error`` carries the failure detail when present.

    - Inputs: slot (CodeSlot), sync (dict from :func:`_derive_slot_sync`).
    - Outputs: JSON-safe dict, PIN-free.
    """
    has_code = bool(slot.pin_code)
    remaining_uses: Optional[int] = None
    if slot.max_uses is not None and slot.max_uses >= 0:
        remaining_uses = max(slot.max_uses - slot.use_count, 0)

    status = sync["status"]
    return {
        "slot_number": slot.slot_number,
        "user_name": slot.user_name,
        "has_code": has_code,
        "is_active": slot.is_active,
        "is_synced": status == SYNC_STATUS_SYNCED,
        "sync_status": status,
        "sync_error": sync["sync_error"],
        "start_date": slot.start_date.isoformat() if slot.start_date else None,
        "end_date": slot.end_date.isoformat() if slot.end_date else None,
        "allowed_hours": slot.allowed_hours,
        "allowed_days": slot.allowed_days,
        "max_uses": slot.max_uses,
        "use_count": slot.use_count,
        "remaining_uses": remaining_uses,
        "notify_on_use": slot.notify_on_use,
    }


def _serialize_member(
    hass: HomeAssistant, entity_id: str, locks: Dict[str, SmartLockManagerLock]
) -> Dict[str, Any]:
    """Serialize one zone member lock with its live hardware state.

    - Inputs: hass, entity_id (str), locks (entity_id -> SmartLockManagerLock).
    - Outputs: JSON-safe member dict.
    """
    lock = locks.get(entity_id)
    state = hass.states.get(entity_id)
    lock_state = _lock_state_value(state)
    return {
        "entity_id": entity_id,
        "node_id": _resolve_node_id(hass, entity_id),
        "friendly_name": _lock_friendly_name(hass, entity_id, lock),
        "lock_state": lock_state,
        "battery_level": _battery_level(hass, entity_id, state),
        "is_jammed": _is_jammed(hass, entity_id, lock_state),
        "use_count": _member_use_count(lock),
    }


def _member_use_count(lock: Optional[SmartLockManagerLock]) -> Optional[int]:
    """Return total actuations on a physical lock, or None if unknown.

    Aggregates per-slot ``use_count`` values (the lock object tracks usage at
    the slot level, not as a single lock-wide counter).

    - Inputs: lock (SmartLockManagerLock or None).
    - Outputs: int total uses, or None when the lock object is absent.
    """
    if lock is None:
        return None
    return sum(slot.use_count for slot in lock.code_slots.values())


def _serialize_zone(
    hass: HomeAssistant, zone: Zone, locks: Dict[str, SmartLockManagerLock]
) -> Dict[str, Any]:
    """Serialize one zone (members + PIN-free code slots).

    - Inputs: hass, zone (Zone), locks (entity_id -> SmartLockManagerLock).
    - Outputs: JSON-safe zone dict.
    """
    members = [
        _serialize_member(hass, entity_id, locks)
        for entity_id in zone.member_lock_entity_ids
    ]
    # Live member lock objects backing this zone, used to DERIVE each slot's
    # true sync state (the zone's own slot copy is a frozen mirror).
    member_locks = [
        locks[entity_id]
        for entity_id in zone.member_lock_entity_ids
        if entity_id in locks
    ]

    code_slots: List[Dict[str, Any]] = []
    error_slots: List[int] = []
    for num in sorted(zone.code_slots):
        sync = _derive_slot_sync(zone, num, member_locks)
        if sync["status"] == SYNC_STATUS_ERROR:
            error_slots.append(num)
        code_slots.append(_serialize_slot(zone.code_slots[num], sync))

    return {
        "zone_id": zone.zone_id,
        "name": zone.name,
        "slots": zone.slots,
        "start_from": zone.start_from,
        "active_codes_count": zone.get_active_codes_count(),
        "configured_codes_count": zone.get_configured_codes_count(),
        "members": members,
        "code_slots": code_slots,
        "error_slots": error_slots,
        "has_sync_errors": bool(error_slots),
        # Per-zone operational config for the settings editor. Contains only
        # non-secret config (toggles, thresholds, business hours, notify
        # targets the user typed) — never PIN material.
        "settings": zone.settings.to_dict(),
    }


def _serialize_unhomed(
    hass: HomeAssistant, entity_id: str, locks: Dict[str, SmartLockManagerLock]
) -> Dict[str, Any]:
    """Serialize one unhomed lock for the picker pool.

    - Inputs: hass, entity_id (str), locks (entity_id -> SmartLockManagerLock).
    - Outputs: JSON-safe dict (entity_id, node_id, friendly_name, lock_state).
    """
    lock = locks.get(entity_id)
    state = hass.states.get(entity_id)
    return {
        "entity_id": entity_id,
        "node_id": _resolve_node_id(hass, entity_id),
        "friendly_name": _lock_friendly_name(hass, entity_id, lock),
        "lock_state": _lock_state_value(state),
    }
