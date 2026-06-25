"""Dev-only mock layer for the Smart Lock Manager integration.

This module stubs the THREE places SLM touches Z-Wave so the integration can
be exercised against DUMMY locks with NO real Z-Wave hardware. It is strictly
dev-gated: everything here is inert unless the ``SLM_DEV_MOCK`` environment
variable is truthy. In production (flag unset) ``is_dev_mock()`` returns False
and not a single mock code path is reached, so behavior is 100% unchanged.

The three intercepted touch-points (see ``services/zwave_services.py`` and
``__init__.py``):

1. Write/clear user codes — normally ``zwave_js.set_lock_usercode`` /
   ``clear_lock_usercode`` service calls. Mocked into ``MockValueDB``.
2. Node lookup + cached code read — normally
   ``async_get_node_from_entity_id`` + ``get_usercode``. Mocked into
   ``mock_node_for_entity`` + ``mock_get_usercode``.
3. Access-log event — normally a real ``zwave_js_notification`` bus event.
   Mocked via ``fire_mock_notification`` which fires a correctly-shaped event.

SECURITY: only obviously-fake sample PINs ever flow through here in dev. The
mock never logs PIN values.
"""

from __future__ import annotations

import logging
import os
from threading import Lock
from types import SimpleNamespace
from typing import Any, Dict, Optional, Tuple

_LOGGER = logging.getLogger(__name__)

# Environment flag that gates ALL behavior in this module.
_DEV_MOCK_ENV = "SLM_DEV_MOCK"

# Z-Wave Notification command class number for Access Control events.
# Mirrors ``NOTIFICATION_COMMAND_CLASS`` in ``__init__.py`` so a mock
# notification is shaped exactly like a real Kwikset Access Control event.
_NOTIFICATION_COMMAND_CLASS = 113

# Dummy entity_id -> Z-Wave node_id table for the dev locks. Seven generic
# demo locks for the dev-mock harness.
ENTITY_TO_NODE_ID: Dict[str, int] = {
    "lock.demo_front": 10,
    "lock.demo_back": 11,
    "lock.demo_side": 12,
    "lock.demo_office": 13,
    "lock.demo_garage": 14,
    "lock.demo_unit_a": 15,
    "lock.demo_unit_b": 16,
}


def is_dev_mock() -> bool:
    """Return whether dev-mock mode is enabled.

    - Description: Read the ``SLM_DEV_MOCK`` env var and interpret it as a
      boolean. Truthy values are ``1``, ``true``, ``yes``, ``on`` (any case).
    - Inputs: none (reads process environment).
    - Outputs: bool — True only when the flag is explicitly truthy.
    - Example: with ``SLM_DEV_MOCK=1`` set, ``is_dev_mock()`` -> True.
    """
    raw = os.environ.get(_DEV_MOCK_ENV, "")
    return raw.strip().lower() in ("1", "true", "yes", "on")


class MockValueDB:
    """In-memory stand-in for the Z-Wave JS ValueDB, keyed by (node_id, slot).

    Stores the PIN written to each (node_id, slot) so reads round-trip the same
    way the real cached ValueDB does. Supports failure injection so the sad
    path (supervision failure) can be tested deterministically.
    """

    def __init__(self) -> None:
        """Initialize an empty, thread-safe code store."""
        self._codes: Dict[Tuple[int, int], str] = {}
        self._lock = Lock()
        # When True, the NEXT write/clear raises to simulate a Z-Wave failure.
        self._fail_next = False

    def set_usercode(self, node_id: int, slot: int, usercode: str) -> None:
        """Store a PIN for (node_id, slot), honoring failure injection.

        - Inputs: node_id (int), slot (int), usercode (str numeric PIN).
        - Outputs: None.
        - Raises: RuntimeError if ``fail_next`` was armed (consumes the toggle).
        """
        with self._lock:
            if self._fail_next:
                self._fail_next = False
                raise RuntimeError(
                    f"Mock Z-Wave supervision failure writing slot {slot} "
                    f"on node {node_id}"
                )
            self._codes[(node_id, slot)] = usercode
        _LOGGER.debug("MockValueDB: set node %s slot %s", node_id, slot)

    def clear_usercode(self, node_id: int, slot: int) -> None:
        """Remove the PIN for (node_id, slot), honoring failure injection.

        - Inputs: node_id (int), slot (int).
        - Outputs: None.
        - Raises: RuntimeError if ``fail_next`` was armed (consumes the toggle).
        """
        with self._lock:
            if self._fail_next:
                self._fail_next = False
                raise RuntimeError(
                    f"Mock Z-Wave supervision failure clearing slot {slot} "
                    f"on node {node_id}"
                )
            self._codes.pop((node_id, slot), None)
        _LOGGER.debug("MockValueDB: cleared node %s slot %s", node_id, slot)

    def get_usercode(self, node_id: int, slot: int) -> Optional[str]:
        """Return the stored PIN for (node_id, slot), or None if empty.

        - Inputs: node_id (int), slot (int).
        - Outputs: the stored PIN string, or None.
        """
        with self._lock:
            return self._codes.get((node_id, slot))

    def fail_next(self, enabled: bool = True) -> None:
        """Arm/disarm a one-shot failure on the next write or clear.

        - Inputs: enabled (bool) — True to arm, False to disarm.
        - Outputs: None.
        - Example: ``MOCK_DB.fail_next()`` then a set/clear raises once.
        """
        with self._lock:
            self._fail_next = enabled


# Single process-wide mock store. SLM runs one HA process per dev instance.
MOCK_DB = MockValueDB()


class MockBoltStatus:
    """In-memory stand-in for Door Lock CC (98) ``boltStatus`` reads.

    The production auto-lock verify path reads ``boltStatus`` via
    ``zwave_js.invoke_cc_api``. There is no real Z-Wave in dev, so the
    :class:`~..auto_lock.AutoLockEngine` reads boltStatus from THIS registry
    instead (gated by ``is_dev_mock()``). By default a lock's boltStatus tracks
    its HA entity state (the engine passes the live state in), but a per-entity
    OVERRIDE can be set to force a verify-failure (``"unlocked"``) so the
    retry + CRIT-alert path is exercised deterministically.
    """

    def __init__(self) -> None:
        """Initialize an empty, thread-safe boltStatus override store."""
        self._overrides: Dict[str, str] = {}
        self._lock = Lock()

    def set_override(self, entity_id: str, bolt_status: Optional[str]) -> None:
        """Force (or clear) the boltStatus a verify read returns for a lock.

        - Inputs: entity_id (str lock entity id), bolt_status (str like
          ``"unlocked"`` / ``"locked"``, or None to clear the override).
        - Outputs: None.
        - Example: ``MOCK_BOLT.set_override("lock.demo_back", "unlocked")`` makes the
          next verify read fail so the engine retries.
        """
        with self._lock:
            if bolt_status is None:
                self._overrides.pop(entity_id, None)
            else:
                self._overrides[entity_id] = str(bolt_status).strip().lower()

    def read(self, entity_id: str, entity_state: Optional[str]) -> Optional[str]:
        """Return the boltStatus the verify path should see for a lock.

        - Description: An explicit override wins; otherwise boltStatus is
          DERIVED from the live entity state — ``"locked"`` -> ``"locked"``
          (bolt thrown), anything else -> ``"unlocked"`` (not thrown). This
          mirrors how a real Kwikset reports boltStatus tracking the bolt.
        - Inputs: entity_id (str), entity_state (str|None live HA lock state).
        - Outputs: str lowercased boltStatus, or None if nothing is known.
        """
        with self._lock:
            if entity_id in self._overrides:
                return self._overrides[entity_id]
        if entity_state is None:
            return None
        return "locked" if str(entity_state).strip().lower() == "locked" else "unlocked"


# Single process-wide boltStatus mock. Mirrors MOCK_DB's lifetime/scope.
MOCK_BOLT = MockBoltStatus()


def mock_get_usercode(node: Any, slot: int) -> Dict[str, Any]:
    """Mock of ``zwave_js_server.util.lock.get_usercode``.

    - Description: Return a dict in the SAME shape the real ``get_usercode``
      returns (keys: ``code_slot``, ``name``, ``in_use``, ``usercode``) so
      every existing caller in SLM works unchanged.
    - Inputs: node (object with a ``node_id`` attr), slot (int).
    - Outputs: dict mirroring the real CodeSlot read result.
    - Example: ``mock_get_usercode(SimpleNamespace(node_id=10), 1)``.
    """
    node_id = getattr(node, "node_id", None)
    code = MOCK_DB.get_usercode(node_id, slot) if node_id is not None else None
    return {
        "code_slot": slot,
        "name": f"Code Slot {slot}",
        "in_use": code is not None,
        "usercode": code,
    }


def mock_node_for_entity(entity_id: str) -> Optional[SimpleNamespace]:
    """Mock of ``async_get_node_from_entity_id`` for the dev dummy locks.

    - Description: Resolve a dummy lock entity_id to a fake node object
      carrying the seeded ``node_id``, using ``ENTITY_TO_NODE_ID``.
    - Inputs: entity_id (str), e.g. ``"lock.demo_front"``.
    - Outputs: ``SimpleNamespace(node_id=...)`` or None if the entity is not a
      known dev lock.
    - Example: ``mock_node_for_entity("lock.demo_back").node_id`` -> 11.
    """
    node_id = ENTITY_TO_NODE_ID.get(entity_id)
    if node_id is None:
        return None
    return SimpleNamespace(node_id=node_id)


def dev_inject_sync_error(
    hass: Any, entity_id: str, code_slot: int, message: Optional[str] = None
) -> bool:
    """Force a member lock's CodeSlot into a hard sync-error state (dev only).

    - Description: DEV-ONLY entrypoint (called from the ``dev_inject_sync_error``
      service) that drives a member lock's slot into the same hard-failure state
      a real Z-Wave supervision failure would leave it in, so the LIVE zone
      sync-status derivation (:func:`api.zones._derive_slot_sync`) reports the
      slot as ``error`` and the panel raises its warning banner. It sets the
      member slot's ``sync_error`` and marks it unsynced for an IMMEDIATE effect
      on the next ``/zones`` read, and ALSO arms :data:`MOCK_DB` ``fail_next`` so
      the very next coordinator write to any node raises — exercising the real
      error-recording path end-to-end. The caller must already have verified
      ``is_dev_mock()``.
    - Inputs:
        hass: HomeAssistant instance.
        entity_id: target member lock entity id.
        code_slot: slot number to fault.
        message: optional sync_error detail (defaults to a generic dev message).
    - Outputs: True if the member slot was found and faulted, else False.
    """
    from .const import DOMAIN, PRIMARY_LOCK

    detail = message or "Dev-injected sync error"
    for entry_data in hass.data.get(DOMAIN, {}).values():
        if not isinstance(entry_data, dict):
            continue
        lock = entry_data.get(PRIMARY_LOCK)
        if lock is None or getattr(lock, "lock_entity_id", None) != entity_id:
            continue
        slot = lock.code_slots.get(code_slot)
        if slot is None:
            return False
        slot.is_synced = False
        slot.sync_error = detail
        # Arm the mock ValueDB so the NEXT real coordinator write also fails,
        # proving the live error-recording path (not just the injected flag).
        MOCK_DB.fail_next(True)
        _LOGGER.info("DEV: injected sync error on %s slot %s", entity_id, code_slot)
        return True
    return False


def fire_mock_notification(
    hass: Any, node_id: int, event_code: int, user_id: Optional[int] = None
) -> None:
    """Fire a correctly-shaped ``zwave_js_notification`` onto the HA bus.

    - Description: Drive the REAL access-log handler end-to-end without Z-Wave
      hardware by firing a notification event matching what zwave_js emits for
      a Kwikset Access Control (command class 113) event.
    - Inputs:
        hass: HomeAssistant instance.
        node_id: Z-Wave node id of the dummy lock (see ENTITY_TO_NODE_ID).
        event_code: Access Control event code (e.g. 6 = keypad unlock).
        user_id: SLM code slot for keypad events (codes 5/6); else None.
    - Outputs: None (fires onto ``hass.bus``).
    - Example: ``fire_mock_notification(hass, 10, 6, user_id=1)``.
    """
    data: Dict[str, Any] = {
        "command_class": _NOTIFICATION_COMMAND_CLASS,
        "command_class_name": "Notification",
        "node_id": node_id,
        "type": 6,
        "event": event_code,
        "label": "Access Control",
    }
    if user_id is not None:
        data["parameters"] = {"userId": user_id}

    hass.bus.async_fire("zwave_js_notification", data)
    _LOGGER.info(
        "Mock fired zwave_js_notification: node=%s event=%s user_id=%s",
        node_id,
        event_code,
        user_id,
    )
