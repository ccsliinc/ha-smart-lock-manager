"""Tests for Smart Lock Manager lock services."""

from unittest.mock import AsyncMock, patch

import pytest

from custom_components.smart_lock_manager.services.lock_services import LockServices

# Fixtures are imported from conftest.py automatically


class TestLockServices:
    """Test lock service operations."""

    @pytest.mark.asyncio
    async def test_set_code_basic(self, setup_hass_data, service_call_mock):
        """Test basic code setting service."""
        hass = setup_hass_data

        # Mock service call data
        service_call_mock.data = {
            "entity_id": "lock.test_lock",
            "code_slot": 1,
            "usercode": "1234",
        }

        await LockServices.set_code(hass, service_call_mock)

        # Verify code was set in the lock object
        lock = hass.data["smart_lock_manager"]["test_entry_123"]["primary_lock"]
        assert lock.code_slots[1].pin_code == "1234"
        assert lock.code_slots[1].is_active

    @pytest.mark.asyncio
    async def test_set_code_advanced(self, setup_hass_data, service_call_mock):
        """Test advanced code setting service."""
        hass = setup_hass_data

        # Mock advanced service call data
        service_call_mock.data = {
            "entity_id": "lock.test_lock",
            "code_slot": 2,
            "usercode": "5678",
            "code_slot_name": "Weekend User",
            "allowed_days": [5, 6],  # Weekend
            "allowed_hours": [8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20],
            "max_uses": 10,
            "notify_on_use": True,
        }

        with patch(
            "custom_components.smart_lock_manager.storage.lock_storage.save_lock_data"
        ) as mock_save:
            await LockServices.set_code_advanced(hass, service_call_mock)

        # Verify advanced code was set
        lock = hass.data["smart_lock_manager"]["test_entry_123"]["primary_lock"]
        slot = lock.code_slots[2]
        assert slot.pin_code == "5678"
        assert slot.user_name == "Weekend User"
        assert slot.allowed_days == [5, 6]
        assert slot.max_uses == 10
        assert slot.notify_on_use is True

        # Verify storage was called
        mock_save.assert_called_once()

    @pytest.mark.asyncio
    async def test_clear_code(self, setup_hass_data, service_call_mock):
        """Test code clearing service."""
        hass = setup_hass_data
        lock = hass.data["smart_lock_manager"]["test_entry_123"]["primary_lock"]

        # Set up a code first
        lock.set_code(1, "1234", "Test User")
        assert lock.code_slots[1].is_active

        # Mock service call to clear code
        service_call_mock.data = {"entity_id": "lock.test_lock", "code_slot": 1}

        await LockServices.clear_code(hass, service_call_mock)

        # Verify code was cleared
        slot = lock.code_slots[1]
        assert slot.pin_code is None
        assert slot.user_name is None
        assert not slot.is_active

    @pytest.mark.asyncio
    async def test_service_with_invalid_entity(
        self, setup_hass_data, service_call_mock
    ):
        """Test service calls with invalid entity_id."""
        hass = setup_hass_data

        # Mock service call with non-existent entity
        service_call_mock.data = {
            "entity_id": "lock.nonexistent_lock",
            "code_slot": 1,
            "usercode": "1234",
        }

        # Should handle gracefully (no exception)
        await LockServices.set_code(hass, service_call_mock)

        # No codes should be set in our test lock
        lock = hass.data["smart_lock_manager"]["test_entry_123"]["primary_lock"]
        assert not lock.code_slots[1].is_active
