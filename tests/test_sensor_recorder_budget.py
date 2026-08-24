"""Tests for the sensor recorder budget (``_unrecorded_attributes``).

The summary sensor is a 30-second ``CoordinatorEntity`` carrying large,
frequently-rewritten attribute blobs. Excluding them from the recorder is only
safe if they remain LIVE in the state machine, because the panel and any
automation read them from there. These tests assert BOTH halves: the heavy
attributes are excluded from recording, and they are still emitted.

They also pin the attributes the panel consumes (``frontend/src/main.js`` and
``frontend/src/utils/presentation.js``) so a future exclusion cannot quietly
take one of them away.
"""

from __future__ import annotations

from datetime import datetime
from unittest.mock import Mock

import pytest
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from custom_components.smart_lock_manager.const import DOMAIN, PRIMARY_LOCK
from custom_components.smart_lock_manager.models.lock import (
    CodeSlot,
    SmartLockManagerLock,
)
from custom_components.smart_lock_manager.sensor import (
    SmartLockManagerSensor,
    SmartLockManagerZoneSensor,
)

# The attribute names the shipped panel reads off the lock sensor. Losing any of
# these breaks the UI, so they must stay present in extra_state_attributes.
PANEL_LOCK_ATTRIBUTES = {
    "slot_details",
    "access_log",
    "usage_stats",
    "zone_id",
    "active_codes_count",
    "configured_codes_count",
    "lock_name",
    "total_slots",
    "start_from",
}

# HA merges a class's _unrecorded_attributes up its MRO into this private name;
# it is what the recorder actually consults, so assert against it rather than
# the raw class attribute.
COMBINED = "_Entity__combined_unrecorded_attributes"


@pytest.fixture
def lock() -> SmartLockManagerLock:
    """Build a lock with a couple of populated slots and an access log entry."""
    lock = SmartLockManagerLock(
        lock_name="Test Lock",
        lock_entity_id="lock.test_lock",
        slots=10,
        start_from=1,
    )
    lock.code_slots[1] = CodeSlot(
        slot_number=1,
        pin_code="1234",
        user_name="Alice",
        is_active=True,
        created_at=datetime.now(),
    )
    lock.access_log = [{"event": "unlock", "slot": 1}]
    return lock


@pytest.fixture
def sensor(lock: SmartLockManagerLock) -> SmartLockManagerSensor:
    """Build a lock summary sensor wired to a mock hass."""
    hass = Mock(spec=HomeAssistant)
    hass.data = {DOMAIN: {}}
    entry = Mock(spec=ConfigEntry)
    entry.entry_id = "entry_recorder_budget"
    entry.data = {"lock_entity_id": "lock.test_lock", "lock_name": "Test Lock"}
    coordinator = Mock()
    coordinator.last_update_success = True
    hass.data[DOMAIN][entry.entry_id] = {
        PRIMARY_LOCK: lock,
        "coordinator": coordinator,
        "entry": entry,
    }
    return SmartLockManagerSensor(
        hass=hass, entry=entry, lock=lock, coordinator=coordinator
    )


def test_heavy_lock_attributes_are_excluded_from_the_recorder() -> None:
    """The two large, high-churn attributes are not persisted by the recorder."""
    combined = getattr(SmartLockManagerSensor, COMBINED)

    assert "slot_details" in combined
    assert "access_log" in combined


def test_heavy_lock_attributes_are_still_emitted_live(
    sensor: SmartLockManagerSensor, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Unrecorded is not unavailable: the panel still receives both blobs."""
    monkeypatch.setattr(
        SmartLockManagerSensor, "_get_owning_zone", lambda self, entity_id: None
    )
    attributes = sensor.extra_state_attributes

    assert attributes["slot_details"]["slot_1"]["user_name"] == "Alice"
    assert attributes["access_log"] == [{"event": "unlock", "slot": 1}]


def test_every_panel_attribute_is_still_present(
    sensor: SmartLockManagerSensor, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No attribute the shipped panel reads was dropped from the sensor."""
    monkeypatch.setattr(
        SmartLockManagerSensor, "_get_owning_zone", lambda self, entity_id: None
    )
    attributes = sensor.extra_state_attributes

    assert PANEL_LOCK_ATTRIBUTES <= set(attributes)


def test_automation_facing_counts_stay_recorded() -> None:
    """Small scalar attributes an automation would trigger on keep their history."""
    combined = getattr(SmartLockManagerSensor, COMBINED)

    for name in (
        "active_codes_count",
        "configured_codes_count",
        "valid_codes_count",
        "is_connected",
        "connection_status",
        "zone_id",
        "zone_name",
        "active_slots",
        "valid_slots_now",
        "usage_stats",
    ):
        assert name not in combined, f"{name} must remain recorded"


def test_slot_details_pins_are_never_emitted(
    sensor: SmartLockManagerSensor, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Excluding from the recorder does not relax the no-PIN rule."""
    monkeypatch.setattr(
        SmartLockManagerSensor, "_get_owning_zone", lambda self, entity_id: None
    )
    slot = sensor.extra_state_attributes["slot_details"]["slot_1"]

    assert slot["has_pin_code"] is True
    assert "pin_code" not in slot
    assert "1234" not in str(slot)


def test_zone_slot_summary_is_excluded_but_zone_identity_is_not() -> None:
    """The zone sensor drops its per-slot blob and keeps its identity fields."""
    combined = getattr(SmartLockManagerZoneSensor, COMBINED)

    assert "slot_summary" in combined
    for name in ("zone_id", "zone_name", "member_locks", "member_count"):
        assert name not in combined, f"{name} must remain recorded"
