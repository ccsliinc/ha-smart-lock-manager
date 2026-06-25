"""Tests for SLM alert actor attribution (who/what triggered an alert)."""

from datetime import datetime, timedelta
from unittest.mock import Mock

from custom_components.smart_lock_manager.const import DOMAIN, PRIMARY_LOCK
from custom_components.smart_lock_manager.models.lock import (
    CodeSlot,
    SmartLockManagerLock,
)
from custom_components.smart_lock_manager.notifications_html import render_alert_html
from custom_components.smart_lock_manager.services.access_log import (
    _attribute_actor,
    format_actor,
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


def _make_hass(lock) -> Mock:
    """Build a hass mock exposing one SLM lock at find_lock's expected path."""
    hass = Mock()
    hass.data = {DOMAIN: {"e1": {PRIMARY_LOCK: lock, "store": Mock()}}}
    return hass


def _record(alert_type="sustained_unlock", timestamp=None) -> dict:
    """Build a minimal alert record dict for _attribute_actor."""
    return {
        "member_entity_id": "lock.front_door",
        "timestamp": (timestamp or datetime.now()).isoformat(),
        "alert_type": alert_type,
    }


class TestFormatActor:
    """format_actor maps an access-log entry to a human label."""

    def test_keypad_user_and_slot(self):
        """Keypad with user_name + slot reads 'Name (keypad, slot N)'."""
        entry = {"source": "keypad", "user_name": "Theresa", "slot": 2}
        assert format_actor(entry) == "Theresa (keypad, slot 2)"

    def test_keypad_user_no_slot(self):
        """Keypad with user_name but no slot drops the slot clause."""
        entry = {"source": "keypad", "user_name": "Theresa", "slot": None}
        assert format_actor(entry) == "Theresa (keypad)"

    def test_keypad_no_user_with_slot(self):
        """Keypad with no user_name but a slot reads 'keypad (slot N)'."""
        entry = {"source": "keypad", "user_name": None, "slot": 7}
        assert format_actor(entry) == "keypad (slot 7)"

    def test_keypad_no_user_no_slot(self):
        """Keypad with neither user_name nor slot reads 'keypad'."""
        entry = {"source": "keypad"}
        assert format_actor(entry) == "keypad"

    def test_rf_source(self):
        """RF source reads the app/HA label."""
        assert format_actor({"source": "rf"}) == "app / Home Assistant (RF)"

    def test_auto_source(self):
        """Auto source reads 'auto-lock'."""
        assert format_actor({"source": "auto"}) == "auto-lock"

    def test_manual_source(self):
        """Manual source reads 'manual (thumbturn)'."""
        assert format_actor({"source": "manual"}) == "manual (thumbturn)"

    def test_missing_source_no_none(self):
        """A missing source never renders 'None' and falls back sanely."""
        assert format_actor({}) == "unknown"
        # Falsy source but a present action falls back to the action label.
        assert format_actor({"source": None, "action": "jammed"}) == "jammed"


class TestAttributeActor:
    """_attribute_actor joins an alert to a recent access-log entry."""

    def test_keypad_unlock_attributed(self):
        """A recent keypad unlock is credited to a sustained_unlock alert."""
        lock = _make_lock()
        lock.add_access_log_entry("unlocked", "keypad", user_name="Theresa", slot=2)
        hass = _make_hass(lock)
        record = _record("sustained_unlock")

        _attribute_actor(record, hass, "sustained_unlock", is_recovery=False)
        assert record["actor"] == "Theresa (keypad, slot 2)"

    def test_rf_unlock_method_only(self):
        """An RF unlock yields a method-only actor (no fabricated user)."""
        lock = _make_lock()
        lock.add_access_log_entry("unlocked", "rf")
        hass = _make_hass(lock)
        record = _record("outside_hours")

        _attribute_actor(record, hass, "outside_hours", is_recovery=False)
        assert record["actor"] == "app / Home Assistant (RF)"

    def test_manual_lock_recovery_method_only(self):
        """A manual relock attributes the recovery to the thumbturn."""
        lock = _make_lock()
        lock.add_access_log_entry("locked", "manual")
        hass = _make_hass(lock)
        record = _record("sustained_unlock")

        _attribute_actor(record, hass, "sustained_unlock", is_recovery=True)
        assert record["actor"] == "manual (thumbturn)"

    def test_auto_lock_attributed(self):
        """An auto-lock entry is credited to an auto_lock_failed alert."""
        lock = _make_lock()
        lock.add_access_log_entry("locked", "auto")
        hass = _make_hass(lock)
        record = _record("auto_lock_failed")

        _attribute_actor(record, hass, "auto_lock_failed", is_recovery=False)
        assert record["actor"] == "auto-lock"

    def test_out_of_window_entry_not_attributed(self):
        """An access-log entry outside the match window is not credited."""
        lock = _make_lock()
        old = (datetime.now() - timedelta(seconds=60)).isoformat()
        lock.access_log.append(
            {
                "timestamp": old,
                "action": "unlocked",
                "source": "keypad",
                "user_name": "Theresa",
                "slot": 2,
            }
        )
        hass = _make_hass(lock)
        record = _record("sustained_unlock")

        _attribute_actor(record, hass, "sustained_unlock", is_recovery=False)
        assert "actor" not in record

    def test_empty_access_log_not_attributed(self):
        """No access-log entries leaves actor unset."""
        lock = _make_lock()
        hass = _make_hass(lock)
        record = _record("sustained_unlock")

        _attribute_actor(record, hass, "sustained_unlock", is_recovery=False)
        assert "actor" not in record

    def test_non_actor_alert_type_skipped(self):
        """A non-actor-driven alert type (low_battery) is never attributed."""
        lock = _make_lock()
        lock.add_access_log_entry("unlocked", "keypad", user_name="Theresa", slot=2)
        hass = _make_hass(lock)
        record = _record("low_battery")

        _attribute_actor(record, hass, "low_battery", is_recovery=False)
        assert "actor" not in record


class TestRenderActorHtml:
    """The HTML card renders the triggered-by line only when an actor is set."""

    def test_html_includes_actor(self):
        """An actor renders a 'Triggered by:' block carrying the name."""
        html = render_alert_html(
            severity="WARN",
            subject="Door left unlocked",
            body_lines=["Front Door has been unlocked for 5 minutes."],
            actor="Theresa (keypad, slot 2)",
        )
        assert "Triggered by:" in html
        assert "Theresa" in html

    def test_html_omits_actor_when_absent(self):
        """No actor omits the triggered-by line and never leaks 'None'."""
        html = render_alert_html(
            severity="WARN",
            subject="Door left unlocked",
            body_lines=["Front Door has been unlocked for 5 minutes."],
            actor=None,
        )
        assert "Triggered by:" not in html
        assert "None" not in html
