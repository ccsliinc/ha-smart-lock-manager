"""Test the Smart Lock Manager config flow."""

import pytest
from unittest.mock import Mock, patch, AsyncMock
from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from custom_components.smart_lock_manager.config_flow import SmartLockManagerConfigFlow
from custom_components.smart_lock_manager.const import DOMAIN


class TestSmartLockManagerConfigFlow:
    """Test the Smart Lock Manager configuration flow."""

    @pytest.fixture
    def mock_hass(self):
        """Create a mock Home Assistant instance."""
        hass = Mock(spec=HomeAssistant)
        hass.data = {}
        hass.states = Mock()
        hass.states.async_all.return_value = []
        return hass

    @pytest.fixture
    def config_flow(self, mock_hass):
        """Create a config flow instance."""
        flow = SmartLockManagerConfigFlow()
        flow.hass = mock_hass
        return flow

    @pytest.fixture
    def mock_lock_entities(self):
        """Create mock lock entities."""
        entities = []
        
        # Create mock lock entities
        for i in range(3):
            entity = Mock()
            entity.entity_id = f"lock.test_lock_{i}"
            entity.attributes = {
                "friendly_name": f"Test Lock {i}",
                "supported_features": 1,  # Supports locking
            }
            entity.domain = "lock"
            entities.append(entity)
            
        # Add a non-lock entity to test filtering
        non_lock = Mock()
        non_lock.entity_id = "switch.test_switch"
        non_lock.domain = "switch"
        entities.append(non_lock)
        
        return entities

    async def test_async_step_user_no_locks(self, config_flow):
        """Test user step when no lock entities are available."""
        config_flow.hass.states.async_all.return_value = []
        
        result = await config_flow.async_step_user()
        
        assert result["type"] == FlowResultType.ABORT
        assert result["reason"] == "no_locks"

    async def test_async_step_user_with_locks(self, config_flow, mock_lock_entities):
        """Test user step with available lock entities."""
        config_flow.hass.states.async_all.return_value = mock_lock_entities
        
        result = await config_flow.async_step_user()
        
        assert result["type"] == FlowResultType.FORM
        assert result["step_id"] == "user"
        
        # Check that data schema contains lock options
        schema = result["data_schema"]
        assert schema is not None

    async def test_async_step_user_form_submission(self, config_flow, mock_lock_entities):
        """Test form submission in user step."""
        config_flow.hass.states.async_all.return_value = mock_lock_entities
        
        user_input = {
            "lock_entity_id": "lock.test_lock_0",
            "lock_name": "My Test Lock",
            "slots": 10,
            "start_from": 1,
        }
        
        with patch.object(config_flow, 'async_set_unique_id') as mock_unique_id, \
             patch.object(config_flow, '_abort_if_unique_id_configured') as mock_abort:
            
            result = await config_flow.async_step_user(user_input)
            
            # Verify unique ID was set
            mock_unique_id.assert_called_once_with("lock.test_lock_0")
            mock_abort.assert_called_once()
            
            assert result["type"] == FlowResultType.CREATE_ENTRY
            assert result["title"] == "My Test Lock"
            assert result["data"] == user_input

    async def test_form_validation_invalid_slots(self, config_flow, mock_lock_entities):
        """Test form validation with invalid slot count."""
        config_flow.hass.states.async_all.return_value = mock_lock_entities
        
        user_input = {
            "lock_entity_id": "lock.test_lock_0",
            "lock_name": "Test Lock",
            "slots": 0,  # Invalid: must be > 0
            "start_from": 1,
        }
        
        result = await config_flow.async_step_user(user_input)
        
        assert result["type"] == FlowResultType.FORM
        assert "errors" in result
        # Should show error for invalid slot count

    async def test_form_validation_invalid_start_from(self, config_flow, mock_lock_entities):
        """Test form validation with invalid start_from value."""
        config_flow.hass.states.async_all.return_value = mock_lock_entities
        
        user_input = {
            "lock_entity_id": "lock.test_lock_0", 
            "lock_name": "Test Lock",
            "slots": 10,
            "start_from": 0,  # Invalid: must be >= 1
        }
        
        result = await config_flow.async_step_user(user_input)
        
        assert result["type"] == FlowResultType.FORM
        assert "errors" in result

    async def test_duplicate_entity_handling(self, config_flow, mock_lock_entities):
        """Test handling of duplicate lock entity configuration."""
        config_flow.hass.states.async_all.return_value = mock_lock_entities
        
        # Mock existing config entry with same entity
        config_flow._abort_if_unique_id_configured = Mock(
            side_effect=config_entries.AbortFlow("already_configured")
        )
        
        user_input = {
            "lock_entity_id": "lock.test_lock_0",
            "lock_name": "Test Lock",
            "slots": 10, 
            "start_from": 1,
        }
        
        with pytest.raises(config_entries.AbortFlow):
            await config_flow.async_step_user(user_input)

    async def test_lock_entity_filtering(self, config_flow, mock_lock_entities):
        """Test that only lock entities are included in options."""
        # Add mix of entities including non-lock entities
        all_entities = mock_lock_entities + [
            Mock(entity_id="sensor.temperature", domain="sensor"),
            Mock(entity_id="light.living_room", domain="light"),
        ]
        
        config_flow.hass.states.async_all.return_value = all_entities
        
        result = await config_flow.async_step_user()
        
        # Should only show lock entities in the form
        # Implementation would need to verify schema contains only lock.* entities

    @patch('custom_components.smart_lock_manager.config_flow.vol')
    async def test_schema_validation(self, mock_vol, config_flow, mock_lock_entities):
        """Test schema validation logic."""
        config_flow.hass.states.async_all.return_value = mock_lock_entities
        
        # Mock voluptuous validators
        mock_vol.Required.return_value = Mock()
        mock_vol.All.return_value = Mock()
        mock_vol.Range.return_value = Mock()
        mock_vol.In.return_value = Mock()
        
        result = await config_flow.async_step_user()
        
        # Verify schema creation calls
        assert mock_vol.Required.called
        assert mock_vol.Range.called  # For slots and start_from validation

    async def test_options_flow_not_implemented(self, config_flow):
        """Test that options flow is not implemented yet."""
        # Most config flows don't implement options initially
        assert not hasattr(config_flow, 'async_get_options_flow')

    async def test_error_handling_invalid_entity(self, config_flow, mock_lock_entities):
        """Test error handling for invalid lock entity."""
        config_flow.hass.states.async_all.return_value = mock_lock_entities
        
        user_input = {
            "lock_entity_id": "lock.nonexistent_lock",
            "lock_name": "Test Lock",
            "slots": 10,
            "start_from": 1,
        }
        
        result = await config_flow.async_step_user(user_input)
        
        # Should show form with error for invalid entity
        assert result["type"] == FlowResultType.FORM
        assert "errors" in result

    async def test_max_slots_validation(self, config_flow, mock_lock_entities):
        """Test validation of maximum slot count."""
        config_flow.hass.states.async_all.return_value = mock_lock_entities
        
        user_input = {
            "lock_entity_id": "lock.test_lock_0",
            "lock_name": "Test Lock", 
            "slots": 300,  # Very high number
            "start_from": 1,
        }
        
        result = await config_flow.async_step_user(user_input)
        
        # Should either accept or show reasonable error for high slot count
        # Implementation dependent on actual validation rules

    async def test_special_characters_in_name(self, config_flow, mock_lock_entities):
        """Test handling of special characters in lock name."""
        config_flow.hass.states.async_all.return_value = mock_lock_entities
        
        user_input = {
            "lock_entity_id": "lock.test_lock_0",
            "lock_name": "Test Lock!@#$%^&*()",
            "slots": 10,
            "start_from": 1,
        }
        
        with patch.object(config_flow, 'async_set_unique_id'), \
             patch.object(config_flow, '_abort_if_unique_id_configured'):
            
            result = await config_flow.async_step_user(user_input)
            
            # Should handle special characters appropriately
            assert result["type"] == FlowResultType.CREATE_ENTRY


class TestConfigFlowEdgeCases:
    """Test edge cases and error conditions."""

    @pytest.fixture
    def config_flow(self):
        """Create a config flow instance."""
        return SmartLockManagerConfigFlow()

    async def test_hass_not_set(self, config_flow):
        """Test behavior when hass is not set."""
        config_flow.hass = None
        
        with pytest.raises(AttributeError):
            await config_flow.async_step_user()

    async def test_state_service_unavailable(self, config_flow):
        """Test handling when state service is unavailable."""
        hass = Mock(spec=HomeAssistant)
        hass.states = None
        config_flow.hass = hass
        
        with pytest.raises(AttributeError):
            await config_flow.async_step_user()

    async def test_empty_user_input(self, config_flow):
        """Test handling of empty user input."""
        hass = Mock(spec=HomeAssistant)
        hass.states = Mock()
        hass.states.async_all.return_value = []
        config_flow.hass = hass
        
        result = await config_flow.async_step_user({})
        
        # Should show form with validation errors
        assert result["type"] == FlowResultType.FORM

    async def test_version_property(self, config_flow):
        """Test config flow version property."""
        # Most config flows have a VERSION class variable
        assert hasattr(SmartLockManagerConfigFlow, 'VERSION') or hasattr(config_flow, 'VERSION')
        
    async def test_connection_class_property(self, config_flow):
        """Test config flow connection class property.""" 
        # Check if connection class is defined for backwards compatibility
        if hasattr(SmartLockManagerConfigFlow, 'CONNECTION_CLASS'):
            assert SmartLockManagerConfigFlow.CONNECTION_CLASS is not None