"""Regression tests for the six P0 lock-safety fixes (2026.8.1).

Each test proves a specific "a revoked/cleared/expired/out-of-window PIN can stay
live on the deadbolt" bug is fixed and cannot silently regress. The desired
physical presence of a code is defined as ``is_active AND pin_code AND
is_valid_now()``; removal from the lock is driven by desired-absent, and a slot
is never nulled/deleted until a physical clear is CONFIRMED.

Hardware is exercised through the dev-mock MockValueDB (``SLM_DEV_MOCK=1``) so
these assert against the ACTUAL mock lock state, not just the in-memory model.
"""

from __future__ import annotations

import logging
import os
from unittest.mock import AsyncMock, Mock

import pytest
from freezegun import freeze_time
from homeassistant.core import HomeAssistant, ServiceCall

from custom_components.smart_lock_manager.const import DOMAIN, PRIMARY_LOCK
from custom_components.smart_lock_manager.dev_mock import MOCK_DB
from custom_components.smart_lock_manager.models.lock import SmartLockManagerLock

# A dev-mock demo lock: entity -> node_id 10 (see dev_mock.ENTITY_TO_NODE_ID).
DEMO_ENTITY = "lock.demo_front"
DEMO_NODE = 10
ENTRY_ID = "regression_entry"


@pytest.fixture(autouse=True)
def _reset_mock_db():
    """Reset the process-global MockValueDB before and after each test."""
    MOCK_DB._codes.clear()
    MOCK_DB.fail_next(False)
    yield
    MOCK_DB._codes.clear()
    MOCK_DB.fail_next(False)


@pytest.fixture
def dev_mock_env():
    """Enable SLM_DEV_MOCK for the duration of a test."""
    prev = os.environ.get("SLM_DEV_MOCK")
    os.environ["SLM_DEV_MOCK"] = "1"
    yield
    if prev is None:
        os.environ.pop("SLM_DEV_MOCK", None)
    else:
        os.environ["SLM_DEV_MOCK"] = prev


def _make_hass() -> Mock:
    """Build a minimal hass double with data/services/bus/states."""
    hass = Mock(spec=HomeAssistant)
    hass.data = {DOMAIN: {}}
    hass.services = Mock()
    hass.services.async_call = AsyncMock()
    hass.bus = Mock()
    hass.bus.async_fire = Mock()
    hass.states = Mock()
    hass.states.get = Mock(return_value=Mock(state="locked"))
    return hass


def _demo_lock(**slot_overrides) -> SmartLockManagerLock:
    """Build a 10-slot demo lock bound to the dev-mock node."""
    return SmartLockManagerLock(
        lock_name="Demo Front",
        lock_entity_id=DEMO_ENTITY,
        slots=10,
        start_from=1,
    )


def _seed_hass(hass: Mock, lock: SmartLockManagerLock) -> None:
    """Register the lock + a store double under a config entry."""
    store = Mock()
    store.async_save = AsyncMock()
    hass.data[DOMAIN][ENTRY_ID] = {
        PRIMARY_LOCK: lock,
        "store": store,
        "coordinator": Mock(),
        "entry": Mock(entry_id=ENTRY_ID),
    }


def _call(data) -> ServiceCall:
    """Build a ServiceCall double with the given payload."""
    call = Mock(spec=ServiceCall)
    call.data = data
    return call


# ---------------------------------------------------------------------------
# Fix 1: clear_all_slots physically removes codes from the lock
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_clear_all_slots_removes_codes_from_lock(dev_mock_env):
    """clear_all_slots must actually clear the deadbolt, not just the model."""
    from custom_components.smart_lock_manager.services.management_services import (
        ManagementServices,
    )

    hass = _make_hass()
    lock = _demo_lock()
    lock.set_code(1, "1111", "A")
    lock.set_code(2, "2222", "B")
    _seed_hass(hass, lock)
    # Both codes live on the physical (mock) lock.
    MOCK_DB.set_usercode(DEMO_NODE, 1, "1111")
    MOCK_DB.set_usercode(DEMO_NODE, 2, "2222")

    await ManagementServices.clear_all_slots(hass, _call({"entity_id": DEMO_ENTITY}))

    # The deadbolt no longer accepts either code.
    assert MOCK_DB.get_usercode(DEMO_NODE, 1) is None
    assert MOCK_DB.get_usercode(DEMO_NODE, 2) is None
    # And the in-memory model is finalized only after the confirmed clear.
    assert lock.code_slots[1].pin_code is None
    assert lock.code_slots[2].pin_code is None
    assert lock.code_slots[1].is_active is False


@pytest.mark.asyncio
async def test_clear_all_slots_failed_clear_retains_pin(dev_mock_env):
    """A failed physical clear in clear_all keeps the PIN + flags unsynced."""
    from custom_components.smart_lock_manager.services.management_services import (
        ManagementServices,
    )

    hass = _make_hass()
    lock = _demo_lock()
    lock.set_code(1, "1111", "A")
    _seed_hass(hass, lock)
    MOCK_DB.set_usercode(DEMO_NODE, 1, "1111")
    MOCK_DB.fail_next(True)  # next clear raises

    await ManagementServices.clear_all_slots(hass, _call({"entity_id": DEMO_ENTITY}))

    # Clear failed: code still on the lock, PIN retained, marked unsynced.
    assert MOCK_DB.get_usercode(DEMO_NODE, 1) == "1111"
    assert lock.code_slots[1].pin_code == "1111"
    assert lock.code_slots[1].is_synced is False
    assert lock.code_slots[1].is_active is False
    assert lock.code_slots[1].sync_error


# ---------------------------------------------------------------------------
# Fix 2: clear_code on physical-clear FAILURE does not false-green
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_clear_code_failed_physical_clear_retries(dev_mock_env):
    """A failed clear must keep the PIN, stay unsynced, and land in remove set."""
    from custom_components.smart_lock_manager.services.lock_services import (
        LockServices,
    )

    hass = _make_hass()
    lock = _demo_lock()
    lock.set_code(1, "1111", "A")
    _seed_hass(hass, lock)
    MOCK_DB.set_usercode(DEMO_NODE, 1, "1111")
    MOCK_DB.fail_next(True)

    await LockServices.clear_code(
        hass, _call({"entity_id": DEMO_ENTITY, "code_slot": 1})
    )

    slot = lock.code_slots[1]
    # No green empty slot: PIN retained, unsynced, disabled, error recorded.
    assert slot.pin_code == "1111"
    assert slot.is_synced is False
    assert slot.is_active is False
    assert slot.sync_error
    # Code still physically present.
    assert MOCK_DB.get_usercode(DEMO_NODE, 1) == "1111"
    # The coordinator's remove path will RETRY it.
    actions = lock.get_slots_needing_sync({1: {"code": "1111"}})
    assert 1 in actions["remove"]


@pytest.mark.asyncio
async def test_clear_code_success_finalizes(dev_mock_env):
    """A confirmed clear nulls the PIN and empties the deadbolt."""
    from custom_components.smart_lock_manager.services.lock_services import (
        LockServices,
    )

    hass = _make_hass()
    lock = _demo_lock()
    lock.set_code(1, "1111", "A")
    _seed_hass(hass, lock)
    MOCK_DB.set_usercode(DEMO_NODE, 1, "1111")

    await LockServices.clear_code(
        hass, _call({"entity_id": DEMO_ENTITY, "code_slot": 1})
    )

    assert lock.code_slots[1].pin_code is None
    assert MOCK_DB.get_usercode(DEMO_NODE, 1) is None


# ---------------------------------------------------------------------------
# Fix 3: time-window enforcement drives add/remove on the hardware
# ---------------------------------------------------------------------------
def test_time_window_closed_marks_remove():
    """An active code outside its allowed_hours is desired-absent -> remove."""
    lock = SmartLockManagerLock(
        lock_name="T", lock_entity_id="lock.t", slots=5, start_from=1
    )
    lock.set_code(1, "1234", "A", allowed_hours=[9])  # only valid at 09:xx

    with freeze_time("2026-08-21 10:00:00"):
        actions = lock.get_slots_needing_sync({1: {"code": "1234"}})
    assert 1 in actions["remove"]
    assert 1 not in actions["add"]


def test_time_window_open_marks_add_then_stable():
    """When the window reopens the code re-adds, then stays once present."""
    lock = SmartLockManagerLock(
        lock_name="T", lock_entity_id="lock.t", slots=5, start_from=1
    )
    lock.set_code(1, "1234", "A", allowed_hours=[9])

    with freeze_time("2026-08-21 09:00:00"):
        # Lock currently empty -> must ADD.
        actions_add = lock.get_slots_needing_sync({})
        assert 1 in actions_add["add"]
        # Once the matching code is present -> neither add nor remove.
        actions_stable = lock.get_slots_needing_sync({1: {"code": "1234"}})
        assert 1 not in actions_stable["add"]
        assert 1 not in actions_stable["remove"]


# ---------------------------------------------------------------------------
# Fix 4: shrinking resize physically clears dropped slots before deletion
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_resize_shrink_clears_dropped_slot(dev_mock_env):
    """A dropped slot's code is cleared from the lock before the slot is deleted."""
    from custom_components.smart_lock_manager.services.slot_services import (
        SlotServices,
    )

    hass = _make_hass()
    lock = _demo_lock()
    lock.set_code(8, "8888", "High")  # slot 8 will be dropped by shrink to 5
    _seed_hass(hass, lock)
    MOCK_DB.set_usercode(DEMO_NODE, 8, "8888")

    await SlotServices.resize_slots(
        hass, _call({"entity_id": DEMO_ENTITY, "slot_count": 5})
    )

    # Physically cleared, then the slot object removed.
    assert MOCK_DB.get_usercode(DEMO_NODE, 8) is None
    assert 8 not in lock.code_slots
    assert lock.slots == 5


@pytest.mark.asyncio
async def test_resize_shrink_aborts_when_clear_fails(dev_mock_env):
    """If a dropped slot's clear fails, the shrink is aborted (no orphan)."""
    from custom_components.smart_lock_manager.services.slot_services import (
        SlotServices,
    )

    hass = _make_hass()
    lock = _demo_lock()
    lock.set_code(8, "8888", "High")
    _seed_hass(hass, lock)
    MOCK_DB.set_usercode(DEMO_NODE, 8, "8888")
    MOCK_DB.fail_next(True)

    await SlotServices.resize_slots(
        hass, _call({"entity_id": DEMO_ENTITY, "slot_count": 5})
    )

    # Not shrunk: the slot (and its known code) is still tracked, not orphaned.
    assert 8 in lock.code_slots
    assert lock.slots == 10
    assert lock.code_slots[8].sync_error


# ---------------------------------------------------------------------------
# Fix 5: max_uses is enforced via increment_usage on keypad unlock
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_keypad_unlock_counts_and_autodisables(dev_mock_env):
    """A max_uses=1 code auto-disables after one keypad unlock -> desired-absent."""
    from custom_components.smart_lock_manager.services.access_log import (
        _build_access_log_handler,
    )

    hass = _make_hass()
    lock = _demo_lock()
    lock.set_code(1, "1234", "Guest", max_uses=1)
    _seed_hass(hass, lock)

    handler = _build_access_log_handler(hass)
    event = Mock()
    event.data = {
        "command_class": 113,
        "event": 6,  # keypad unlock
        "node_id": DEMO_NODE,
        "parameters": {"userId": 1},
    }
    await handler(event)

    slot = lock.code_slots[1]
    assert slot.use_count == 1
    assert slot.is_active is False  # exhausted -> auto-disabled
    # By the invariant, it is now removed from the lock on the next cycle.
    actions = lock.get_slots_needing_sync({1: {"code": "1234"}})
    assert 1 in actions["remove"]


@pytest.mark.asyncio
async def test_rf_unlock_does_not_count(dev_mock_env):
    """An RF (non-keypad) unlock must NOT consume a max_uses credit."""
    from custom_components.smart_lock_manager.services.access_log import (
        _build_access_log_handler,
    )

    hass = _make_hass()
    lock = _demo_lock()
    lock.set_code(1, "1234", "Guest", max_uses=1)
    _seed_hass(hass, lock)

    handler = _build_access_log_handler(hass)
    event = Mock()
    event.data = {
        "command_class": 113,
        "event": 4,  # RF unlock (app/HA), NOT keypad
        "node_id": DEMO_NODE,
    }
    await handler(event)

    assert lock.code_slots[1].use_count == 0
    assert lock.code_slots[1].is_active is True


# ---------------------------------------------------------------------------
# Fix 6: prefix-collision rejection never leaks any slice of the PIN
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_prefix_collision_does_not_leak_pin(setup_hass_data, caplog):
    """No slice of the PIN may appear in the raised error or any log record."""
    from homeassistant.exceptions import HomeAssistantError

    from custom_components.smart_lock_manager.services.lock_services import (
        LockServices,
    )

    caplog.set_level(logging.DEBUG)
    hass = setup_hass_data
    lock = hass.data[DOMAIN]["test_entry_123"][PRIMARY_LOCK]
    lock.code_slots[2].pin_code = "1234"
    lock.code_slots[2].user_name = "Existing"
    lock.code_slots[2].is_active = True

    call = _call(
        {
            "entity_id": "lock.test_lock",
            "code_slot": 1,
            "usercode": "12345",  # first 4 digits collide with slot 2
            "code_slot_name": "Collider",
        }
    )

    with pytest.raises(HomeAssistantError) as excinfo:
        await LockServices.set_code_advanced(hass, call)

    err_text = str(excinfo.value)
    log_text = "\n".join(r.getMessage() for r in caplog.records)
    for leak in ("12345", "1234"):
        assert leak not in err_text, f"PIN slice {leak!r} leaked into error"
        assert leak not in log_text, f"PIN slice {leak!r} leaked into logs"
    # The stored sync_error the UI shows must also be PIN-free.
    assert "1234" not in (lock.code_slots[1].sync_error or "")


def test_reject_for_prefix_collision_no_pin_in_logs(caplog):
    """The coordinator-path collision guard is also PIN-free in logs/error."""
    from custom_components.smart_lock_manager.services.zwave_services import (
        _reject_for_prefix_collision,
    )

    caplog.set_level(logging.DEBUG)
    lock = SmartLockManagerLock(
        lock_name="T", lock_entity_id="lock.t", slots=5, start_from=1
    )
    lock.set_code(2, "1234", "Existing")

    rejected = _reject_for_prefix_collision(lock, 1, "12345")

    assert rejected is True
    log_text = "\n".join(r.getMessage() for r in caplog.records)
    for leak in ("12345", "1234"):
        assert leak not in log_text
    assert "1234" not in (lock.code_slots[1].sync_error or "")
