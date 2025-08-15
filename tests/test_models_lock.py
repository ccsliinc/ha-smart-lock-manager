"""Comprehensive tests for Smart Lock Manager models."""

from datetime import datetime, timedelta
from unittest.mock import patch

import pytest

from custom_components.smart_lock_manager.models.lock import (
    CodeSlot,
    LockSettings,
    SmartLockManagerLock,
)

# Fixtures are imported from conftest.py automatically


class TestCodeSlot:
    """Test CodeSlot functionality."""

    def test_code_slot_creation(self):
        """Test basic CodeSlot creation."""
        slot = CodeSlot(slot_number=1, pin_code="1234", user_name="Test User")

        assert slot.slot_number == 1
        assert slot.pin_code == "1234"
        assert slot.user_name == "Test User"
        assert slot.is_active is False  # Default
        assert slot.use_count == 0
        assert slot.max_uses == -1

    def test_is_valid_now_basic(self, sample_code_slot):
        """Test basic validity checking."""
        # Inactive slot should not be valid
        sample_code_slot.is_active = False
        assert not sample_code_slot.is_valid_now()

        # Active slot with PIN should be valid
        sample_code_slot.is_active = True
        assert sample_code_slot.is_valid_now()

        # Active slot without PIN should not be valid
        sample_code_slot.pin_code = None
        assert not sample_code_slot.is_valid_now()

    @patch("custom_components.smart_lock_manager.models.lock.datetime")
    def test_is_valid_now_time_restrictions(self, mock_datetime, weekend_code_slot):
        """Test time-based validity checking."""
        # Mock current time as Monday (weekday=0)
        mock_datetime.now.return_value = datetime(2024, 1, 1, 10, 0)  # Monday

        # Weekend-only slot should not be valid on Monday
        assert not weekend_code_slot.is_valid_now()

        # Mock current time as Saturday (weekday=5)
        mock_datetime.now.return_value = datetime(2024, 1, 6, 10, 0)  # Saturday

        # Weekend-only slot should be valid on Saturday
        assert weekend_code_slot.is_valid_now()

    @patch("custom_components.smart_lock_manager.models.lock.datetime")
    def test_is_valid_now_hour_restrictions(self, mock_datetime, sample_code_slot):
        """Test hour-based validity checking."""
        # Set hour restrictions (9-17, business hours)
        sample_code_slot.allowed_hours = list(range(9, 18))  # 9-17

        # Mock time as 8 AM (hour=8, before allowed hours)
        mock_datetime.now.return_value = datetime(2024, 1, 1, 8, 0)
        assert not sample_code_slot.is_valid_now()

        # Mock time as 10 AM (hour=10, within allowed hours)
        mock_datetime.now.return_value = datetime(2024, 1, 1, 10, 0)
        assert sample_code_slot.is_valid_now()

        # Mock time as 6 PM (hour=18, after allowed hours)
        mock_datetime.now.return_value = datetime(2024, 1, 1, 18, 0)
        assert not sample_code_slot.is_valid_now()

    def test_usage_limits(self, usage_limited_slot):
        """Test usage limit functionality."""
        # Slot with 3 uses out of 5 should be valid
        assert usage_limited_slot.is_valid_now()

        # After reaching max uses, should not be valid
        usage_limited_slot.use_count = 5
        assert not usage_limited_slot.is_valid_now()

        # After exceeding max uses, should not be valid
        usage_limited_slot.use_count = 6
        assert not usage_limited_slot.is_valid_now()

    def test_should_disable_expired(self, expired_code_slot):
        """Test automatic disabling of expired slots."""
        assert expired_code_slot.should_disable()

    def test_should_disable_max_uses(self, usage_limited_slot):
        """Test automatic disabling when max uses reached."""
        # Not at max uses yet
        assert not usage_limited_slot.should_disable()

        # At max uses
        usage_limited_slot.use_count = 5
        assert usage_limited_slot.should_disable()

        # Over max uses
        usage_limited_slot.use_count = 6
        assert usage_limited_slot.should_disable()

    def test_increment_usage(self, sample_code_slot):
        """Test usage counter increment."""
        initial_count = sample_code_slot.use_count
        initial_time = sample_code_slot.last_used

        sample_code_slot.increment_usage()

        assert sample_code_slot.use_count == initial_count + 1
        assert sample_code_slot.last_used is not None
        assert sample_code_slot.last_used != initial_time

    def test_reset_usage(self, usage_limited_slot):
        """Test usage counter reset."""
        usage_limited_slot.use_count = 3
        usage_limited_slot.reset_usage()
        assert usage_limited_slot.use_count == 0


class TestSmartLockManagerLock:
    """Test SmartLockManagerLock functionality."""

    def test_lock_creation(self, sample_lock):
        """Test basic lock creation and slot initialization."""
        assert sample_lock.lock_name == "Test Lock"
        assert sample_lock.lock_entity_id == "lock.test_lock"
        assert sample_lock.slots == 10
        assert len(sample_lock.code_slots) == 10

        # All slots should be initialized as empty
        for slot_num in range(1, 11):
            slot = sample_lock.code_slots[slot_num]
            assert slot.slot_number == slot_num
            assert slot.is_active is False
            assert slot.pin_code is None

    def test_set_code_basic(self, sample_lock):
        """Test basic code setting."""
        success = sample_lock.set_code(1, "1234", "Test User")

        assert success
        slot = sample_lock.code_slots[1]
        assert slot.pin_code == "1234"
        assert slot.user_name == "Test User"
        assert slot.is_active is True
        assert slot.created_at is not None

    def test_set_code_advanced(self, sample_lock):
        """Test advanced code setting with scheduling."""
        start_date = datetime.now()
        end_date = datetime.now() + timedelta(days=30)
        allowed_hours = [9, 10, 11, 12, 13, 14, 15, 16, 17]
        allowed_days = [0, 1, 2, 3, 4]  # Weekdays

        success = sample_lock.set_code(
            slot_number=2,
            pin_code="5678",
            user_name="Business User",
            start_date=start_date,
            end_date=end_date,
            allowed_hours=allowed_hours,
            allowed_days=allowed_days,
            max_uses=50,
            notify_on_use=True,
        )

        assert success
        slot = sample_lock.code_slots[2]
        assert slot.pin_code == "5678"
        assert slot.user_name == "Business User"
        assert slot.start_date == start_date
        assert slot.end_date == end_date
        assert slot.allowed_hours == allowed_hours
        assert slot.allowed_days == allowed_days
        assert slot.max_uses == 50
        assert slot.notify_on_use is True

    def test_clear_code(self, lock_with_slots):
        """Test code clearing."""
        # Verify slot 1 has a code
        assert lock_with_slots.code_slots[1].is_active
        assert lock_with_slots.code_slots[1].pin_code == "1234"

        success = lock_with_slots.clear_code(1)

        assert success
        slot = lock_with_slots.code_slots[1]
        assert slot.pin_code is None
        assert slot.user_name is None
        assert slot.is_active is False
        assert slot.created_at is None

    def test_get_active_codes_count(self, lock_with_slots):
        """Test active code counting."""
        # Should have 2 active slots (1 and 2 from fixture)
        count = lock_with_slots.get_active_codes_count()
        assert count == 3  # sample_code_slot, weekend_code_slot, expired_code_slot

    def test_get_valid_slots_now(self, lock_with_slots):
        """Test getting currently valid slots."""
        with patch(
            "custom_components.smart_lock_manager.models.lock.datetime"
        ) as mock_dt:
            # Mock current time as Saturday morning (weekend slot should be valid)
            mock_dt.now.return_value = datetime(2024, 1, 6, 10, 0)  # Saturday

            valid_slots = lock_with_slots.get_valid_slots_now()

            # Should have slot 1 (always valid) and slot 2 (valid on weekends)
            assert 1 in valid_slots
            assert 2 in valid_slots
            # Slot 3 should not be valid (expired)
            assert 3 not in valid_slots

    def test_check_and_update_slot_validity(self, lock_with_slots):
        """Test automatic slot validity updates."""
        # Before update, expired slot should be active
        assert lock_with_slots.code_slots[3].is_active

        changed_slots = lock_with_slots.check_and_update_slot_validity()

        # Expired slot should be disabled
        assert not lock_with_slots.code_slots[3].is_active
        assert 3 in changed_slots

    def test_resize_slots_reduce(self, sample_lock):
        """Test reducing slot count."""
        # Start with 10 slots, reduce to 5
        success = sample_lock.resize_slots(5)

        assert success
        assert sample_lock.slots == 5
        assert len(sample_lock.code_slots) == 5

        # Slots 1-5 should exist, 6-10 should be gone
        for slot_num in range(1, 6):
            assert slot_num in sample_lock.code_slots
        for slot_num in range(6, 11):
            assert slot_num not in sample_lock.code_slots

    def test_resize_slots_increase(self, sample_lock):
        """Test increasing slot count."""
        # Start with 10 slots, increase to 15
        success = sample_lock.resize_slots(15)

        assert success
        assert sample_lock.slots == 15
        assert len(sample_lock.code_slots) == 15

        # All slots 1-15 should exist
        for slot_num in range(1, 16):
            assert slot_num in sample_lock.code_slots
            assert sample_lock.code_slots[slot_num].slot_number == slot_num

    def test_sync_status_update(self, sample_lock, mock_zwave_codes):
        """Test Z-Wave sync status updates."""
        # Set up a slot with a code
        sample_lock.set_code(1, "1234", "Test User")

        # Update sync status with matching Z-Wave codes
        sample_lock.update_sync_status(mock_zwave_codes)

        # Slot 1 should be synced (codes match)
        assert sample_lock.code_slots[1].is_synced

        # Set up slot 2 with different code
        sample_lock.set_code(2, "9999", "Test User 2")
        sample_lock.update_sync_status(mock_zwave_codes)

        # Slot 2 should not be synced (code mismatch)
        assert not sample_lock.code_slots[2].is_synced
