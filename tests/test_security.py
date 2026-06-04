"""Security-focused test cases for Smart Lock Manager.

These tests assert the *current* security contract of the service layer:

* The lock model (``SmartLockManagerLock.set_code``) is the single source of
  truth for PIN validation — it accepts only 4-8 digit numeric PINs and
  silently refuses (returns ``False``) anything else. The service handler does
  not raise on bad PINs; it logs and leaves the slot unchanged.
* Slot resolution happens inline against ``hass.data[DOMAIN]`` (there is no
  ``get_lock_from_entity`` helper). An unknown ``entity_id`` is a safe no-op.
* PIN-prefix collisions (Kwikset behavior) are the one rejection that *does*
  raise — as ``HomeAssistantError``.
* PIN codes are never emitted to logs in plaintext.
* Persistence failures are swallowed by ``save_lock_data`` and never surface
  to the caller.
"""

import logging
from datetime import datetime
from unittest.mock import AsyncMock, Mock

import pytest
from homeassistant.core import HomeAssistant, ServiceCall

from custom_components.smart_lock_manager.const import DOMAIN, PRIMARY_LOCK
from custom_components.smart_lock_manager.models.lock import (
    CodeSlot,
    SmartLockManagerLock,
)
from custom_components.smart_lock_manager.services.lock_services import LockServices

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
    """Create an empty mock lock for testing."""
    return SmartLockManagerLock(
        lock_name="Test Lock",
        lock_entity_id="lock.test_lock",
        slots=10,
        start_from=1,
    )


@pytest.fixture
def store_mock():
    """Create a store mock with an awaitable async_save."""
    store = Mock()
    store.async_save = AsyncMock()
    return store


@pytest.fixture
def setup_hass_data(mock_hass, mock_lock, store_mock):
    """Seed hass.data so the service layer can resolve the lock inline."""
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


class TestSecurityValidation:
    """Test security validation and input sanitization."""

    async def test_pin_code_injection_attack(
        self, mock_hass, mock_lock, setup_hass_data
    ):
        """Malicious (non-numeric) PINs are rejected; the slot stays empty."""
        malicious_pins = [
            "'; DROP TABLE users; --",
            "<script>alert('xss')</script>",
            "../../../../etc/passwd",
            "1234\0",
            "1234\n\r",
            "1234%00",
        ]

        for malicious_pin in malicious_pins:
            service_call = _call(
                {
                    "entity_id": "lock.test_lock",
                    "code_slot": 1,
                    "usercode": malicious_pin,
                    "code_slot_name": "Test User",
                }
            )

            # No exception: the model refuses non-numeric PINs and the handler
            # logs-and-returns, leaving the slot unconfigured.
            await LockServices.set_code_advanced(mock_hass, service_call)
            assert mock_lock.code_slots[1].pin_code is None
            assert mock_lock.code_slots[1].is_active is False

    async def test_user_name_stored_verbatim_pin_safe(
        self, mock_hass, mock_lock, setup_hass_data
    ):
        """User names are stored verbatim (escaping is the frontend's job).

        The security guarantee here is that a hostile *name* cannot break the
        write path or leak the PIN — not that the backend rewrites the name.
        """
        hostile_name = "<script>alert('xss')</script>"
        service_call = _call(
            {
                "entity_id": "lock.test_lock",
                "code_slot": 1,
                "usercode": "1234",
                "code_slot_name": hostile_name,
            }
        )

        await LockServices.set_code_advanced(mock_hass, service_call)

        # Valid PIN -> slot configured; the name is preserved exactly as given.
        assert mock_lock.code_slots[1].pin_code == "1234"
        assert mock_lock.code_slots[1].user_name == hostile_name

    async def test_pin_length_validation(self, mock_hass, mock_lock, setup_hass_data):
        """Reject PINs outside the 4-8 digit window so no active code results.

        Non-empty too-short / too-long PINs are refused outright (pin stays
        ``None``). An empty PIN is treated as "no code": it is stored as an
        empty string and the slot is left inactive. In every case the slot
        must not become an active, usable code.
        """
        invalid_pins = ["", "1", "12", "123", "123456789", "1234567890123456"]

        for invalid_pin in invalid_pins:
            # Reset the target slot between iterations to a clean state.
            mock_lock.code_slots[1] = CodeSlot(slot_number=1)

            service_call = _call(
                {
                    "entity_id": "lock.test_lock",
                    "code_slot": 1,
                    "usercode": invalid_pin,
                    "code_slot_name": "Test User",
                }
            )

            await LockServices.set_code_advanced(mock_hass, service_call)
            slot = mock_lock.code_slots[1]
            assert slot.is_active is False
            assert not slot.pin_code  # None (refused) or "" (empty == no code)

    async def test_pin_character_validation(
        self, mock_hass, mock_lock, setup_hass_data
    ):
        """Non-numeric PINs are refused (slot unchanged)."""
        invalid_pins = ["abcd", "12ab", "12!@", "12 34", "12-34", "12.34"]

        for invalid_pin in invalid_pins:
            service_call = _call(
                {
                    "entity_id": "lock.test_lock",
                    "code_slot": 1,
                    "usercode": invalid_pin,
                    "code_slot_name": "Test User",
                }
            )

            await LockServices.set_code_advanced(mock_hass, service_call)
            assert mock_lock.code_slots[1].pin_code is None

    async def test_slot_number_bounds_are_noop(
        self, mock_hass, mock_lock, setup_hass_data
    ):
        """Out-of-range slot numbers never create a slot or persist data."""
        invalid_slots = [-1, 0, 99999, -99999]

        for invalid_slot in invalid_slots:
            store = mock_hass.data[DOMAIN][setup_hass_data]["store"]
            store.async_save.reset_mock()

            service_call = _call(
                {
                    "entity_id": "lock.test_lock",
                    "code_slot": invalid_slot,
                    "usercode": "1234",
                    "code_slot_name": "Test User",
                }
            )

            # No exception; the slot is not in the lock's range so nothing is
            # written or saved.
            await LockServices.set_code_advanced(mock_hass, service_call)
            assert invalid_slot not in mock_lock.code_slots
            store.async_save.assert_not_called()

    async def test_max_uses_values_accepted(
        self, mock_hass, mock_lock, setup_hass_data
    ):
        """max_uses is stored as given; values <= 0 simply mean "no limit".

        The current contract treats only ``max_uses > 0`` as an enforced cap
        (see ``CodeSlot.should_disable``); -1/0/negative are valid sentinels
        for "unlimited" and are accepted without error.
        """
        for value in [-5, -99999, 0, -1]:
            service_call = _call(
                {
                    "entity_id": "lock.test_lock",
                    "code_slot": 1,
                    "usercode": "1234",
                    "code_slot_name": "Test User",
                    "max_uses": value,
                }
            )

            await LockServices.set_code_advanced(mock_hass, service_call)
            assert mock_lock.code_slots[1].pin_code == "1234"
            assert mock_lock.code_slots[1].max_uses == value

    async def test_prefix_collision_is_rejected(
        self, mock_hass, mock_lock, setup_hass_data
    ):
        """PIN-prefix collisions raise HomeAssistantError (Kwikset safety)."""
        from homeassistant.exceptions import HomeAssistantError

        # Seed slot 2 with a 4-digit PIN.
        mock_lock.code_slots[2].pin_code = "1234"
        mock_lock.code_slots[2].user_name = "Existing"
        mock_lock.code_slots[2].is_active = True

        # Slot 1 with a PIN sharing the same first 4 digits must be rejected.
        service_call = _call(
            {
                "entity_id": "lock.test_lock",
                "code_slot": 1,
                "usercode": "12345",
                "code_slot_name": "Collider",
            }
        )

        with pytest.raises(HomeAssistantError):
            await LockServices.set_code_advanced(mock_hass, service_call)


class TestLoggingSecurity:
    """Test that sensitive information is not logged."""

    async def test_pin_codes_not_logged_plaintext(
        self, mock_hass, mock_lock, setup_hass_data, caplog
    ):
        """A successful code set must not emit the PIN in plaintext logs."""
        caplog.set_level(logging.DEBUG)

        service_call = _call(
            {
                "entity_id": "lock.test_lock",
                "code_slot": 1,
                "usercode": "1234",
                "code_slot_name": "Test User",
            }
        )

        await LockServices.set_code_advanced(mock_hass, service_call)

        log_text = "\n".join(record.getMessage() for record in caplog.records)
        assert "1234" not in log_text, "PIN code found in logs!"

    async def test_sensitive_data_masking(
        self, mock_hass, mock_lock, setup_hass_data, caplog
    ):
        """Neither old nor new PIN values appear in logs on update."""
        caplog.set_level(logging.DEBUG)

        # Pre-existing slot with an old PIN (distinct prefix from the new one).
        mock_lock.code_slots[1] = CodeSlot(
            slot_number=1,
            pin_code="9876",
            user_name="Sensitive User",
            is_active=True,
            created_at=datetime.now(),
        )

        service_call = _call(
            {
                "entity_id": "lock.test_lock",
                "code_slot": 1,
                "usercode": "5432",
                "code_slot_name": "Updated User",
            }
        )

        await LockServices.set_code_advanced(mock_hass, service_call)

        log_text = "\n".join(record.getMessage() for record in caplog.records)
        assert "9876" not in log_text, "Old PIN code found in logs!"
        assert "5432" not in log_text, "New PIN code found in logs!"


class TestAccessControlSecurity:
    """Test access control and authorization."""

    async def test_unauthorized_entity_access(self, mock_hass):
        """An unconfigured entity resolves to no lock and is a safe no-op."""
        service_call = _call(
            {
                "entity_id": "lock.unauthorized_lock",
                "code_slot": 1,
                "usercode": "1234",
                "code_slot_name": "Hacker",
            }
        )

        # hass.data has no DOMAIN entries -> no lock found -> logs and returns.
        await LockServices.set_code_advanced(mock_hass, service_call)

    async def test_cross_lock_access_prevention(self, mock_hass, store_mock):
        """A request for an unconfigured lock cannot mutate a different lock."""
        lock1 = SmartLockManagerLock(
            lock_name="Lock 1",
            lock_entity_id="lock.lock1",
            slots=10,
            start_from=1,
        )

        mock_hass.data[DOMAIN] = {
            "entry1": {
                PRIMARY_LOCK: lock1,
                "store": store_mock,
                "coordinator": Mock(),
                "entry": Mock(entry_id="entry1"),
            }
        }

        # Target lock.lock2, which is not configured at all.
        service_call = _call(
            {
                "entity_id": "lock.lock2",
                "code_slot": 1,
                "usercode": "1234",
                "code_slot_name": "Hacker",
            }
        )

        await LockServices.set_code_advanced(mock_hass, service_call)

        # lock1 must be untouched, and nothing persisted.
        assert lock1.code_slots[1].pin_code is None
        store_mock.async_save.assert_not_called()


class TestDataIntegrity:
    """Test data integrity and consistency."""

    async def test_concurrent_modifications_integrity(
        self, mock_hass, mock_lock, setup_hass_data
    ):
        """Sequential code sets keep the slot collection well-formed."""
        original_slot_count = len(mock_lock.code_slots)

        # Distinct first-4-digit prefixes avoid spurious collision rejections.
        for i in range(5):
            service_call = _call(
                {
                    "entity_id": "lock.test_lock",
                    "code_slot": i + 1,
                    "usercode": f"123{i}",
                    "code_slot_name": f"User {i}",
                }
            )
            await LockServices.set_code_advanced(mock_hass, service_call)

        assert isinstance(mock_lock.code_slots, dict)
        assert len(mock_lock.code_slots) >= original_slot_count
        # All five slots should now carry their PIN.
        for i in range(5):
            assert mock_lock.code_slots[i + 1].pin_code == f"123{i}"

    async def test_storage_corruption_resilience(
        self, mock_hass, mock_lock, setup_hass_data
    ):
        """A failing store save is swallowed by save_lock_data, not raised."""
        store_mock = mock_hass.data[DOMAIN][setup_hass_data]["store"]
        store_mock.async_save.side_effect = Exception("Storage corruption")

        service_call = _call(
            {
                "entity_id": "lock.test_lock",
                "code_slot": 1,
                "usercode": "1234",
                "code_slot_name": "Test User",
            }
        )

        # save_lock_data wraps async_save in try/except; the handler returns
        # normally even though persistence failed. The in-memory write stands.
        await LockServices.set_code_advanced(mock_hass, service_call)
        assert mock_lock.code_slots[1].pin_code == "1234"
        store_mock.async_save.assert_called_once()
