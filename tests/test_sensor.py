"""Test the Smart Lock Manager sensor."""

from datetime import datetime
from unittest.mock import Mock, patch

import pytest
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from custom_components.smart_lock_manager.const import DOMAIN, PRIMARY_LOCK
from custom_components.smart_lock_manager.models.lock import (
    CodeSlot,
    SmartLockManagerLock,
)
from custom_components.smart_lock_manager.sensor import (
    SmartLockManagerSensor,
    async_setup_entry,
)


class TestSmartLockManagerSensor:
    """Test the SmartLockManagerSensor class."""

    @pytest.fixture
    def mock_hass(self):
        """Create a mock Home Assistant instance."""
        hass = Mock(spec=HomeAssistant)
        hass.data = {DOMAIN: {}}
        return hass

    @pytest.fixture
    def mock_config_entry(self):
        """Create a mock config entry."""
        entry = Mock(spec=ConfigEntry)
        entry.entry_id = "test_entry_123"
        entry.data = {
            "lock_entity_id": "lock.test_lock",
            "lock_name": "Test Lock",
        }
        return entry

    @pytest.fixture
    def mock_lock_with_slots(self):
        """Create a mock lock with populated slots."""
        lock = SmartLockManagerLock(
            lock_name="Test Lock",
            lock_entity_id="lock.test_lock",
            slots=10,
            start_from=1,
        )

        # Add some test slots
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
            allowed_days=[5, 6],  # Saturday, Sunday
            created_at=datetime.now(),
        )

        return lock

    @pytest.fixture
    def mock_coordinator(self):
        """Create a mock DataUpdateCoordinator for CoordinatorEntity."""
        coordinator = Mock()
        coordinator.last_update_success = True
        return coordinator

    @pytest.fixture
    def sensor_instance(
        self, mock_hass, mock_config_entry, mock_lock_with_slots, mock_coordinator
    ):
        """Create a sensor instance for testing."""
        mock_hass.data[DOMAIN][mock_config_entry.entry_id] = {
            PRIMARY_LOCK: mock_lock_with_slots,
            "coordinator": mock_coordinator,
            "entry": mock_config_entry,
        }

        sensor = SmartLockManagerSensor(
            hass=mock_hass,
            entry=mock_config_entry,
            lock=mock_lock_with_slots,
            coordinator=mock_coordinator,
        )
        return sensor

    async def test_async_setup_entry(
        self, mock_hass, mock_config_entry, mock_lock_with_slots, mock_coordinator
    ):
        """Test async_setup_entry function."""
        mock_hass.data[DOMAIN][mock_config_entry.entry_id] = {
            PRIMARY_LOCK: mock_lock_with_slots,
            "coordinator": mock_coordinator,
            "entry": mock_config_entry,
        }

        add_entities = Mock(spec=AddEntitiesCallback)

        await async_setup_entry(mock_hass, mock_config_entry, add_entities)

        # Verify that add_entities was called with a SmartLockManagerSensor
        add_entities.assert_called_once()
        entities = add_entities.call_args[0][0]
        assert len(entities) == 1
        assert isinstance(entities[0], SmartLockManagerSensor)

    def test_sensor_properties(self, sensor_instance):
        """Test sensor basic properties.

        Name falls back to the lock settings friendly name ("Smart Lock"
        default). Unique id is derived from the config entry id. Icon is fixed
        and the entity is coordinator-driven (no polling).
        """
        assert sensor_instance.name == "Smart Lock"
        assert sensor_instance.unique_id == "smart_lock_manager_test_entry_123"
        assert sensor_instance.icon == "mdi:lock-smart"
        assert sensor_instance.should_poll is False

    def test_sensor_state(self, sensor_instance):
        """Test sensor state calculation.

        State combines connection status with the display name.
        """
        state = sensor_instance.state
        assert state == "Connected - Smart Lock"

    def test_sensor_attributes(self, sensor_instance):
        """Test sensor attributes calculation."""
        attrs = sensor_instance.extra_state_attributes

        # Check basic attributes
        assert attrs["integration_version"] == "1.0.0"
        assert attrs["architecture"] == "object_oriented_advanced"
        assert attrs["lock_entity_id"] == "lock.test_lock"
        assert attrs["lock_name"] == "Test Lock"
        assert attrs["total_slots"] == 10

        # Check slot details
        assert "slot_details" in attrs
        slot_details = attrs["slot_details"]

        # Check slot 1 details
        assert "slot_1" in slot_details
        slot_1 = slot_details["slot_1"]
        assert slot_1["user_name"] == "Test User"
        assert slot_1["is_active"] is True
        assert slot_1["use_count"] == 5
        assert slot_1["max_uses"] == 10

    def test_sensor_active_codes_count(self, sensor_instance):
        """Test active codes count calculation."""
        attrs = sensor_instance.extra_state_attributes
        assert attrs["active_codes_count"] == 2  # Two active slots

    def test_sensor_usage_stats(self, sensor_instance):
        """Test usage statistics calculation."""
        attrs = sensor_instance.extra_state_attributes

        usage_stats = attrs["usage_stats"]
        assert usage_stats["total_uses"] == 5  # Only slot 1 has use_count
        assert usage_stats["active_users"] == 2
        assert usage_stats["most_used_slot"] == 1
        assert usage_stats["most_used_count"] == 5

    def test_display_title_generation(self, sensor_instance):
        """Test slot display title generation."""
        attrs = sensor_instance.extra_state_attributes
        slot_details = attrs["slot_details"]

        # Slot with user name should show "Slot X: User Name"
        assert slot_details["slot_1"]["display_title"] == "Slot 1: Test User"
        assert slot_details["slot_2"]["display_title"] == "Slot 2: Weekend User"

    def test_slot_status_calculation(self, sensor_instance):
        """Test slot status calculation logic."""
        attrs = sensor_instance.extra_state_attributes
        slot_details = attrs["slot_details"]

        # Slot 1 is active and unsynced -> SYNCHRONIZING/SYNC_ERROR family.
        slot_1_status = slot_details["slot_1"]["status"]
        assert slot_1_status["name"] in [
            "SYNCHRONIZED",
            "SYNCHRONIZING",
            "SYNC_ERROR",
            "VALID",
        ]

        # Slot 2 is weekend-only; on a weekday it is OUTSIDE_HOURS.
        slot_2_status = slot_details["slot_2"]["status"]
        assert slot_2_status["name"] in [
            "SYNCHRONIZED",
            "SYNCHRONIZING",
            "VALID",
            "OUTSIDE_HOURS",
        ]

    def test_empty_slots_handling(self, sensor_instance):
        """Test handling of empty (unconfigured) slots.

        Empty slots are CodeSlot objects with no PIN. They surface as inactive
        with the EMPTY status, not as fully active codes.
        """
        attrs = sensor_instance.extra_state_attributes
        slot_details = attrs["slot_details"]

        # Slot 5 was never configured -> empty CodeSlot.
        assert "slot_5" in slot_details
        slot_5 = slot_details["slot_5"]
        assert slot_5["is_active"] is False
        assert slot_5["status"]["name"] == "EMPTY"

    @patch("custom_components.smart_lock_manager.sensor.datetime")
    def test_time_based_validation(self, mock_datetime, sensor_instance):
        """Test time-based slot validation does not break attribute building."""
        # Mock current time to be during weekend (Saturday)
        mock_datetime.now.return_value = datetime(2025, 1, 18, 14, 0)  # Saturday 2PM
        mock_datetime.side_effect = lambda *args, **kw: datetime(*args, **kw)

        # Weekend slot should be present and attribute generation must succeed.
        attrs = sensor_instance.extra_state_attributes
        slot_details = attrs["slot_details"]
        assert "slot_2" in slot_details

    def test_coordinator_integration(self, sensor_instance, mock_coordinator):
        """Test integration with coordinator."""
        # CoordinatorEntity exposes the coordinator it was built with.
        assert sensor_instance.coordinator is mock_coordinator

        # Test coordinator_context property exists (provided by CoordinatorEntity)
        assert hasattr(sensor_instance, "coordinator_context")


class TestSensorEdgeCases:
    """Test edge cases and error conditions."""

    @pytest.fixture
    def mock_hass(self):
        """Create a mock Home Assistant instance."""
        hass = Mock(spec=HomeAssistant)
        hass.data = {DOMAIN: {}}
        return hass

    @pytest.fixture
    def mock_config_entry(self):
        """Create a mock config entry."""
        entry = Mock(spec=ConfigEntry)
        entry.entry_id = "test_entry_123"
        entry.data = {
            "lock_entity_id": "lock.test_lock",
            "lock_name": "Test Lock",
        }
        return entry

    @pytest.fixture
    def mock_coordinator(self):
        """Create a mock DataUpdateCoordinator for CoordinatorEntity."""
        coordinator = Mock()
        coordinator.last_update_success = True
        return coordinator

    def test_unconfigured_slots_are_empty(
        self, mock_hass, mock_config_entry, mock_coordinator
    ):
        """A freshly created lock reports every slot as an empty CodeSlot."""
        lock = SmartLockManagerLock(
            lock_name="Test Lock",
            lock_entity_id="lock.test_lock",
            slots=10,
            start_from=1,
        )
        mock_hass.data[DOMAIN][mock_config_entry.entry_id] = {
            PRIMARY_LOCK: lock,
            "coordinator": mock_coordinator,
            "entry": mock_config_entry,
        }
        sensor = SmartLockManagerSensor(
            hass=mock_hass,
            entry=mock_config_entry,
            lock=lock,
            coordinator=mock_coordinator,
        )

        attrs = sensor.extra_state_attributes
        assert attrs["active_codes_count"] == 0
        assert all(
            slot["status"]["name"] == "EMPTY" for slot in attrs["slot_details"].values()
        )

    def test_large_slot_count(self, mock_hass, mock_config_entry, mock_coordinator):
        """Test sensor with large number of slots."""
        large_lock = SmartLockManagerLock(
            lock_name="Large Lock",
            lock_entity_id="lock.large_lock",
            slots=100,
            start_from=1,
        )

        mock_hass.data[DOMAIN][mock_config_entry.entry_id] = {
            PRIMARY_LOCK: large_lock,
            "coordinator": mock_coordinator,
            "entry": mock_config_entry,
        }

        sensor = SmartLockManagerSensor(
            hass=mock_hass,
            entry=mock_config_entry,
            lock=large_lock,
            coordinator=mock_coordinator,
        )

        # Should handle large slot count without issues
        attrs = sensor.extra_state_attributes
        assert attrs["total_slots"] == 100
        assert len(attrs["slot_details"]) == 100
