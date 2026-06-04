"""Comprehensive pytest fixtures for Smart Lock Manager tests."""

from datetime import datetime
from unittest.mock import AsyncMock, Mock

import pytest
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers.storage import Store
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.smart_lock_manager.const import DOMAIN, PRIMARY_LOCK
from custom_components.smart_lock_manager.models.lock import (
    CodeSlot,
    SmartLockManagerLock,
)


@pytest.fixture
def mock_config_entry() -> ConfigEntry:
    """Create a mock config entry."""
    return MockConfigEntry(
        domain=DOMAIN,
        data={
            "name": "Test Lock Manager",
        },
        unique_id="test_lock_manager",
    )


@pytest.fixture
async def init_integration(hass: HomeAssistant, mock_config_entry: ConfigEntry):
    """Set up the integration for testing."""
    mock_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    return mock_config_entry


@pytest.fixture
def hass_mock():
    """Create a mock Home Assistant instance."""
    hass = Mock(spec=HomeAssistant)
    hass.data = {DOMAIN: {}}
    hass.services = Mock()
    hass.services.async_register = AsyncMock()
    hass.services.async_call = AsyncMock()
    hass.bus = Mock()
    # async_fire is synchronous in Home Assistant; keep it a plain Mock so
    # production code that calls it without awaiting does not leak a coroutine.
    hass.bus.async_fire = Mock()
    return hass


@pytest.fixture
def config_entry_mock():
    """Create a mock config entry."""
    entry = Mock(spec=ConfigEntry)
    entry.entry_id = "test_entry_123"
    entry.data = {
        "lock_entity_id": "lock.test_lock",
        "lock_name": "Test Lock",
        "slots": 10,
        "start_from": 1,
    }
    return entry


@pytest.fixture
def store_mock():
    """Create a mock storage store."""
    store = Mock(spec=Store)
    store.async_save = AsyncMock()
    store.async_load = AsyncMock(return_value={})
    return store


@pytest.fixture
def service_call_mock():
    """Create a mock service call."""
    call = Mock(spec=ServiceCall)
    call.data = {
        "entity_id": "lock.test_lock",
        "code_slot": 1,
        "usercode": "1234",
        "code_slot_name": "Test User",
    }
    return call


@pytest.fixture
def sample_code_slot():
    """Create a sample code slot for testing."""
    return CodeSlot(
        slot_number=1,
        pin_code="1234",
        user_name="Test User",
        is_active=True,
        created_at=datetime.now(),
    )


@pytest.fixture
def weekend_code_slot():
    """Create a weekend-only code slot for testing."""
    return CodeSlot(
        slot_number=2,
        pin_code="5678",
        user_name="Weekend User",
        is_active=True,
        allowed_days=[5, 6],  # Saturday, Sunday
        created_at=datetime.now(),
    )


@pytest.fixture
def expired_code_slot():
    """Create an expired code slot for testing."""
    return CodeSlot(
        slot_number=3,
        pin_code="9999",
        user_name="Expired User",
        is_active=True,
        end_date=datetime(2023, 1, 1),  # Already expired
        created_at=datetime(2022, 12, 1),
    )


@pytest.fixture
def usage_limited_slot():
    """Create a usage-limited code slot for testing."""
    return CodeSlot(
        slot_number=4,
        pin_code="7777",
        user_name="Limited User",
        is_active=True,
        max_uses=5,
        use_count=3,
        created_at=datetime.now(),
    )


@pytest.fixture
def sample_lock():
    """Create a sample Smart Lock Manager lock."""
    return SmartLockManagerLock(
        lock_name="Test Lock", lock_entity_id="lock.test_lock", slots=10, start_from=1
    )


@pytest.fixture
def lock_with_slots(
    sample_lock, sample_code_slot, weekend_code_slot, expired_code_slot
):
    """Create a lock with sample slots populated."""
    sample_lock.code_slots[1] = sample_code_slot
    sample_lock.code_slots[2] = weekend_code_slot
    sample_lock.code_slots[3] = expired_code_slot
    return sample_lock


@pytest.fixture
def mock_zwave_codes():
    """Mock Z-Wave codes data structure."""
    return {
        1: {"code": "1234", "in_use": True, "userIdStatus": "occupied"},
        2: {"code": "5678", "in_use": False, "userIdStatus": "available"},
    }


@pytest.fixture
def setup_hass_data(hass_mock, config_entry_mock, sample_lock, store_mock):
    """Set up hass.data structure for testing."""
    hass_mock.data[DOMAIN][config_entry_mock.entry_id] = {
        PRIMARY_LOCK: sample_lock,
        "store": store_mock,
        "coordinator": Mock(),
        "entry": config_entry_mock,
    }
    return hass_mock
