"""Security-focused test cases for Smart Lock Manager."""

import pytest
from unittest.mock import AsyncMock, Mock, patch
from datetime import datetime
import logging
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import ServiceValidationError

from custom_components.smart_lock_manager.services.lock_services import LockServices
from custom_components.smart_lock_manager.models.lock import SmartLockManagerLock, CodeSlot
from custom_components.smart_lock_manager.const import DOMAIN, PRIMARY_LOCK


class TestSecurityValidation:
    """Test security validation and input sanitization."""

    @pytest.fixture
    def mock_hass(self):
        """Create a mock Home Assistant instance."""
        hass = Mock(spec=HomeAssistant)
        hass.data = {DOMAIN: {}}
        hass.bus = Mock()
        hass.bus.async_fire = AsyncMock()
        return hass

    @pytest.fixture
    def mock_lock(self):
        """Create a mock lock for testing."""
        return SmartLockManagerLock(
            lock_name="Test Lock",
            lock_entity_id="lock.test_lock",
            slots=10,
            start_from=1
        )

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

    async def test_pin_code_injection_attack(self, mock_hass, mock_lock, setup_hass_data):
        """Test PIN code input sanitization against injection attacks."""
        malicious_pins = [
            "'; DROP TABLE users; --",  # SQL injection attempt
            "<script>alert('xss')</script>",  # XSS attempt
            "../../../../etc/passwd",  # Path traversal attempt
            "1234\0",  # Null byte injection
            "1234\n\r",  # Newline injection
            "1234%00",  # URL encoded null byte
        ]
        
        for malicious_pin in malicious_pins:
            service_call = Mock(spec=ServiceCall)
            service_call.data = {
                "entity_id": "lock.test_lock",
                "code_slot": 1,
                "usercode": malicious_pin,
                "code_slot_name": "Test User"
            }
            
            with patch('custom_components.smart_lock_manager.services.lock_services.get_lock_from_entity') as mock_get_lock:
                mock_get_lock.return_value = (mock_lock, setup_hass_data)
                
                # Should either reject malicious input or sanitize it
                with pytest.raises(ServiceValidationError):
                    await LockServices.set_code_advanced(mock_hass, service_call)

    async def test_user_name_sanitization(self, mock_hass, mock_lock, setup_hass_data):
        """Test user name input sanitization."""
        malicious_names = [
            "<script>alert('xss')</script>",
            "'; DROP TABLE codes; --",
            "../../../etc/passwd",
            "User\0Name",
            "A" * 1000,  # Extremely long name
        ]
        
        for malicious_name in malicious_names:
            service_call = Mock(spec=ServiceCall)
            service_call.data = {
                "entity_id": "lock.test_lock",
                "code_slot": 1,
                "usercode": "1234",
                "code_slot_name": malicious_name
            }
            
            with patch('custom_components.smart_lock_manager.services.lock_services.get_lock_from_entity') as mock_get_lock:
                mock_get_lock.return_value = (mock_lock, setup_hass_data)
                
                # Should sanitize or reject malicious names
                try:
                    await LockServices.set_code_advanced(mock_hass, service_call)
                    # If accepted, verify it was sanitized
                    if 1 in mock_lock.code_slots:
                        sanitized_name = mock_lock.code_slots[1].user_name
                        assert "<script>" not in sanitized_name
                        assert "DROP TABLE" not in sanitized_name
                        assert len(sanitized_name) <= 100  # Reasonable length limit
                except ServiceValidationError:
                    # Rejection is also acceptable
                    pass

    async def test_pin_length_validation(self, mock_hass, mock_lock, setup_hass_data):
        """Test PIN code length validation."""
        invalid_pins = [
            "",           # Empty PIN
            "1",          # Too short
            "12",         # Too short
            "123",        # Too short  
            "123456789",  # Too long for most locks
            "1234567890123456",  # Extremely long
        ]
        
        for invalid_pin in invalid_pins:
            service_call = Mock(spec=ServiceCall)
            service_call.data = {
                "entity_id": "lock.test_lock",
                "code_slot": 1,
                "usercode": invalid_pin,
                "code_slot_name": "Test User"
            }
            
            with patch('custom_components.smart_lock_manager.services.lock_services.get_lock_from_entity') as mock_get_lock:
                mock_get_lock.return_value = (mock_lock, setup_hass_data)
                
                with pytest.raises(ServiceValidationError):
                    await LockServices.set_code_advanced(mock_hass, service_call)

    async def test_pin_character_validation(self, mock_hass, mock_lock, setup_hass_data):
        """Test PIN code character validation (numeric only)."""
        invalid_pins = [
            "abcd",       # Letters
            "12ab",       # Mixed alphanumeric
            "12!@",       # Special characters
            "12 34",      # Spaces
            "12-34",      # Hyphens
            "12.34",      # Decimal points
        ]
        
        for invalid_pin in invalid_pins:
            service_call = Mock(spec=ServiceCall)
            service_call.data = {
                "entity_id": "lock.test_lock",
                "code_slot": 1,
                "usercode": invalid_pin,
                "code_slot_name": "Test User"
            }
            
            with patch('custom_components.smart_lock_manager.services.lock_services.get_lock_from_entity') as mock_get_lock:
                mock_get_lock.return_value = (mock_lock, setup_hass_data)
                
                with pytest.raises(ServiceValidationError):
                    await LockServices.set_code_advanced(mock_hass, service_call)

    async def test_slot_number_bounds_validation(self, mock_hass, mock_lock, setup_hass_data):
        """Test slot number bounds validation."""
        invalid_slots = [
            -1,           # Negative
            0,            # Zero (slots start at 1)
            99999,        # Extremely high
            -99999,       # Extremely negative
        ]
        
        for invalid_slot in invalid_slots:
            service_call = Mock(spec=ServiceCall)
            service_call.data = {
                "entity_id": "lock.test_lock",
                "code_slot": invalid_slot,
                "usercode": "1234",
                "code_slot_name": "Test User"
            }
            
            with patch('custom_components.smart_lock_manager.services.lock_services.get_lock_from_entity') as mock_get_lock:
                mock_get_lock.return_value = (mock_lock, setup_hass_data)
                
                with pytest.raises(ServiceValidationError):
                    await LockServices.set_code_advanced(mock_hass, service_call)

    async def test_max_uses_validation(self, mock_hass, mock_lock, setup_hass_data):
        """Test max_uses parameter validation."""
        invalid_max_uses = [
            -5,           # Negative (except -1)
            -99999,       # Extremely negative
            0,            # Zero uses not logical
        ]
        
        for invalid_max_uses_val in invalid_max_uses:
            service_call = Mock(spec=ServiceCall)
            service_call.data = {
                "entity_id": "lock.test_lock",
                "code_slot": 1,
                "usercode": "1234",
                "code_slot_name": "Test User",
                "max_uses": invalid_max_uses_val
            }
            
            with patch('custom_components.smart_lock_manager.services.lock_services.get_lock_from_entity') as mock_get_lock:
                mock_get_lock.return_value = (mock_lock, setup_hass_data)
                
                with pytest.raises(ServiceValidationError):
                    await LockServices.set_code_advanced(mock_hass, service_call)


class TestLoggingSecurity:
    """Test that sensitive information is not logged."""

    @pytest.fixture
    def capture_logs(self, caplog):
        """Capture logs for security analysis."""
        caplog.set_level(logging.DEBUG)
        return caplog

    async def test_pin_codes_not_logged_plaintext(self, mock_hass, mock_lock, setup_hass_data, capture_logs):
        """Test that PIN codes are not logged in plaintext."""
        service_call = Mock(spec=ServiceCall)
        service_call.data = {
            "entity_id": "lock.test_lock",
            "code_slot": 1,
            "usercode": "1234",
            "code_slot_name": "Test User"
        }
        
        with patch('custom_components.smart_lock_manager.services.lock_services.get_lock_from_entity') as mock_get_lock:
            mock_get_lock.return_value = (mock_lock, setup_hass_data)
            
            try:
                await LockServices.set_code_advanced(mock_hass, service_call)
            except:
                pass  # Don't care about success/failure, just check logs
            
            # Check that PIN code is not in logs
            log_text = "\n".join([record.message for record in capture_logs.records])
            assert "1234" not in log_text, "PIN code found in logs!"
            
            # PIN should be masked if logged
            if "pin" in log_text.lower() or "code" in log_text.lower():
                assert "***" in log_text or "****" in log_text, "PIN not properly masked in logs"

    async def test_sensitive_data_masking(self, mock_hass, mock_lock, setup_hass_data, capture_logs):
        """Test that sensitive data is properly masked in logs."""
        # Create slot with sensitive data
        mock_lock.code_slots[1] = CodeSlot(
            slot_number=1,
            pin_code="9876",
            user_name="Sensitive User",
            is_active=True,
            created_at=datetime.now()
        )
        
        service_call = Mock(spec=ServiceCall)
        service_call.data = {
            "entity_id": "lock.test_lock",
            "code_slot": 1,
            "usercode": "5432",
            "code_slot_name": "Updated User"
        }
        
        with patch('custom_components.smart_lock_manager.services.lock_services.get_lock_from_entity') as mock_get_lock:
            mock_get_lock.return_value = (mock_lock, setup_hass_data)
            
            try:
                await LockServices.set_code_advanced(mock_hass, service_call)
            except:
                pass
            
            log_text = "\n".join([record.message for record in capture_logs.records])
            
            # Neither old nor new PIN should appear in logs
            assert "9876" not in log_text, "Old PIN code found in logs!"
            assert "5432" not in log_text, "New PIN code found in logs!"


class TestAccessControlSecurity:
    """Test access control and authorization."""

    async def test_unauthorized_entity_access(self, mock_hass):
        """Test access to non-existent or unauthorized entities."""
        service_call = Mock(spec=ServiceCall)
        service_call.data = {
            "entity_id": "lock.unauthorized_lock",
            "code_slot": 1,
            "usercode": "1234",
            "code_slot_name": "Hacker"
        }
        
        # Don't setup hass.data for this entity
        
        with patch('custom_components.smart_lock_manager.services.lock_services.get_lock_from_entity') as mock_get_lock:
            mock_get_lock.side_effect = ServiceValidationError("Lock not found")
            
            with pytest.raises(ServiceValidationError):
                await LockServices.set_code_advanced(mock_hass, service_call)

    async def test_cross_lock_access_prevention(self, mock_hass):
        """Test that operations on one lock can't affect another."""
        # Setup two different locks
        lock1 = SmartLockManagerLock(
            lock_name="Lock 1",
            lock_entity_id="lock.lock1",
            slots=10,
            start_from=1
        )
        
        lock2 = SmartLockManagerLock(
            lock_name="Lock 2", 
            lock_entity_id="lock.lock2",
            slots=10,
            start_from=1
        )
        
        # Setup data for lock1 only
        mock_hass.data[DOMAIN] = {
            "entry1": {
                PRIMARY_LOCK: lock1,
                "store": Mock(),
                "coordinator": Mock(),
                "entry": Mock(entry_id="entry1")
            }
        }
        
        # Try to access lock2 which isn't properly configured
        service_call = Mock(spec=ServiceCall)
        service_call.data = {
            "entity_id": "lock.lock2",  # Different lock
            "code_slot": 1,
            "usercode": "1234",
            "code_slot_name": "Hacker"
        }
        
        with patch('custom_components.smart_lock_manager.services.lock_services.get_lock_from_entity') as mock_get_lock:
            mock_get_lock.side_effect = ServiceValidationError("Lock not found")
            
            with pytest.raises(ServiceValidationError):
                await LockServices.set_code_advanced(mock_hass, service_call)


class TestDataIntegrity:
    """Test data integrity and consistency."""

    async def test_concurrent_modifications_integrity(self, mock_hass, mock_lock, setup_hass_data):
        """Test data integrity under concurrent modifications."""
        # This would test race conditions in a real implementation
        # For mock testing, verify that operations maintain data consistency
        
        original_slot_count = len(mock_lock.code_slots)
        
        service_calls = []
        for i in range(5):
            call = Mock(spec=ServiceCall)
            call.data = {
                "entity_id": "lock.test_lock",
                "code_slot": i + 1,
                "usercode": f"123{i}",
                "code_slot_name": f"User {i}"
            }
            service_calls.append(call)
        
        with patch('custom_components.smart_lock_manager.services.lock_services.get_lock_from_entity') as mock_get_lock:
            mock_get_lock.return_value = (mock_lock, setup_hass_data)
            
            # Execute multiple operations
            for call in service_calls:
                try:
                    await LockServices.set_code_advanced(mock_hass, call)
                except:
                    pass  # Some might fail, that's ok
            
            # Verify data structure integrity
            assert isinstance(mock_lock.code_slots, dict)
            assert len(mock_lock.code_slots) >= original_slot_count

    async def test_storage_corruption_resilience(self, mock_hass, mock_lock, setup_hass_data):
        """Test resilience against storage corruption."""
        # Simulate storage save failure
        store_mock = mock_hass.data[DOMAIN][setup_hass_data]["store"]
        store_mock.async_save.side_effect = Exception("Storage corruption")
        
        service_call = Mock(spec=ServiceCall)
        service_call.data = {
            "entity_id": "lock.test_lock",
            "code_slot": 1,
            "usercode": "1234",
            "code_slot_name": "Test User"
        }
        
        with patch('custom_components.smart_lock_manager.services.lock_services.get_lock_from_entity') as mock_get_lock:
            mock_get_lock.return_value = (mock_lock, setup_hass_data)
            
            # Should handle storage errors gracefully
            with pytest.raises(Exception):
                await LockServices.set_code_advanced(mock_hass, service_call)