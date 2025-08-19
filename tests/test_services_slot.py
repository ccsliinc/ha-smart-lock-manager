"""Test Smart Lock Manager slot services."""

import pytest
from unittest.mock import AsyncMock, Mock, patch
from datetime import datetime, timedelta
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import ServiceValidationError

from custom_components.smart_lock_manager.services.slot_services import SlotServices
from custom_components.smart_lock_manager.const import DOMAIN, PRIMARY_LOCK
from custom_components.smart_lock_manager.models.lock import SmartLockManagerLock, CodeSlot


class TestSlotServices:
    """Test slot management services."""

    @pytest.fixture
    def mock_hass(self):
        """Create a mock Home Assistant instance."""
        hass = Mock(spec=HomeAssistant)
        hass.data = {DOMAIN: {}}
        hass.bus = Mock()
        hass.bus.async_fire = AsyncMock()
        hass.services = Mock()
        hass.services.async_call = AsyncMock()
        return hass

    @pytest.fixture
    def mock_lock(self):
        """Create a mock lock with sample slots."""
        lock = SmartLockManagerLock(
            lock_name="Test Lock",
            lock_entity_id="lock.test_lock",
            slots=10,
            start_from=1
        )
        
        # Add sample slots
        lock.code_slots[1] = CodeSlot(
            slot_number=1,
            pin_code="1234",
            user_name="Test User",
            is_active=True,
            use_count=5,
            max_uses=10,
            created_at=datetime.now()
        )
        
        lock.code_slots[2] = CodeSlot(
            slot_number=2,
            pin_code="5678",
            user_name="Weekend User",
            is_active=True,
            allowed_days=[5, 6],
            created_at=datetime.now()
        )
        
        return lock

    @pytest.fixture
    def setup_hass_data(self, mock_hass, mock_lock):
        """Setup hass.data structure."""
        entry_id = "test_entry_123"
        mock_hass.data[DOMAIN][entry_id] = {
            PRIMARY_LOCK: mock_lock,
            "store": Mock(),
            "coordinator": Mock(),
            "entry": Mock(entry_id=entry_id)
        }
        return entry_id

    async def test_enable_slot_service(self, mock_hass, mock_lock, setup_hass_data):
        """Test enabling a slot."""
        # Disable slot first
        mock_lock.code_slots[1].is_active = False
        
        service_call = Mock(spec=ServiceCall)
        service_call.data = {
            "entity_id": "lock.test_lock",
            "code_slot": 1
        }
        
        with patch('custom_components.smart_lock_manager.services.slot_services.get_lock_from_entity') as mock_get_lock:
            mock_get_lock.return_value = (mock_lock, setup_hass_data)
            
            await SlotServices.enable_slot(mock_hass, service_call)
            
            # Verify slot was enabled
            assert mock_lock.code_slots[1].is_active is True
            
            # Verify storage save was called
            mock_hass.data[DOMAIN][setup_hass_data]["store"].async_save.assert_called_once()

    async def test_disable_slot_service(self, mock_hass, mock_lock, setup_hass_data):
        """Test disabling a slot."""
        service_call = Mock(spec=ServiceCall)
        service_call.data = {
            "entity_id": "lock.test_lock", 
            "code_slot": 1
        }
        
        with patch('custom_components.smart_lock_manager.services.slot_services.get_lock_from_entity') as mock_get_lock:
            mock_get_lock.return_value = (mock_lock, setup_hass_data)
            
            await SlotServices.disable_slot(mock_hass, service_call)
            
            # Verify slot was disabled
            assert mock_lock.code_slots[1].is_active is False
            
            # Verify storage save was called
            mock_hass.data[DOMAIN][setup_hass_data]["store"].async_save.assert_called_once()

    async def test_reset_slot_usage(self, mock_hass, mock_lock, setup_hass_data):
        """Test resetting slot usage count."""
        # Set initial usage count
        mock_lock.code_slots[1].use_count = 5
        
        service_call = Mock(spec=ServiceCall)
        service_call.data = {
            "entity_id": "lock.test_lock",
            "code_slot": 1
        }
        
        with patch('custom_components.smart_lock_manager.services.slot_services.get_lock_from_entity') as mock_get_lock:
            mock_get_lock.return_value = (mock_lock, setup_hass_data)
            
            await SlotServices.reset_slot_usage(mock_hass, service_call)
            
            # Verify usage count was reset
            assert mock_lock.code_slots[1].use_count == 0
            
            # Verify storage save was called
            mock_hass.data[DOMAIN][setup_hass_data]["store"].async_save.assert_called_once()

    async def test_resize_slots_expand(self, mock_hass, mock_lock, setup_hass_data):
        """Test expanding slot count."""
        original_slots = len(mock_lock.code_slots)
        
        service_call = Mock(spec=ServiceCall)
        service_call.data = {
            "entity_id": "lock.test_lock",
            "slot_count": 15  # Expand from 10 to 15
        }
        
        with patch('custom_components.smart_lock_manager.services.slot_services.get_lock_from_entity') as mock_get_lock:
            mock_get_lock.return_value = (mock_lock, setup_hass_data)
            
            await SlotServices.resize_slots(mock_hass, service_call)
            
            # Verify slot count was updated
            assert mock_lock.slots == 15
            
            # Existing slots should remain unchanged
            assert mock_lock.code_slots[1].user_name == "Test User"
            assert mock_lock.code_slots[2].user_name == "Weekend User"

    async def test_resize_slots_shrink(self, mock_hass, mock_lock, setup_hass_data):
        """Test shrinking slot count and clearing higher slots."""
        # Add a slot at position 8
        mock_lock.code_slots[8] = CodeSlot(
            slot_number=8,
            pin_code="8888",
            user_name="High Slot User",
            is_active=True,
            created_at=datetime.now()
        )
        
        service_call = Mock(spec=ServiceCall)
        service_call.data = {
            "entity_id": "lock.test_lock",
            "slot_count": 5  # Shrink from 10 to 5
        }
        
        with patch('custom_components.smart_lock_manager.services.slot_services.get_lock_from_entity') as mock_get_lock:
            mock_get_lock.return_value = (mock_lock, setup_hass_data)
            
            await SlotServices.resize_slots(mock_hass, service_call)
            
            # Verify slot count was updated
            assert mock_lock.slots == 5
            
            # Lower slots should remain
            assert mock_lock.code_slots[1].user_name == "Test User"
            assert mock_lock.code_slots[2].user_name == "Weekend User"
            
            # Higher slots should be cleared
            assert 8 not in mock_lock.code_slots or mock_lock.code_slots[8] is None

    async def test_enable_nonexistent_slot(self, mock_hass, mock_lock, setup_hass_data):
        """Test enabling a slot that doesn't exist."""
        service_call = Mock(spec=ServiceCall)
        service_call.data = {
            "entity_id": "lock.test_lock",
            "code_slot": 99  # Nonexistent slot
        }
        
        with patch('custom_components.smart_lock_manager.services.slot_services.get_lock_from_entity') as mock_get_lock:
            mock_get_lock.return_value = (mock_lock, setup_hass_data)
            
            # Should create a new slot or handle gracefully
            await SlotServices.enable_slot(mock_hass, service_call)
            
            # Behavior depends on implementation - might create slot or raise error

    async def test_slot_validation_invalid_slot_number(self, mock_hass, mock_lock, setup_hass_data):
        """Test validation with invalid slot number."""
        service_call = Mock(spec=ServiceCall)
        service_call.data = {
            "entity_id": "lock.test_lock",
            "code_slot": 0  # Invalid: slot numbers start at 1
        }
        
        with patch('custom_components.smart_lock_manager.services.slot_services.get_lock_from_entity') as mock_get_lock:
            mock_get_lock.return_value = (mock_lock, setup_hass_data)
            
            with pytest.raises(ServiceValidationError):
                await SlotServices.enable_slot(mock_hass, service_call)

    async def test_slot_validation_negative_slot_count(self, mock_hass, mock_lock, setup_hass_data):
        """Test validation with negative slot count."""
        service_call = Mock(spec=ServiceCall)
        service_call.data = {
            "entity_id": "lock.test_lock",
            "slot_count": -5  # Invalid: negative slot count
        }
        
        with patch('custom_components.smart_lock_manager.services.slot_services.get_lock_from_entity') as mock_get_lock:
            mock_get_lock.return_value = (mock_lock, setup_hass_data)
            
            with pytest.raises(ServiceValidationError):
                await SlotServices.resize_slots(mock_hass, service_call)

    async def test_enable_slot_with_pin_code(self, mock_hass, mock_lock, setup_hass_data):
        """Test enabling a slot that has pin code but is disabled."""
        # Create disabled slot with PIN code
        mock_lock.code_slots[3] = CodeSlot(
            slot_number=3,
            pin_code="9999",
            user_name="Disabled User",
            is_active=False,
            created_at=datetime.now()
        )
        
        service_call = Mock(spec=ServiceCall)
        service_call.data = {
            "entity_id": "lock.test_lock",
            "code_slot": 3
        }
        
        with patch('custom_components.smart_lock_manager.services.slot_services.get_lock_from_entity') as mock_get_lock:
            mock_get_lock.return_value = (mock_lock, setup_hass_data)
            
            await SlotServices.enable_slot(mock_hass, service_call)
            
            # Verify slot was enabled and PIN code preserved
            assert mock_lock.code_slots[3].is_active is True
            assert mock_lock.code_slots[3].pin_code == "9999"
            assert mock_lock.code_slots[3].user_name == "Disabled User"

    async def test_reset_usage_preserves_other_data(self, mock_hass, mock_lock, setup_hass_data):
        """Test that resetting usage preserves other slot data."""
        original_pin = mock_lock.code_slots[1].pin_code
        original_name = mock_lock.code_slots[1].user_name
        original_max_uses = mock_lock.code_slots[1].max_uses
        
        service_call = Mock(spec=ServiceCall)
        service_call.data = {
            "entity_id": "lock.test_lock",
            "code_slot": 1
        }
        
        with patch('custom_components.smart_lock_manager.services.slot_services.get_lock_from_entity') as mock_get_lock:
            mock_get_lock.return_value = (mock_lock, setup_hass_data)
            
            await SlotServices.reset_slot_usage(mock_hass, service_call)
            
            # Verify only use_count was reset, other data preserved
            assert mock_lock.code_slots[1].use_count == 0
            assert mock_lock.code_slots[1].pin_code == original_pin
            assert mock_lock.code_slots[1].user_name == original_name
            assert mock_lock.code_slots[1].max_uses == original_max_uses

    async def test_resize_slots_minimum_size(self, mock_hass, mock_lock, setup_hass_data):
        """Test resizing to minimum allowed slot count."""
        service_call = Mock(spec=ServiceCall)
        service_call.data = {
            "entity_id": "lock.test_lock",
            "slot_count": 1  # Minimum size
        }
        
        with patch('custom_components.smart_lock_manager.services.slot_services.get_lock_from_entity') as mock_get_lock:
            mock_get_lock.return_value = (mock_lock, setup_hass_data)
            
            await SlotServices.resize_slots(mock_hass, service_call)
            
            # Should accept minimum size
            assert mock_lock.slots == 1

    async def test_missing_lock_entity_error(self, mock_hass):
        """Test error handling when lock entity is not found."""
        service_call = Mock(spec=ServiceCall)
        service_call.data = {
            "entity_id": "lock.nonexistent_lock",
            "code_slot": 1
        }
        
        with patch('custom_components.smart_lock_manager.services.slot_services.get_lock_from_entity') as mock_get_lock:
            mock_get_lock.side_effect = ServiceValidationError("Lock not found")
            
            with pytest.raises(ServiceValidationError):
                await SlotServices.enable_slot(mock_hass, service_call)


class TestSlotServiceEdgeCases:
    """Test edge cases and error conditions."""

    async def test_concurrent_slot_modifications(self, mock_hass, mock_lock, setup_hass_data):
        """Test handling of concurrent slot modifications."""
        # This would test race conditions in real implementation
        # For now, test that multiple operations can be performed
        
        service_call_1 = Mock(spec=ServiceCall)
        service_call_1.data = {"entity_id": "lock.test_lock", "code_slot": 1}
        
        service_call_2 = Mock(spec=ServiceCall) 
        service_call_2.data = {"entity_id": "lock.test_lock", "code_slot": 2}
        
        with patch('custom_components.smart_lock_manager.services.slot_services.get_lock_from_entity') as mock_get_lock:
            mock_get_lock.return_value = (mock_lock, setup_hass_data)
            
            # Perform multiple operations
            await SlotServices.enable_slot(mock_hass, service_call_1)
            await SlotServices.disable_slot(mock_hass, service_call_2)
            
            # Both operations should complete successfully
            assert mock_lock.code_slots[1].is_active is True
            assert mock_lock.code_slots[2].is_active is False

    async def test_storage_save_failure(self, mock_hass, mock_lock, setup_hass_data):
        """Test handling of storage save failures."""
        service_call = Mock(spec=ServiceCall)
        service_call.data = {
            "entity_id": "lock.test_lock",
            "code_slot": 1
        }
        
        # Make storage save fail
        store_mock = mock_hass.data[DOMAIN][setup_hass_data]["store"]
        store_mock.async_save.side_effect = Exception("Storage error")
        
        with patch('custom_components.smart_lock_manager.services.slot_services.get_lock_from_entity') as mock_get_lock:
            mock_get_lock.return_value = (mock_lock, setup_hass_data)
            
            # Should handle storage errors gracefully
            with pytest.raises(Exception):
                await SlotServices.enable_slot(mock_hass, service_call)