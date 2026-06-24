"""Z-Wave Access Control notification -> access-log mapping.

Extracted from ``services/registration.py`` (behavior-preserving) to keep both
modules under the 500-line limit. ``registration`` re-imports the public names
(``map_access_control_event``, ``_resolve_lock_for_node``,
``_build_access_log_handler``) and the package root re-exports them so the
frozen patch target ``custom_components.smart_lock_manager._resolve_lock_for_node``
still resolves.

NEVER import from the package ``__init__`` at module top — ``_save_lock_data``
is imported lazily inside the access-log handler to avoid a circular import.
"""

import logging
from typing import Any, Callable, Dict, Optional

from homeassistant.core import HomeAssistant

from ..const import DOMAIN, PRIMARY_LOCK
from ..dev_mock import is_dev_mock, mock_node_for_entity
from ..models.lock import SmartLockManagerLock

# Module-level logger so log entries appear under
# ``custom_components.smart_lock_manager`` (not ``.services.access_log``).
_LOGGER = logging.getLogger("custom_components.smart_lock_manager")


# ---------------------------------------------------------------------------
# Z-Wave Access Control notification -> access-log mapping
# ---------------------------------------------------------------------------
# Kwikset-style Access Control (command_class 113) event codes mapped to an
# (action, source) tuple. Keypad events (5/6) additionally carry a
# parameters.userId pointing at the SLM code slot.
#   1 = manual lock (thumbturn)        2 = manual unlock (thumbturn)
#   3 = RF lock (app/HA)               4 = RF unlock (app/HA)
#   5 = keypad lock (-> userId)        6 = keypad unlock (-> userId)
#   9 = auto-lock                      11 = lock jammed
ACCESS_CONTROL_EVENT_MAP: Dict[int, Dict[str, str]] = {
    1: {"action": "locked", "source": "manual"},
    2: {"action": "unlocked", "source": "manual"},
    3: {"action": "locked", "source": "rf"},
    4: {"action": "unlocked", "source": "rf"},
    5: {"action": "locked", "source": "keypad"},
    6: {"action": "unlocked", "source": "keypad"},
    9: {"action": "locked", "source": "auto"},
    11: {"action": "jammed", "source": "manual"},
}

# Z-Wave Notification command class number for Access Control events.
NOTIFICATION_COMMAND_CLASS = 113


def map_access_control_event(event_code: int) -> Optional[Dict[str, str]]:
    """Map a Z-Wave Access Control event code to action/source.

    - Description: Translate a Kwikset Access Control event code into the
      ``{"action", "source"}`` dict used by the access log.
    - Inputs: event_code (int) — the ``event`` field of the notification.
    - Outputs: dict with "action" and "source", or None if unrecognized.
    - Example: ``map_access_control_event(6)`` ->
      ``{"action": "unlocked", "source": "keypad"}``
    """
    mapping = ACCESS_CONTROL_EVENT_MAP.get(event_code)
    return dict(mapping) if mapping else None


def _resolve_lock_for_node(
    hass: HomeAssistant, node_id: Optional[int]
) -> Optional[SmartLockManagerLock]:
    """Find the SLM-managed lock whose Z-Wave node matches ``node_id``.

    - Description: SLM never stores node_id on the lock object, so resolve
      each managed lock's entity_id to its Z-Wave node and compare node_id.
    - Inputs: node_id (int) from the notification event data.
    - Outputs: matching SmartLockManagerLock or None.
    """
    if node_id is None:
        return None

    dev_mock = is_dev_mock()
    if not dev_mock:
        try:
            from homeassistant.components.zwave_js.helpers import (
                async_get_node_from_entity_id,
            )
        except Exception:  # pragma: no cover - zwave_js always present in prod
            return None

    for entry_data in hass.data.get(DOMAIN, {}).values():
        if not isinstance(entry_data, dict):
            continue
        lock: Optional[SmartLockManagerLock] = entry_data.get(PRIMARY_LOCK)
        if not lock or not lock.lock_entity_id:
            continue
        try:
            if dev_mock:
                # DEV: resolve via the seeded entity->node_id table.
                node = mock_node_for_entity(lock.lock_entity_id)
            else:
                # async_get_node_from_entity_id is @callback (sync) — do NOT await
                node = async_get_node_from_entity_id(hass, lock.lock_entity_id)
        except Exception:
            node = None
        if node is not None and getattr(node, "node_id", None) == node_id:
            return lock

    return None


def _build_access_log_handler(hass: HomeAssistant) -> Callable:
    """Create the ``zwave_js_notification`` event handler for the access log.

    - Description: Returns an async listener that records lock/unlock/jam
      events (with user attribution for keypad events) on the matching SLM
      lock's bounded access log, then persists the lock data.
    - Inputs: hass (HomeAssistant).
    - Outputs: an async callable suitable for ``hass.bus.async_listen``.

    SECURITY: only user_name + slot number are logged — never PIN codes.
    """

    async def _handle_zwave_notification(event: Any) -> None:
        data = event.data or {}

        # Only Access Control (door lock) notifications carry lock events.
        if data.get("command_class") != NOTIFICATION_COMMAND_CLASS:
            return

        event_code = data.get("event")
        if not isinstance(event_code, int):
            return

        mapping = map_access_control_event(event_code)
        if not mapping:
            _LOGGER.debug(
                "Access log: ignoring unmapped Access Control event %s", event_code
            )
            return

        # Resolve via the package-root module attribute so tests that patch
        # ``custom_components.smart_lock_manager._resolve_lock_for_node`` (the
        # frozen patch target, from when this lived in __init__) still take
        # effect. Falls back to the local definition if the re-export is absent.
        import custom_components.smart_lock_manager as _pkg

        _resolve = getattr(_pkg, "_resolve_lock_for_node", _resolve_lock_for_node)
        lock = _resolve(hass, data.get("node_id"))
        if not lock:
            _LOGGER.debug(
                "Access log: no SLM lock matches node_id %s", data.get("node_id")
            )
            return

        # Resolve user attribution for keypad events via parameters.userId.
        user_name: Optional[str] = None
        slot: Optional[int] = None
        if mapping["source"] == "keypad":
            params = data.get("parameters") or {}
            raw_slot = params.get("userId")
            if isinstance(raw_slot, int):
                slot = raw_slot
                slot_obj = lock.code_slots.get(slot)
                user_name = (
                    slot_obj.user_name
                    if slot_obj and slot_obj.user_name
                    else f"slot {slot}"
                )

        entry = lock.add_access_log_entry(
            action=mapping["action"],
            source=mapping["source"],
            user_name=user_name,
            slot=slot,
        )
        _LOGGER.info(
            "Access log [%s]: %s via %s%s",
            lock.lock_name,
            entry["action"],
            entry["source"],
            f" by {user_name} (slot {slot})" if user_name else "",
        )

        # Persist the updated access log. Find this lock's entry_id to save.
        # Lazy import to avoid a circular import with the package __init__.
        from .. import _save_lock_data

        for entry_id, entry_data in hass.data.get(DOMAIN, {}).items():
            if isinstance(entry_data, dict) and entry_data.get(PRIMARY_LOCK) is lock:
                await _save_lock_data(hass, lock, entry_id)
                break

    return _handle_zwave_notification
