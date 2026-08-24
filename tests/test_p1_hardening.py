"""Regression tests for the P1 hardening set (2026.8.2).

Each test pins one P1 fix so it cannot silently regress:

* the zones DATA API rejects a non-admin and never leaks ``str(err)``;
* a written code that fails read-back verification stays UNSYNCED (three-outcome
  rule: a write we could not confirm is not synced) and thus retries;
* an aware (offset-carrying) ``end_date`` no longer raises in ``is_valid_now``;
* a metadata-only edit on a zoned lock propagates to the owning zone, so the
  next mirror cycle does not revert it;
* removing a lock from a zone does not re-push the wiped codes; and
* an idle coordinator cycle does not rewrite unchanged lock storage.

Admin-gating of the six alert/monitoring-control services lives in
``test_security_pin_exposure.py`` (``test_alert_control_services_require_admin``).
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from http import HTTPStatus
from unittest.mock import AsyncMock, Mock, patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import Unauthorized

from custom_components.smart_lock_manager.const import DOMAIN, PRIMARY_LOCK
from custom_components.smart_lock_manager.dev_mock import MOCK_DB
from custom_components.smart_lock_manager.models.lock import SmartLockManagerLock
from custom_components.smart_lock_manager.models.slot import CodeSlot
from custom_components.smart_lock_manager.models.zone import Zone
from custom_components.smart_lock_manager.zone_runtime import (
    ZONE_REGISTRY_KEY,
    get_zone_for_lock,
    mirror_owning_zone_to_member,
)

DEMO_ENTITY = "lock.demo_front"
DEMO_NODE = 10
ENTRY_ID = "p1_entry"


# ---------------------------------------------------------------------------
# shared doubles
# ---------------------------------------------------------------------------
@pytest.fixture(autouse=True)
def _reset_mock_db():
    """Reset the process-global MockValueDB before and after each test."""
    MOCK_DB._codes.clear()
    MOCK_DB.fail_next(False)
    yield
    MOCK_DB._codes.clear()
    MOCK_DB.fail_next(False)


@pytest.fixture
def dev_mock_env():
    """Enable SLM_DEV_MOCK for the duration of a test."""
    prev = os.environ.get("SLM_DEV_MOCK")
    os.environ["SLM_DEV_MOCK"] = "1"
    yield
    if prev is None:
        os.environ.pop("SLM_DEV_MOCK", None)
    else:
        os.environ["SLM_DEV_MOCK"] = prev


def _make_hass() -> Mock:
    """Build a minimal hass double with data/services/bus/states."""
    hass = Mock(spec=HomeAssistant)
    hass.data = {DOMAIN: {}}
    hass.services = Mock()
    hass.services.async_call = AsyncMock()
    hass.bus = Mock()
    hass.bus.async_fire = Mock()
    hass.states = Mock()
    hass.states.get = Mock(return_value=Mock(state="locked"))
    return hass


def _demo_lock() -> SmartLockManagerLock:
    """Build a 10-slot demo lock bound to the dev-mock node."""
    return SmartLockManagerLock(
        lock_name="Demo Front",
        lock_entity_id=DEMO_ENTITY,
        slots=10,
        start_from=1,
    )


def _seed_hass(hass: Mock, lock: SmartLockManagerLock) -> None:
    """Register the lock + a store double + coordinator under a config entry."""
    store = Mock()
    store.async_save = AsyncMock()
    coordinator = Mock()
    coordinator.async_request_refresh = AsyncMock()
    hass.data[DOMAIN][ENTRY_ID] = {
        PRIMARY_LOCK: lock,
        "store": store,
        "coordinator": coordinator,
        "entry": Mock(entry_id=ENTRY_ID),
    }


def _call(data) -> Mock:
    """Build a ServiceCall double with the given payload."""
    call = Mock()
    call.data = data
    return call


# ---------------------------------------------------------------------------
# #2 zones DATA API: admin-gated + no str(err) leak
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_zones_view_rejects_non_admin() -> None:
    """A non-admin authenticated caller is rejected with Unauthorized."""
    from custom_components.smart_lock_manager.api.zones import (
        SmartLockManagerZonesView,
    )

    view = SmartLockManagerZonesView(Mock())
    request = {"hass_user": Mock(is_admin=False)}

    with pytest.raises(Unauthorized):
        await view.get(request)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_zones_view_missing_user_rejected() -> None:
    """A request with no ``hass_user`` at all is rejected, not served."""
    from custom_components.smart_lock_manager.api.zones import (
        SmartLockManagerZonesView,
    )

    view = SmartLockManagerZonesView(Mock())
    with pytest.raises(Unauthorized):
        await view.get({})  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_zones_view_500_does_not_leak_exception_text() -> None:
    """An internal failure returns a GENERIC 500, never ``str(err)``."""
    from custom_components.smart_lock_manager.api import zones as zones_mod
    from custom_components.smart_lock_manager.api.zones import (
        SmartLockManagerZonesView,
    )

    view = SmartLockManagerZonesView(Mock())
    request = {"hass_user": Mock(is_admin=True)}

    secret = "/etc/secret/path leaked internal detail"
    with patch.object(
        zones_mod, "build_zones_payload", side_effect=RuntimeError(secret)
    ):
        response = await view.get(request)  # type: ignore[arg-type]

    assert response.status == HTTPStatus.INTERNAL_SERVER_ERROR
    body = response.body.decode("utf-8")
    assert secret not in body
    assert "Internal error" in body


# ---------------------------------------------------------------------------
# #4 read-back verification: an unverifiable write is NOT synced
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_write_that_fails_readback_stays_unsynced(dev_mock_env):
    """A dispatched write whose read-back does not confirm stays unsynced.

    The write reaches the (mock) lock, but the read-back reports the slot empty
    (a lock that silently dropped the code). By the three-outcome rule the slot
    must be left UNSYNCED with a could-not-verify error so the coordinator's
    add/retry path re-attempts it — never an optimistic green.
    """
    from custom_components.smart_lock_manager.services import zwave_services
    from custom_components.smart_lock_manager.services.zwave_services import (
        ZWaveServices,
    )

    hass = _make_hass()
    lock = _demo_lock()
    lock.set_code(1, "1234", "Guest")
    _seed_hass(hass, lock)

    # Read-back always reports the slot EMPTY (write silently dropped).
    with patch.object(
        zwave_services,
        "_read_usercode",
        return_value={"usercode": None, "in_use": False},
    ):
        await ZWaveServices.sync_slot_to_zwave(
            hass,
            _call({"entity_id": DEMO_ENTITY, "code_slot": 1, "action": "enable"}),
        )

    slot = lock.code_slots[1]
    # The write DID dispatch (code is on the mock lock)...
    assert MOCK_DB.get_usercode(DEMO_NODE, 1) == "1234"
    # ...but read-back could not confirm it -> NOT synced, error recorded.
    assert slot.is_synced is False
    assert slot.sync_error and "could not be verified" in slot.sync_error
    # Desired-present and unmatched on the lock's *reported* state -> retried.
    actions = lock.get_slots_needing_sync({})
    assert 1 in actions["add"]


@pytest.mark.asyncio
async def test_write_that_passes_readback_is_synced(dev_mock_env):
    """Positive control: when read-back confirms, the slot IS synced.

    Proves the read-back actually GATES is_synced (not that it always fails):
    with the mock lock reflecting the write, verification passes.
    """
    from custom_components.smart_lock_manager.services.zwave_services import (
        ZWaveServices,
    )

    hass = _make_hass()
    lock = _demo_lock()
    lock.set_code(1, "1234", "Guest")
    _seed_hass(hass, lock)

    await ZWaveServices.sync_slot_to_zwave(
        hass,
        _call({"entity_id": DEMO_ENTITY, "code_slot": 1, "action": "enable"}),
    )

    slot = lock.code_slots[1]
    assert MOCK_DB.get_usercode(DEMO_NODE, 1) == "1234"
    assert slot.is_synced is True
    assert slot.sync_error is None


# ---------------------------------------------------------------------------
# #5 timezone-aware datetimes: an aware end_date must not crash is_valid_now
# ---------------------------------------------------------------------------
def test_aware_end_date_does_not_raise_in_is_valid_now():
    """An offset-carrying (aware) end_date is comparable against now.

    Before the fix, ``now = datetime.now()`` (naive) vs an aware end_date raised
    ``TypeError`` inside the coordinator update and aborted the whole sync cycle.
    """
    future = datetime.now(timezone.utc) + timedelta(days=1)
    slot = CodeSlot(slot_number=1, pin_code="1234", is_active=True, end_date=future)
    # No raise, and not yet expired.
    assert slot.is_valid_now() is True
    assert slot.should_disable() is False

    past = datetime.now(timezone.utc) - timedelta(days=1)
    slot.end_date = past
    assert slot.is_valid_now() is False
    assert slot.should_disable() is True


def test_get_slots_needing_sync_survives_aware_dates():
    """The coordinator's sync computation does not crash on aware dates."""
    lock = _demo_lock()
    future = datetime.now(timezone.utc) + timedelta(days=1)
    lock.set_code(1, "1234", "Guest", end_date=future)
    # This is the exact call the coordinator makes each cycle.
    actions = lock.get_slots_needing_sync({})
    assert 1 in actions["add"]


# ---------------------------------------------------------------------------
# #6 metadata-only edit on a zoned lock propagates to the owning zone
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_zoned_metadata_edit_propagates_to_zone(dev_mock_env):
    """A same-PIN metadata edit updates the zone, so a mirror won't revert it."""
    from custom_components.smart_lock_manager import storage as storage_pkg
    from custom_components.smart_lock_manager.services.lock_services import (
        LockServices,
    )

    hass = _make_hass()
    lock = _demo_lock()
    lock.set_code(1, "1234", "Guest")
    _seed_hass(hass, lock)

    old_end = datetime(2026, 1, 1, tzinfo=timezone.utc)
    zone = Zone(
        zone_id="z1",
        name="Front",
        member_lock_entity_ids=[DEMO_ENTITY],
        slots=10,
        start_from=1,
    )
    zone.code_slots[1] = CodeSlot(
        slot_number=1,
        pin_code="1234",
        user_name="Guest",
        is_active=True,
        end_date=old_end,
    )
    hass.data[ZONE_REGISTRY_KEY] = {"z1": zone}

    with patch.object(storage_pkg, "save_zone", AsyncMock()):
        await LockServices.set_code_advanced(
            hass,
            _call(
                {
                    "entity_id": DEMO_ENTITY,
                    "code_slot": 1,
                    "usercode": "1234",  # UNCHANGED PIN -> metadata fast path
                    "code_slot_name": "Guest",
                    "end_date": "2027-06-15T00:00",  # changed metadata
                }
            ),
        )

    new_end = datetime(2027, 6, 15, tzinfo=timezone.utc)
    # The zone's canonical slot picked up the new end_date...
    assert zone.code_slots[1].end_date == new_end
    # ...so the next mirror cycle projects the NEW value back, not a revert.
    zone.mirror_to_member(lock)
    assert lock.code_slots[1].end_date == new_end


# ---------------------------------------------------------------------------
# #7 remove-from-zone does not re-push the wiped codes
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_remove_from_zone_unhomes_before_wipe_no_repush(dev_mock_env):
    """After removal the lock is unhomed and its codes are gone, with no re-push.

    The membership is dropped BEFORE the hardware wipe, so a coordinator mirror
    interleaving during the wipe finds the lock unhomed and cannot re-copy the
    zone's canonical codes back onto it.
    """
    from custom_components.smart_lock_manager.services import zone_services
    from custom_components.smart_lock_manager.services.zone_services import (
        ZoneServices,
    )

    hass = _make_hass()
    lock = _demo_lock()
    lock.set_code(1, "1234", "Guest")
    _seed_hass(hass, lock)
    MOCK_DB.set_usercode(DEMO_NODE, 1, "1234")

    zone = Zone(
        zone_id="z1",
        name="Front",
        member_lock_entity_ids=[DEMO_ENTITY],
        slots=10,
        start_from=1,
    )
    zone.code_slots[1] = CodeSlot(
        slot_number=1, pin_code="1234", user_name="Guest", is_active=True
    )
    hass.data[ZONE_REGISTRY_KEY] = {"z1": zone}

    with patch.object(zone_services, "save_zone", AsyncMock()):
        await ZoneServices.remove_lock_from_zone(
            hass,
            _call({"zone_id": "z1", "lock_entity_id": DEMO_ENTITY}),
        )

    # Lock is unhomed, hardware wiped, in-memory definition blanked.
    assert get_zone_for_lock(hass, DEMO_ENTITY) is None
    assert MOCK_DB.get_usercode(DEMO_NODE, 1) is None
    assert lock.code_slots[1].pin_code is None
    # A subsequent mirror cycle is a no-op (unhomed) -> nothing to re-push.
    assert mirror_owning_zone_to_member(hass, lock) is None
    assert lock.get_slots_needing_sync({})["add"] == []


# ---------------------------------------------------------------------------
# #8 idle coordinator cycle does not rewrite unchanged storage
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_idle_coordinator_cycle_skips_resave(hass: HomeAssistant):
    """Two idle cycles persist the lock at most once (dirty-check)."""
    from custom_components.smart_lock_manager import coordinator as coord_mod
    from custom_components.smart_lock_manager.coordinator import (
        SmartLockManagerDataUpdateCoordinator,
    )

    lock = SmartLockManagerLock(
        lock_name="Idle", lock_entity_id="lock.idle", slots=3, start_from=1
    )
    entry = Mock()
    entry.entry_id = "idle_entry"
    entry.data = {"lock_name": "Idle"}

    store = Mock()
    store.async_save = AsyncMock()
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {
        PRIMARY_LOCK: lock,
        "store": store,
        "entry": entry,
    }

    coord = SmartLockManagerDataUpdateCoordinator(hass, entry)

    save = AsyncMock()
    with (
        patch.object(coord_mod, "read_zwave_codes", return_value={}),
        patch.object(coord_mod, "mirror_owning_zone_to_member", return_value=None),
        patch("custom_components.smart_lock_manager._save_lock_data", save),
    ):
        await coord._async_update_data()
        await coord._async_update_data()

    # First cycle persists the baseline; the identical second cycle is skipped.
    assert save.await_count == 1
