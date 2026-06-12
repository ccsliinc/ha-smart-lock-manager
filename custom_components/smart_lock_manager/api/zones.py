"""Authenticated zone DATA API for the Smart Lock Manager panel.

Exposes a single read-only JSON endpoint the redesigned (Phase 3) panel
consumes instead of scraping per-zone sensor states:

    GET /api/smart_lock_manager/zones   (requires_auth = True)

The payload serializes the FULL in-memory zone registry (so empty zones with
no live sensor still appear) plus the "unhomed" lock pool the "+" picker draws
from. Per-member live hardware state (lock/unlock/jammed, battery) is read from
the corresponding Home Assistant entities via ``hass.states``.

SECURITY: raw PIN codes are NEVER emitted. Each code slot reports only a
``has_code`` boolean (and non-sensitive scheduling/limit metadata). This module
deliberately avoids constructing any dict that pairs a PIN value with a slot so
the PIN-safety pre-commit guard stays satisfied.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from aiohttp import web
from homeassistant.components.http import HomeAssistantView
from homeassistant.core import HomeAssistant, State

from ..models.lock import CodeSlot, SmartLockManagerLock
from ..models.zone import Zone
from ..zone_runtime import (
    get_unhomed_lock_entity_ids,
    get_zone_registry,
)

_LOGGER = logging.getLogger(__name__)

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


def _serialize_slot(slot: CodeSlot) -> Dict[str, Any]:
    """Serialize one zone code slot WITHOUT exposing the PIN.

    Emits ``has_code`` (whether a PIN is configured) plus non-sensitive
    scheduling/limit metadata. The raw PIN value is intentionally never
    referenced here.

    - Inputs: slot (CodeSlot).
    - Outputs: JSON-safe dict, PIN-free.
    """
    has_code = bool(slot.pin_code)
    remaining_uses: Optional[int] = None
    if slot.max_uses is not None and slot.max_uses >= 0:
        remaining_uses = max(slot.max_uses - slot.use_count, 0)

    return {
        "slot_number": slot.slot_number,
        "user_name": slot.user_name,
        "has_code": has_code,
        "is_active": slot.is_active,
        "is_synced": slot.is_synced,
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
    code_slots = [
        _serialize_slot(zone.code_slots[num]) for num in sorted(zone.code_slots)
    ]
    return {
        "zone_id": zone.zone_id,
        "name": zone.name,
        "slots": zone.slots,
        "start_from": zone.start_from,
        "active_codes_count": zone.get_active_codes_count(),
        "configured_codes_count": zone.get_configured_codes_count(),
        "members": members,
        "code_slots": code_slots,
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


def _all_locks(hass: HomeAssistant) -> Dict[str, SmartLockManagerLock]:
    """Return every loaded SLM lock keyed by entity_id.

    Mirrors ``zone_runtime._all_locks`` but kept local so this module owns its
    view of the lock objects without importing a private helper.

    - Inputs: hass.
    - Outputs: dict entity_id -> SmartLockManagerLock.
    """
    from ..const import DOMAIN, PRIMARY_LOCK

    locks: Dict[str, SmartLockManagerLock] = {}
    for entry_data in hass.data.get(DOMAIN, {}).values():
        if isinstance(entry_data, dict):
            lock = entry_data.get(PRIMARY_LOCK)
            if lock is not None:
                locks[lock.lock_entity_id] = lock
    return locks


def build_zones_payload(hass: HomeAssistant) -> Dict[str, Any]:
    """Build the full zones DATA payload (zones + unhomed pool).

    - Description: Serializes the entire in-memory zone registry (including
      empty zones) plus the unhomed lock pool, enriching each member with live
      hardware state. Never includes raw PIN codes.
    - Inputs: hass (HomeAssistant).
    - Outputs: dict with ``zones`` (list) and ``unhomed_locks`` (list).
    """
    locks = _all_locks(hass)
    registry = get_zone_registry(hass)

    zones: List[Dict[str, Any]] = [
        _serialize_zone(hass, zone, locks)
        for zone in sorted(registry.values(), key=lambda z: z.name.lower())
    ]
    unhomed = [
        _serialize_unhomed(hass, entity_id, locks)
        for entity_id in get_unhomed_lock_entity_ids(hass)
    ]
    return {"zones": zones, "unhomed_locks": unhomed}


class SmartLockManagerZonesView(HomeAssistantView):
    """Authenticated JSON view serving the full zone model + unhomed pool."""

    requires_auth = True
    url = "/api/smart_lock_manager/zones"
    name = "api:smart_lock_manager:zones"

    def __init__(self, hass: HomeAssistant) -> None:
        """Store the hass reference for state lookups."""
        self.hass = hass

    async def get(self, request: web.Request) -> web.Response:  # noqa
        """Return the serialized zone model as JSON.

        - Inputs: request (aiohttp request; auth enforced by the base view).
        - Outputs: 200 JSON payload, or 500 with an error message on failure.
        """
        try:
            payload = build_zones_payload(self.hass)
            return self.json(payload)
        except Exception as err:  # pragma: no cover - defensive top-level guard
            _LOGGER.error("Error building zones payload: %s", err)
            return self.json({"error": str(err)}, status_code=500)
