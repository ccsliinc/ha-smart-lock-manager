"""Tests for the Smart Lock Manager access log (lock/unlock event history)."""

from unittest.mock import AsyncMock, Mock, patch

import pytest

from custom_components.smart_lock_manager import (
    _build_access_log_handler,
    map_access_control_event,
)
from custom_components.smart_lock_manager.const import DOMAIN, PRIMARY_LOCK
from custom_components.smart_lock_manager.models.lock import (
    ACCESS_LOG_MAX_ENTRIES,
    CodeSlot,
    SmartLockManagerLock,
)


def _make_lock() -> SmartLockManagerLock:
    """Build a lock with slot 1 = 'Joe' for attribution tests."""
    lock = SmartLockManagerLock(
        lock_name="Front Door",
        lock_entity_id="lock.front_door",
        slots=10,
    )
    lock.code_slots[1] = CodeSlot(slot_number=1, pin_code="1234", user_name="Joe")
    return lock


def _make_event(node_id=5, event_code=6, parameters=None):
    """Build a fake zwave_js_notification event object."""
    event = Mock()
    event.data = {
        "domain": "zwave_js",
        "node_id": node_id,
        "command_class": 113,
        "command_class_name": "Notification",
        "event": event_code,
        "parameters": parameters or {},
    }
    return event


def _make_hass(lock):
    """Build a hass mock with one SLM lock registered."""
    hass = Mock()
    hass.data = {DOMAIN: {"entry_1": {PRIMARY_LOCK: lock, "store": Mock()}}}
    hass.data[DOMAIN]["entry_1"]["store"].async_save = AsyncMock()
    return hass


class TestEventMapping:
    """Pure Access Control event-code mapping."""

    def test_keypad_unlock_maps_to_keypad_source(self):
        """Event 6 maps to unlocked via keypad."""
        assert map_access_control_event(6) == {
            "action": "unlocked",
            "source": "keypad",
        }

    def test_manual_lock_maps_to_manual_source(self):
        """Event 1 maps to locked via manual thumbturn."""
        assert map_access_control_event(1) == {
            "action": "locked",
            "source": "manual",
        }

    def test_rf_unlock_maps_to_rf_source(self):
        """Event 4 maps to unlocked via RF (app/HA)."""
        assert map_access_control_event(4) == {
            "action": "unlocked",
            "source": "rf",
        }

    def test_auto_lock_maps_to_auto_source(self):
        """Event 9 maps to locked via auto-lock."""
        assert map_access_control_event(9) == {
            "action": "locked",
            "source": "auto",
        }

    def test_jammed_maps_to_jammed_action(self):
        """Event 11 maps to the jammed action."""
        assert map_access_control_event(11)["action"] == "jammed"

    def test_unknown_event_returns_none(self):
        """Unrecognized event codes return None."""
        assert map_access_control_event(99) is None


class TestAccessLogBounding:
    """The access log must stay bounded to ACCESS_LOG_MAX_ENTRIES."""

    def test_bounds_to_max_entries(self):
        """The log never exceeds ACCESS_LOG_MAX_ENTRIES."""
        lock = _make_lock()
        for i in range(ACCESS_LOG_MAX_ENTRIES + 50):
            lock.add_access_log_entry("unlocked", "rf")
        assert len(lock.access_log) == ACCESS_LOG_MAX_ENTRIES

    def test_oldest_dropped_first(self):
        """Oldest entries are dropped when the cap is exceeded."""
        lock = _make_lock()
        for i in range(ACCESS_LOG_MAX_ENTRIES):
            lock.add_access_log_entry("unlocked", "keypad", user_name=f"u{i}", slot=i)
        # One more push beyond the cap drops the oldest (u0).
        lock.add_access_log_entry("locked", "manual")
        assert len(lock.access_log) == ACCESS_LOG_MAX_ENTRIES
        assert lock.access_log[0]["user_name"] == "u1"
        assert lock.access_log[-1]["action"] == "locked"

    def test_entry_never_contains_pin(self):
        """Access-log entries must never carry a PIN code."""
        lock = _make_lock()
        entry = lock.add_access_log_entry("unlocked", "keypad", "Joe", 1)
        assert "pin_code" not in entry
        assert "1234" not in str(entry)


class TestNotificationHandler:
    """End-to-end handler behavior against a mocked hass."""

    @pytest.mark.asyncio
    async def test_keypad_unlock_resolves_user_name(self):
        """Keypad unlock (event 6) resolves userId to the slot's user_name."""
        lock = _make_lock()
        hass = _make_hass(lock)
        handler = _build_access_log_handler(hass)
        event = _make_event(event_code=6, parameters={"userId": 1})

        with patch(
            "custom_components.smart_lock_manager._resolve_lock_for_node",
            return_value=lock,
        ):
            await handler(event)

        assert len(lock.access_log) == 1
        entry = lock.access_log[0]
        assert entry["action"] == "unlocked"
        assert entry["source"] == "keypad"
        assert entry["user_name"] == "Joe"
        assert entry["slot"] == 1
        hass.data[DOMAIN]["entry_1"]["store"].async_save.assert_awaited()

    @pytest.mark.asyncio
    async def test_keypad_unknown_slot_falls_back_to_slot_label(self):
        """A keypad event for an unnamed slot falls back to 'slot N'."""
        lock = _make_lock()
        hass = _make_hass(lock)
        handler = _build_access_log_handler(hass)
        event = _make_event(event_code=5, parameters={"userId": 7})

        with patch(
            "custom_components.smart_lock_manager._resolve_lock_for_node",
            return_value=lock,
        ):
            await handler(event)

        entry = lock.access_log[0]
        assert entry["user_name"] == "slot 7"
        assert entry["slot"] == 7

    @pytest.mark.asyncio
    async def test_manual_event_logs_no_user(self):
        """A manual thumbturn event logs no user attribution."""
        lock = _make_lock()
        hass = _make_hass(lock)
        handler = _build_access_log_handler(hass)
        event = _make_event(event_code=1)  # manual lock (thumbturn)

        with patch(
            "custom_components.smart_lock_manager._resolve_lock_for_node",
            return_value=lock,
        ):
            await handler(event)

        entry = lock.access_log[0]
        assert entry["action"] == "locked"
        assert entry["source"] == "manual"
        assert entry["user_name"] is None
        assert entry["slot"] is None

    @pytest.mark.asyncio
    async def test_jammed_event_captured(self):
        """A lock-jammed event (11) is recorded with the jammed action."""
        lock = _make_lock()
        hass = _make_hass(lock)
        handler = _build_access_log_handler(hass)
        event = _make_event(event_code=11)

        with patch(
            "custom_components.smart_lock_manager._resolve_lock_for_node",
            return_value=lock,
        ):
            await handler(event)

        assert lock.access_log[0]["action"] == "jammed"

    @pytest.mark.asyncio
    async def test_non_notification_command_class_ignored(self):
        """Notifications outside command class 113 are ignored."""
        lock = _make_lock()
        hass = _make_hass(lock)
        handler = _build_access_log_handler(hass)
        event = _make_event(event_code=6)
        event.data["command_class"] = 98  # not Notification

        with patch(
            "custom_components.smart_lock_manager._resolve_lock_for_node",
            return_value=lock,
        ):
            await handler(event)

        assert lock.access_log == []

    @pytest.mark.asyncio
    async def test_unmatched_node_logs_nothing(self):
        """An event from an unmatched node_id records nothing."""
        lock = _make_lock()
        hass = _make_hass(lock)
        handler = _build_access_log_handler(hass)
        event = _make_event(node_id=999, event_code=6, parameters={"userId": 1})

        with patch(
            "custom_components.smart_lock_manager._resolve_lock_for_node",
            return_value=None,
        ):
            await handler(event)

        assert lock.access_log == []
