"""Test Smart Lock Manager slot services.

These tests exercise the *current* service layer, which resolves the target
lock inline by scanning ``hass.data[DOMAIN]`` for the entry whose
``PRIMARY_LOCK`` matches the requested ``entity_id`` (there is no
``get_lock_from_entity`` helper anymore). Persistence goes through the
module-level ``_save_lock_data`` helper, which itself reads the per-entry
``store`` from ``hass.data`` and calls ``store.async_save(lock.to_dict())``.

Slot/count validation lives in the voluptuous service schema and in the lock
model (which returns ``False`` for impossible operations); the service
handlers therefore log-and-return rather than raising on bad input.
"""

from datetime import datetime
from unittest.mock import AsyncMock, Mock

import pytest
from homeassistant.core import HomeAssistant, ServiceCall

from custom_components.smart_lock_manager.const import DOMAIN, PRIMARY_LOCK
from custom_components.smart_lock_manager.models.lock import (
    CodeSlot,
    SmartLockManagerLock,
)
from custom_components.smart_lock_manager.services.slot_services import SlotServices

ENTRY_ID = "test_entry_123"


@pytest.fixture
def mock_hass():
    """Create a mock Home Assistant instance."""
    hass = Mock(spec=HomeAssistant)
    hass.data = {DOMAIN: {}}
    hass.bus = Mock()
    hass.bus.async_fire = AsyncMock()
    hass.services = Mock()
    hass.services.async_call = AsyncMock()
    return hass


@pytest.fixture
def mock_lock():
    """Create a mock lock with sample slots."""
    lock = SmartLockManagerLock(
        lock_name="Test Lock",
        lock_entity_id="lock.test_lock",
        slots=10,
        start_from=1,
    )

    lock.code_slots[1] = CodeSlot(
        slot_number=1,
        pin_code="1234",
        user_name="Test User",
        is_active=True,
        use_count=5,
        max_uses=10,
        created_at=datetime.now(),
    )

    lock.code_slots[2] = CodeSlot(
        slot_number=2,
        pin_code="5678",
        user_name="Weekend User",
        is_active=True,
        allowed_days=[5, 6],
        created_at=datetime.now(),
    )

    return lock


@pytest.fixture
def store_mock():
    """Create a store mock with an awaitable async_save."""
    store = Mock()
    store.async_save = AsyncMock()
    return store


@pytest.fixture
def setup_hass_data(mock_hass, mock_lock, store_mock):
    """Seed hass.data with the lock so the service layer can resolve it inline."""
    mock_hass.data[DOMAIN][ENTRY_ID] = {
        PRIMARY_LOCK: mock_lock,
        "store": store_mock,
        "coordinator": Mock(),
        "entry": Mock(entry_id=ENTRY_ID),
    }
    return ENTRY_ID


def _call(data):
    """Build a ServiceCall mock with the given data payload."""
    service_call = Mock(spec=ServiceCall)
    service_call.data = data
    return service_call


class TestSlotServices:
    """Test slot management services."""

    async def test_enable_slot_service(self, mock_hass, mock_lock, setup_hass_data):
        """Test enabling a slot persists the change."""
        # Disable slot first (it has a PIN so it can be re-enabled).
        mock_lock.code_slots[1].is_active = False

        service_call = _call({"entity_id": "lock.test_lock", "code_slot": 1})
        await SlotServices.enable_slot(mock_hass, service_call)

        assert mock_lock.code_slots[1].is_active is True
        mock_hass.data[DOMAIN][setup_hass_data]["store"].async_save.assert_called_once()

    async def test_disable_slot_service(self, mock_hass, mock_lock, setup_hass_data):
        """Test disabling a slot persists the change."""
        service_call = _call({"entity_id": "lock.test_lock", "code_slot": 1})
        await SlotServices.disable_slot(mock_hass, service_call)

        assert mock_lock.code_slots[1].is_active is False
        mock_hass.data[DOMAIN][setup_hass_data]["store"].async_save.assert_called_once()

    async def test_reset_slot_usage(self, mock_hass, mock_lock, setup_hass_data):
        """Test resetting slot usage count persists the change."""
        mock_lock.code_slots[1].use_count = 5

        service_call = _call({"entity_id": "lock.test_lock", "code_slot": 1})
        await SlotServices.reset_slot_usage(mock_hass, service_call)

        assert mock_lock.code_slots[1].use_count == 0
        mock_hass.data[DOMAIN][setup_hass_data]["store"].async_save.assert_called_once()

    async def test_resize_slots_expand(self, mock_hass, mock_lock, setup_hass_data):
        """Test expanding slot count."""
        service_call = _call({"entity_id": "lock.test_lock", "slot_count": 15})
        await SlotServices.resize_slots(mock_hass, service_call)

        assert mock_lock.slots == 15
        # Existing slots should remain unchanged
        assert mock_lock.code_slots[1].user_name == "Test User"
        assert mock_lock.code_slots[2].user_name == "Weekend User"

    async def test_resize_slots_shrink(self, mock_hass, mock_lock, setup_hass_data):
        """Test shrinking slot count clears higher slots."""
        mock_lock.code_slots[8] = CodeSlot(
            slot_number=8,
            pin_code="8888",
            user_name="High Slot User",
            is_active=True,
            created_at=datetime.now(),
        )

        service_call = _call({"entity_id": "lock.test_lock", "slot_count": 5})
        await SlotServices.resize_slots(mock_hass, service_call)

        assert mock_lock.slots == 5
        # Lower slots should remain
        assert mock_lock.code_slots[1].user_name == "Test User"
        assert mock_lock.code_slots[2].user_name == "Weekend User"
        # Higher slots should be cleared
        assert 8 not in mock_lock.code_slots

    async def test_enable_nonexistent_slot(self, mock_hass, mock_lock, setup_hass_data):
        """Enabling a slot outside the configured range is a no-op (no save)."""
        service_call = _call({"entity_id": "lock.test_lock", "code_slot": 99})
        await SlotServices.enable_slot(mock_hass, service_call)

        assert 99 not in mock_lock.code_slots
        mock_hass.data[DOMAIN][setup_hass_data]["store"].async_save.assert_not_called()

    async def test_enable_slot_zero_is_noop(
        self, mock_hass, mock_lock, setup_hass_data
    ):
        """Slot 0 is below the valid range; the handler no-ops without raising."""
        service_call = _call({"entity_id": "lock.test_lock", "code_slot": 0})
        # No exception is raised; the lock model rejects the slot and the
        # handler simply logs and returns without persisting.
        await SlotServices.enable_slot(mock_hass, service_call)

        assert 0 not in mock_lock.code_slots
        mock_hass.data[DOMAIN][setup_hass_data]["store"].async_save.assert_not_called()

    async def test_resize_slots_negative_is_rejected(
        self, mock_hass, mock_lock, setup_hass_data
    ):
        """Negative slot counts are rejected by the handler guard (no change)."""
        service_call = _call({"entity_id": "lock.test_lock", "slot_count": -5})
        await SlotServices.resize_slots(mock_hass, service_call)

        # Slot count is left at its original value; nothing persisted.
        assert mock_lock.slots == 10
        mock_hass.data[DOMAIN][setup_hass_data]["store"].async_save.assert_not_called()

    async def test_enable_slot_with_pin_code(
        self, mock_hass, mock_lock, setup_hass_data
    ):
        """Test enabling a disabled slot that already has a PIN code."""
        mock_lock.code_slots[3] = CodeSlot(
            slot_number=3,
            pin_code="9999",
            user_name="Disabled User",
            is_active=False,
            created_at=datetime.now(),
        )

        service_call = _call({"entity_id": "lock.test_lock", "code_slot": 3})
        await SlotServices.enable_slot(mock_hass, service_call)

        assert mock_lock.code_slots[3].is_active is True
        assert mock_lock.code_slots[3].pin_code == "9999"
        assert mock_lock.code_slots[3].user_name == "Disabled User"

    async def test_reset_usage_preserves_other_data(
        self, mock_hass, mock_lock, setup_hass_data
    ):
        """Test that resetting usage preserves other slot data."""
        original_pin = mock_lock.code_slots[1].pin_code
        original_name = mock_lock.code_slots[1].user_name
        original_max_uses = mock_lock.code_slots[1].max_uses

        service_call = _call({"entity_id": "lock.test_lock", "code_slot": 1})
        await SlotServices.reset_slot_usage(mock_hass, service_call)

        assert mock_lock.code_slots[1].use_count == 0
        assert mock_lock.code_slots[1].pin_code == original_pin
        assert mock_lock.code_slots[1].user_name == original_name
        assert mock_lock.code_slots[1].max_uses == original_max_uses

    async def test_resize_slots_minimum_size(
        self, mock_hass, mock_lock, setup_hass_data
    ):
        """Test resizing to the minimum allowed slot count."""
        service_call = _call({"entity_id": "lock.test_lock", "slot_count": 1})
        await SlotServices.resize_slots(mock_hass, service_call)

        assert mock_lock.slots == 1

    async def test_missing_lock_entity_noop(self, mock_hass, setup_hass_data):
        """Unknown entity ids resolve to no lock; the handler no-ops safely."""
        service_call = _call({"entity_id": "lock.nonexistent_lock", "code_slot": 1})
        # No matching lock in hass.data -> logs "No lock found" and returns.
        await SlotServices.enable_slot(mock_hass, service_call)

        mock_hass.data[DOMAIN][setup_hass_data]["store"].async_save.assert_not_called()


class TestSlotServiceEdgeCases:
    """Test edge cases and error conditions."""

    async def test_concurrent_slot_modifications(
        self, mock_hass, mock_lock, setup_hass_data
    ):
        """Test handling of multiple sequential slot modifications."""
        call_1 = _call({"entity_id": "lock.test_lock", "code_slot": 1})
        call_2 = _call({"entity_id": "lock.test_lock", "code_slot": 2})

        await SlotServices.enable_slot(mock_hass, call_1)
        await SlotServices.disable_slot(mock_hass, call_2)

        assert mock_lock.code_slots[1].is_active is True
        assert mock_lock.code_slots[2].is_active is False

    async def test_storage_save_failure_is_swallowed(
        self, mock_hass, mock_lock, setup_hass_data
    ):
        """A failing store save is logged, not raised, by _save_lock_data."""
        store_mock = mock_hass.data[DOMAIN][setup_hass_data]["store"]
        store_mock.async_save.side_effect = Exception("Storage error")

        service_call = _call({"entity_id": "lock.test_lock", "code_slot": 1})

        # _save_lock_data wraps store.async_save in try/except and logs the
        # error; the service handler must not propagate it.
        await SlotServices.enable_slot(mock_hass, service_call)

        store_mock.async_save.assert_called_once()
        assert mock_lock.code_slots[1].is_active is True
