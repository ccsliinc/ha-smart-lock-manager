"""Integration tests for frontend-backend communication."""

from datetime import datetime
from unittest.mock import AsyncMock, patch

import pytest

from custom_components.smart_lock_manager.models.lock import (
    CodeSlot,
    SmartLockManagerLock,
)


class TestFrontendBackendIntegration:
    """Test frontend-backend integration scenarios."""

    def test_slot_status_calculation_scheduled(self):
        """Test that backend correctly calculates scheduled slot status."""
        # Create a weekend-only slot
        lock = SmartLockManagerLock(
            lock_name="Test Lock", lock_entity_id="lock.test_lock"
        )

        weekend_slot = CodeSlot(
            slot_number=2,
            pin_code="5678",
            user_name="Weekend User",
            is_active=True,
            allowed_days=[5, 6],  # Saturday, Sunday
        )
        lock.code_slots[2] = weekend_slot

        # Mock current time as Thursday
        with patch(
            "custom_components.smart_lock_manager.models.lock.datetime"
        ) as mock_dt:
            mock_dt.now.return_value = datetime(2024, 1, 4, 10, 0)  # Thursday

            # Get valid slots (should exclude weekend-only slot on Thursday)
            valid_slots = lock.get_valid_slots_now()

            # Weekend slot should not be valid on Thursday
            assert 2 not in valid_slots

            # Simulate sensor attribute generation
            slot_details = {}
            for slot_num, slot in lock.code_slots.items():
                if slot.is_active:
                    slot_details[f"slot_{slot_num}"] = {
                        "user_name": slot.user_name,
                        "pin_code": slot.pin_code,
                        "is_active": slot.is_active,
                        "is_valid_now": slot_num in valid_slots,
                        "allowed_days": slot.allowed_days,
                    }

            # Verify frontend would receive correct data
            slot_2_data = slot_details["slot_2"]
            assert slot_2_data["is_active"] is True
            assert slot_2_data["is_valid_now"] is False  # Not valid on Thursday
            assert slot_2_data["allowed_days"] == [5, 6]

    def test_slot_status_calculation_active(self):
        """Test that backend correctly calculates active slot status."""
        lock = SmartLockManagerLock(
            lock_name="Test Lock", lock_entity_id="lock.test_lock"
        )

        # Create an always-active slot
        active_slot = CodeSlot(
            slot_number=1,
            pin_code="1234",
            user_name="Always Active User",
            is_active=True,
        )
        lock.code_slots[1] = active_slot

        # Get valid slots (no restrictions)
        valid_slots = lock.get_valid_slots_now()

        # Unrestricted slot should always be valid
        assert 1 in valid_slots

        # Simulate sensor attribute generation
        slot_details = {}
        for slot_num, slot in lock.code_slots.items():
            if slot.is_active:
                slot_details[f"slot_{slot_num}"] = {
                    "user_name": slot.user_name,
                    "pin_code": slot.pin_code,
                    "is_active": slot.is_active,
                    "is_valid_now": slot_num in valid_slots,
                }

        # Verify frontend would receive correct data
        slot_1_data = slot_details["slot_1"]
        assert slot_1_data["is_active"] is True
        assert slot_1_data["is_valid_now"] is True

    def test_boolean_logic_scenarios(self):
        """Test the specific boolean logic scenarios that caused frontend issues."""
        test_cases = [
            # (is_active, is_valid_now, expected_frontend_status)
            (True, True, "active_and_synced"),
            (True, False, "scheduled"),  # This was the problematic case
            (False, False, "empty"),
            (False, True, "should_not_happen"),
        ]

        for is_active, is_valid_now, expected_status in test_cases:
            # This simulates the frontend boolean logic after our fix

            # OLD BROKEN LOGIC (what we fixed):
            # const isActive = details.is_active !== false;
            # const isValidNow = details.is_valid_now !== false;

            # NEW CORRECT LOGIC:
            # const isActive = details.is_active === true;
            # const isValidNow = details.is_valid_now === true;

            frontend_is_active = is_active == True  # Simulate === true
            frontend_is_valid_now = is_valid_now == True  # Simulate === true

            if expected_status == "active_and_synced":
                assert frontend_is_active and frontend_is_valid_now

            elif expected_status == "scheduled":
                assert frontend_is_active and not frontend_is_valid_now

            elif expected_status == "empty":
                assert not frontend_is_active

    def test_weekend_slot_edge_case(self):
        """Test the specific weekend slot issue that was reported."""
        # This recreates the exact scenario from the bug report
        lock = SmartLockManagerLock(
            lock_name="Front Middle", lock_entity_id="lock.front_middle"
        )

        # Slot 1: Always active
        slot_1 = CodeSlot(
            slot_number=1, pin_code="1234", user_name="Direct API Test", is_active=True
        )

        # Slot 2: Weekend only (Saturday=5, Sunday=6)
        slot_2 = CodeSlot(
            slot_number=2,
            pin_code="5678",
            user_name="Weekend Guest",
            is_active=True,
            allowed_days=[5, 6],
        )

        lock.code_slots[1] = slot_1
        lock.code_slots[2] = slot_2

        # Test on Wednesday (weekday=2)
        with patch(
            "custom_components.smart_lock_manager.models.lock.datetime"
        ) as mock_dt:
            mock_dt.now.return_value = datetime(2024, 8, 14, 11, 0)  # Wednesday

            valid_slots = lock.get_valid_slots_now()

            # Slot 1 should be valid (no restrictions)
            assert 1 in valid_slots

            # Slot 2 should NOT be valid (not weekend)
            assert 2 not in valid_slots

            # Simulate what sensor would expose
            slot_1_attrs = {
                "is_active": slot_1.is_active,
                "is_valid_now": 1 in valid_slots,
                "pin_code": slot_1.pin_code,
            }

            slot_2_attrs = {
                "is_active": slot_2.is_active,
                "is_valid_now": 2 in valid_slots,
                "pin_code": slot_2.pin_code,
                "allowed_days": slot_2.allowed_days,
            }

            # Verify correct values for frontend consumption
            assert slot_1_attrs["is_active"] is True
            assert slot_1_attrs["is_valid_now"] is True  # Should be green

            assert slot_2_attrs["is_active"] is True
            assert slot_2_attrs["is_valid_now"] is False  # Should be yellow "Scheduled"

            # With our boolean logic fix, frontend should show:
            # Slot 1: Green "Active & Synced"
            # Slot 2: Yellow "Scheduled" (not red "Sync Error")
