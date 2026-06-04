"""Test the Smart Lock Manager config flow.

The config flow was intentionally simplified: it no longer enumerates
``hass.states`` for locks, has no ``start_from`` field, and pushes slot-range
validation entirely into the voluptuous schema (``vol.Range(min=1, max=50)``).
The handler itself only validates that the name and entity id are non-empty,
then sets the unique id and creates the entry. These tests assert that current
behavior.
"""

from unittest.mock import Mock, patch

import pytest
import voluptuous as vol
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import AbortFlow, FlowResultType

from custom_components.smart_lock_manager.config_flow import (
    SmartLockManagerConfigFlow,
    SmartLockManagerOptionsFlow,
)


class TestSmartLockManagerConfigFlow:
    """Test the Smart Lock Manager configuration flow."""

    @pytest.fixture
    def mock_hass(self):
        """Create a mock Home Assistant instance."""
        return Mock(spec=HomeAssistant)

    @pytest.fixture
    def config_flow(self, mock_hass):
        """Create a config flow instance."""
        flow = SmartLockManagerConfigFlow()
        flow.hass = mock_hass
        return flow

    async def test_async_step_user_shows_form(self, config_flow):
        """With no input the flow shows the user form (no state enumeration)."""
        result = await config_flow.async_step_user()

        assert result["type"] == FlowResultType.FORM
        assert result["step_id"] == "user"

        # The simplified schema carries exactly name, entity, and slots.
        schema = result["data_schema"]
        assert schema is not None
        keys = {str(k) for k in schema.schema.keys()}
        assert keys == {"lock_name", "lock_entity_id", "slots"}

    async def test_async_step_user_form_submission(self, config_flow):
        """A valid submission creates an entry with name/entity/slots only."""
        user_input = {
            "lock_entity_id": "lock.test_lock_0",
            "lock_name": "My Test Lock",
            "slots": 10,
        }

        with (
            patch.object(config_flow, "async_set_unique_id") as mock_unique_id,
            patch.object(config_flow, "_abort_if_unique_id_configured") as mock_abort,
        ):
            result = await config_flow.async_step_user(user_input)

            # Unique id is keyed off the lock entity id.
            mock_unique_id.assert_called_once_with("lock.test_lock_0")
            mock_abort.assert_called_once()

            assert result["type"] == FlowResultType.CREATE_ENTRY
            assert result["title"] == "My Test Lock"
            # There is no start_from in the simplified flow.
            assert result["data"] == {
                "lock_name": "My Test Lock",
                "lock_entity_id": "lock.test_lock_0",
                "slots": 10,
            }

    async def test_missing_name_rejected(self, config_flow):
        """An empty lock name returns the form with a name_required error."""
        result = await config_flow.async_step_user(
            {"lock_entity_id": "lock.a", "lock_name": "", "slots": 10}
        )

        assert result["type"] == FlowResultType.FORM
        assert result["errors"] == {"lock_name": "name_required"}

    async def test_missing_entity_rejected(self, config_flow):
        """An empty entity id returns the form with an entity_required error."""
        result = await config_flow.async_step_user(
            {"lock_entity_id": "", "lock_name": "Test Lock", "slots": 10}
        )

        assert result["type"] == FlowResultType.FORM
        assert result["errors"] == {"lock_entity_id": "entity_required"}

    async def test_slots_range_enforced_by_schema(self, config_flow):
        """Slot-range validation lives in the schema, not the handler."""
        result = await config_flow.async_step_user()
        schema = result["data_schema"]

        # Below minimum.
        with pytest.raises(vol.Invalid):
            schema({"lock_name": "X", "lock_entity_id": "lock.a", "slots": 0})

        # Above maximum.
        with pytest.raises(vol.Invalid):
            schema({"lock_name": "X", "lock_entity_id": "lock.a", "slots": 300})

        # A valid value passes.
        validated = schema({"lock_name": "X", "lock_entity_id": "lock.a", "slots": 10})
        assert validated["slots"] == 10

    async def test_duplicate_entity_aborts(self, config_flow):
        """A duplicate entity id aborts via _abort_if_unique_id_configured."""
        config_flow._abort_if_unique_id_configured = Mock(
            side_effect=AbortFlow("already_configured")
        )

        user_input = {
            "lock_entity_id": "lock.test_lock_0",
            "lock_name": "Test Lock",
            "slots": 10,
        }

        with patch.object(config_flow, "async_set_unique_id"):
            with pytest.raises(AbortFlow):
                await config_flow.async_step_user(user_input)

    async def test_special_characters_in_name(self, config_flow):
        """Special characters in the lock name are accepted verbatim."""
        user_input = {
            "lock_entity_id": "lock.test_lock_0",
            "lock_name": "Test Lock!@#$%^&*()",
            "slots": 10,
        }

        with (
            patch.object(config_flow, "async_set_unique_id"),
            patch.object(config_flow, "_abort_if_unique_id_configured"),
        ):
            result = await config_flow.async_step_user(user_input)

            assert result["type"] == FlowResultType.CREATE_ENTRY
            assert result["title"] == "Test Lock!@#$%^&*()"

    async def test_options_flow_available(self, config_flow):
        """``async_get_options_flow`` returns a SmartLockManagerOptionsFlow."""
        assert hasattr(SmartLockManagerConfigFlow, "async_get_options_flow")

        options_flow = SmartLockManagerConfigFlow.async_get_options_flow(Mock())
        assert isinstance(options_flow, SmartLockManagerOptionsFlow)


class TestConfigFlowEdgeCases:
    """Test edge cases and metadata of the config flow."""

    @pytest.fixture
    def config_flow(self):
        """Create a config flow instance."""
        flow = SmartLockManagerConfigFlow()
        flow.hass = Mock(spec=HomeAssistant)
        return flow

    async def test_empty_user_input_shows_form(self, config_flow):
        """An empty dict is treated as a submission with missing fields."""
        result = await config_flow.async_step_user({})

        # Missing name -> form returned with an error, not a crash.
        assert result["type"] == FlowResultType.FORM
        assert "errors" in result

    async def test_version_property(self, config_flow):
        """The config flow declares a schema VERSION."""
        assert SmartLockManagerConfigFlow.VERSION == 1
