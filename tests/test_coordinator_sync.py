"""Behavior tests for the auto-sync coordinator (``coordinator.py``).

This is the Z-Wave auto-sync state machine: every critical lock-correctness bug
in this codebase (a revoked/expired/out-of-window PIN staying live on a physical
deadbolt) lived here, so these tests assert the OBSERVABLE effect of a cycle --
which ``sync_slot_to_zwave`` calls were dispatched, with which action -- rather
than the internals that produced them.

The harness runs the REAL coordinator against a REAL Home Assistant instance
(``hass`` fixture): the ``sync_slot_to_zwave`` service is registered as a
recorder stub, so an "enable" call is proof a code was pushed to the lock and a
"disable" call is proof one was pulled off it. Only two seams are patched, and
both are OWNED by this module rather than borrowed from upstream ``zwave_js``:

* ``coordinator.read_zwave_codes`` -- the mock Z-Wave surface (what the deadbolt
  currently holds), and
* ``coordinator._save_lock_data`` is left real, writing into a ``Mock`` store so
  the storage dirty-check can be counted.

The P0 invariant under test throughout: desired physical presence of a code =
``is_active AND pin_code AND is_valid_now()``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, Mock, patch

import pytest
from freezegun import freeze_time
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers.update_coordinator import UpdateFailed
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.smart_lock_manager.const import (
    DOMAIN,
    MAX_SYNC_ATTEMPTS,
    PRIMARY_LOCK,
)
from custom_components.smart_lock_manager.coordinator import (
    SmartLockManagerDataUpdateCoordinator,
)
from custom_components.smart_lock_manager.models.lock import SmartLockManagerLock

READ_PATH = "custom_components.smart_lock_manager.coordinator.read_zwave_codes"


@dataclass
class CoordEnv:
    """Everything a coordinator test needs to drive and observe one cycle.

    - coordinator: the real ``SmartLockManagerDataUpdateCoordinator``.
    - lock: the ``SmartLockManagerLock`` stored under the config entry.
    - calls: every ``sync_slot_to_zwave`` payload the cycle dispatched.
    - events: every bus event fired by the coordinator (type, data).
    - store: the storage double, so ``async_save`` calls can be counted.
    """

    hass: HomeAssistant
    coordinator: SmartLockManagerDataUpdateCoordinator
    lock: SmartLockManagerLock
    calls: List[Dict[str, Any]]
    events: List[Any]
    store: Mock

    async def cycle(
        self, zwave_codes: Optional[Dict[int, Dict[str, Any]]] = None
    ) -> Dict[str, Any]:
        """Run one coordinator update against the given Z-Wave lock contents.

        - Inputs: zwave_codes (the dict ``read_zwave_codes`` should return; the
          mock deadbolt state for this cycle). ``None`` means an empty lock.
        - Outputs: the coordinator's returned data dict.
        """
        with patch(READ_PATH, return_value=dict(zwave_codes or {})):
            data = await self.coordinator._async_update_data()
        await self.hass.async_block_till_done()
        return data

    def actions(self) -> List[tuple]:
        """Return the dispatched calls as ``(slot, action)`` tuples."""
        return [(c["code_slot"], c["action"]) for c in self.calls]

    def event_types(self) -> List[str]:
        """Return the fired event type strings."""
        return [e.event_type for e in self.events]


@pytest.fixture
async def env(hass: HomeAssistant) -> CoordEnv:
    """Build a coordinator wired to a real hass, a lock, and a service recorder."""
    entry = MockConfigEntry(domain=DOMAIN, data={"lock_name": "Test Lock"})
    entry.add_to_hass(hass)

    lock = SmartLockManagerLock(
        lock_name="Test Lock",
        lock_entity_id="lock.test_lock",
        slots=10,
        start_from=1,
    )
    store = Mock()
    store.async_save = AsyncMock()
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {
        PRIMARY_LOCK: lock,
        "store": store,
        "entry": entry,
    }

    calls: List[Dict[str, Any]] = []

    async def _record(call: ServiceCall) -> None:
        calls.append(dict(call.data))

    hass.services.async_register(DOMAIN, "sync_slot_to_zwave", _record)

    events: List[Any] = []
    for event_type in (
        "smart_lock_manager_sync_retry",
        "smart_lock_manager_sync_error",
    ):
        hass.bus.async_listen(event_type, events.append)

    coordinator = SmartLockManagerDataUpdateCoordinator(hass, entry)
    return CoordEnv(hass, coordinator, lock, calls, events, store)


def _age(slot, seconds: int) -> None:
    """Backdate a slot's ``last_sync_attempt`` by ``seconds``.

    The model applies a 60-second post-write grace period, so a slot written
    "just now" is deliberately skipped. Backdating past that grace is how a test
    reaches the coordinator's own exponential-backoff decision.
    """
    slot.last_sync_attempt = datetime.now(timezone.utc) - timedelta(seconds=seconds)


# ---------------------------------------------------------------------------
# _async_update_data: contract, failure wrapping, state integrity
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_returns_connection_payload(env: CoordEnv) -> None:
    """A healthy cycle returns the coordinator's data contract."""
    data = await env.cycle()

    assert data == {
        "user_codes": {},
        "lock_state": "unknown",
        "connection_status": True,
    }


@pytest.mark.asyncio
async def test_update_without_lock_object_returns_empty(env: CoordEnv) -> None:
    """No lock under the config entry: empty dict, no sync, no storage write."""
    env.hass.data[DOMAIN][env.coordinator.entry.entry_id] = {}

    data = await env.cycle()

    assert data == {}
    assert env.calls == []
    assert env.store.async_save.await_count == 0


@pytest.mark.asyncio
async def test_read_failure_is_wrapped_in_update_failed(env: CoordEnv) -> None:
    """A raising Z-Wave read surfaces as UpdateFailed, chained to its cause."""
    boom = RuntimeError("z-wave mesh unreachable")

    with patch(READ_PATH, side_effect=boom):
        with pytest.raises(UpdateFailed) as excinfo:
            await env.coordinator._async_update_data()

    assert "z-wave mesh unreachable" in str(excinfo.value)
    assert excinfo.value.__cause__ is boom


@pytest.mark.asyncio
async def test_read_failure_does_not_corrupt_lock_state(env: CoordEnv) -> None:
    """A failed cycle leaves the code definition and sync bookkeeping intact.

    A partially-applied cycle is how a code goes missing from the model while
    staying live on the deadbolt, so the failure path must be inert.
    """
    env.lock.set_code(1, "1111", "Alice")
    slot = env.lock.code_slots[1]
    slot.is_synced = True
    slot.sync_attempts = 2
    before = (slot.pin_code, slot.is_active, slot.is_synced, slot.sync_attempts)

    with patch(READ_PATH, side_effect=RuntimeError("read failed")):
        with pytest.raises(UpdateFailed):
            await env.coordinator._async_update_data()

    assert (slot.pin_code, slot.is_active, slot.is_synced, slot.sync_attempts) == before
    assert env.calls == []
    assert env.store.async_save.await_count == 0


# ---------------------------------------------------------------------------
# P0 invariant: desired presence = is_active AND pin_code AND is_valid_now()
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_desired_present_code_missing_from_lock_is_added(env: CoordEnv) -> None:
    """An active, in-window code absent from the deadbolt is pushed to it."""
    env.lock.set_code(1, "1111", "Alice")

    await env.cycle(zwave_codes={})

    assert env.actions() == [(1, "enable")]
    assert env.lock.code_slots[1].sync_attempts == 1


@pytest.mark.asyncio
async def test_matching_code_is_not_rewritten(env: CoordEnv) -> None:
    """A code already correct on the deadbolt is marked synced, not re-pushed."""
    env.lock.set_code(1, "1111", "Alice")

    await env.cycle(zwave_codes={1: {"code": "1111", "in_use": True}})

    assert env.calls == []
    assert env.lock.code_slots[1].is_synced is True
    assert env.lock.code_slots[1].sync_attempts == 0


@pytest.mark.asyncio
async def test_deactivated_code_is_removed_from_lock(env: CoordEnv) -> None:
    """Deactivating a slot pulls the PIN off the deadbolt but keeps the PIN."""
    env.lock.set_code(1, "1111", "Alice")
    env.lock.code_slots[1].is_active = False

    await env.cycle(zwave_codes={1: {"code": "1111", "in_use": True}})

    assert env.actions() == [(1, "disable")]
    # The definition is retained so it can be re-enabled without re-entry.
    assert env.lock.code_slots[1].pin_code == "1111"


@pytest.mark.asyncio
async def test_unmanaged_slot_is_left_alone(env: CoordEnv) -> None:
    """A code SLM never managed (no pin_code) is never touched on the lock."""
    await env.cycle(zwave_codes={4: {"code": "9999", "in_use": True}})

    assert env.calls == []


@pytest.mark.asyncio
async def test_closed_time_window_removes_code_and_reopening_readds_it(
    env: CoordEnv,
) -> None:
    """THE P0 INVARIANT, end to end, across a time-window boundary.

    A code whose window has not opened yet is desired-ABSENT: it must be removed
    from the hardware even though its definition is retained. When the window
    opens the same definition must be pushed back. Both halves are asserted from
    the dispatched Z-Wave actions, not from model flags.
    """
    start = datetime(2026, 8, 21, 12, 0, tzinfo=timezone.utc)

    with freeze_time("2026-08-21 10:00:00"):
        env.lock.set_code(1, "1111", "Alice", start_date=start)
        # Window is CLOSED (10:00 < 12:00) and the deadbolt holds the code.
        assert env.lock.code_slots[1].is_active is True
        assert env.lock.code_slots[1].is_valid_now() is False
        await env.cycle(zwave_codes={1: {"code": "1111", "in_use": True}})

    assert env.actions() == [(1, "disable")]
    assert env.lock.code_slots[1].pin_code == "1111", "definition must be retained"

    env.calls.clear()

    with freeze_time("2026-08-21 13:00:00"):
        # Window is now OPEN and the deadbolt is empty: re-add.
        assert env.lock.code_slots[1].is_valid_now() is True
        await env.cycle(zwave_codes={})

    assert env.actions() == [(1, "enable")]


@pytest.mark.asyncio
async def test_expired_code_is_disabled_and_removed(env: CoordEnv) -> None:
    """An end-dated code past its window is auto-disabled AND pulled off."""
    with freeze_time("2026-08-21 10:00:00"):
        env.lock.set_code(
            1,
            "1111",
            "Alice",
            end_date=datetime(2026, 8, 21, 9, 0, tzinfo=timezone.utc),
        )
        await env.cycle(zwave_codes={1: {"code": "1111", "in_use": True}})

    assert env.actions() == [(1, "disable")]
    assert env.lock.code_slots[1].is_active is False


@pytest.mark.asyncio
async def test_usage_exhausted_code_is_removed(env: CoordEnv) -> None:
    """A code that has burned its max_uses is desired-absent and removed."""
    env.lock.set_code(1, "1111", "Alice", max_uses=2)
    env.lock.code_slots[1].use_count = 2

    await env.cycle(zwave_codes={1: {"code": "1111", "in_use": True}})

    assert env.actions() == [(1, "disable")]


@pytest.mark.asyncio
async def test_wrong_code_on_lock_is_overwritten(env: CoordEnv) -> None:
    """A deadbolt holding a DIFFERENT PIN than SLM's is corrected."""
    env.lock.set_code(1, "1111", "Alice")

    await env.cycle(zwave_codes={1: {"code": "8888", "in_use": True}})

    assert env.actions() == [(1, "enable")]
    assert env.lock.code_slots[1].is_synced is False


@pytest.mark.asyncio
async def test_add_and_remove_dispatch_in_one_cycle(env: CoordEnv) -> None:
    """Independent slots are dispatched independently within a single cycle."""
    env.lock.set_code(1, "1111", "Alice")
    env.lock.set_code(2, "2222", "Bob")
    env.lock.code_slots[2].is_active = False

    await env.cycle(zwave_codes={2: {"code": "2222", "in_use": True}})

    assert sorted(env.actions()) == [(1, "enable"), (2, "disable")]


# ---------------------------------------------------------------------------
# Retry / backoff / MAX_SYNC_ATTEMPTS
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_backoff_suppresses_resync_inside_window(env: CoordEnv) -> None:
    """Within the exponential backoff window no write is attempted."""
    env.lock.set_code(1, "1111", "Alice")
    slot = env.lock.code_slots[1]
    slot.sync_attempts = 3  # backoff = min(60 * 2**3, 600) = 480s
    _age(slot, 120)  # past the 60s grace, inside the 480s backoff

    await env.cycle(zwave_codes={})

    assert env.calls == []
    assert slot.sync_attempts == 3, "a suppressed attempt must not be counted"


@pytest.mark.asyncio
async def test_backoff_expiry_allows_resync(env: CoordEnv) -> None:
    """Once the backoff window elapses the write is attempted and counted."""
    env.lock.set_code(1, "1111", "Alice")
    slot = env.lock.code_slots[1]
    slot.sync_attempts = 3
    _age(slot, 700)  # past the 480s backoff

    await env.cycle(zwave_codes={})

    assert env.actions() == [(1, "enable")]
    assert slot.sync_attempts == 4


@pytest.mark.asyncio
async def test_backoff_is_capped_at_ten_minutes(env: CoordEnv) -> None:
    """The backoff saturates at 600s rather than growing without bound."""
    env.lock.set_code(1, "1111", "Alice")
    slot = env.lock.code_slots[1]
    slot.sync_attempts = MAX_SYNC_ATTEMPTS - 1  # 2**9 * 60 would be 8+ hours
    _age(slot, 610)

    await env.cycle(zwave_codes={})

    assert env.actions() == [(1, "enable")]


@pytest.mark.asyncio
async def test_max_attempts_hands_slot_to_periodic_retry(env: CoordEnv) -> None:
    """At MAX_SYNC_ATTEMPTS the slot stops burning attempts and is retried.

    The periodic-retry path resets the slot for a fresh run, dispatches ONE
    write, records the retry in the tracker and announces it on the bus.
    """
    env.lock.set_code(1, "1111", "Alice")
    slot = env.lock.code_slots[1]
    slot.sync_attempts = MAX_SYNC_ATTEMPTS
    _age(slot, 700)

    await env.cycle(zwave_codes={})

    assert env.actions() == [(1, "enable")]
    assert slot.sync_attempts == 0, "periodic retry restarts the attempt budget"
    assert slot.sync_error is None
    assert "smart_lock_manager_sync_retry" in env.event_types()

    tracker = env.coordinator._periodic_retry_tracker["lock.test_lock_slot_1"]
    assert tracker["periodic_attempts"] == 1


@pytest.mark.asyncio
async def test_periodic_retry_honours_its_interval(env: CoordEnv) -> None:
    """A slot retried moments ago is not retried again on the next cycle."""
    env.lock.set_code(1, "1111", "Alice")
    slot = env.lock.code_slots[1]
    slot.sync_attempts = MAX_SYNC_ATTEMPTS
    _age(slot, 700)
    env.coordinator._periodic_retry_tracker["lock.test_lock_slot_1"] = {
        "last_retry": datetime.now(timezone.utc),
        "periodic_attempts": 1,
    }

    await env.cycle(zwave_codes={})

    assert env.calls == []
    assert "smart_lock_manager_sync_retry" not in env.event_types()
    assert slot.sync_attempts == MAX_SYNC_ATTEMPTS


@pytest.mark.asyncio
async def test_periodic_retry_is_one_slot_per_cycle(env: CoordEnv) -> None:
    """Two stuck slots produce exactly ONE retry, to spare the Z-Wave mesh."""
    for slot_number, pin in ((1, "1111"), (2, "2222")):
        env.lock.set_code(slot_number, pin, f"User {slot_number}")
        slot = env.lock.code_slots[slot_number]
        slot.sync_attempts = MAX_SYNC_ATTEMPTS
        _age(slot, 700)

    await env.cycle(zwave_codes={})

    assert len(env.calls) == 1
    assert env.event_types().count("smart_lock_manager_sync_retry") == 1


@pytest.mark.asyncio
async def test_stuck_slot_announces_its_error_on_the_bus(env: CoordEnv) -> None:
    """A stuck slot NOT chosen for retry still surfaces its sync error.

    Slot 1 is held back by its retry interval, so slot 2 takes the single retry
    of this cycle; slot 1 must not go quiet just because it lost the draw.
    """
    for slot_number, pin in ((1, "1111"), (2, "2222")):
        env.lock.set_code(slot_number, pin, f"User {slot_number}")
        slot = env.lock.code_slots[slot_number]
        _age(slot, 700)
    env.lock.code_slots[1].sync_attempts = 120  # a multiple of the 120-cycle throttle
    env.lock.code_slots[2].sync_attempts = MAX_SYNC_ATTEMPTS
    env.coordinator._periodic_retry_tracker["lock.test_lock_slot_1"] = {
        "last_retry": datetime.now(timezone.utc),
        "periodic_attempts": 1,
    }

    await env.cycle(zwave_codes={})

    errors = [e for e in env.events if e.event_type == "smart_lock_manager_sync_error"]
    assert [e.data["slot_number"] for e in errors] == [1]
    assert errors[0].data["attempts"] == 120
    assert "Failed to sync" in errors[0].data["error"]


@pytest.mark.asyncio
async def test_sync_success_clears_the_retry_tracker(env: CoordEnv) -> None:
    """Once a slot reads back correct, its periodic-retry state is discarded."""
    env.lock.set_code(1, "1111", "Alice")
    env.coordinator._periodic_retry_tracker["lock.test_lock_slot_1"] = {
        "last_retry": datetime.now(timezone.utc),
        "periodic_attempts": 4,
    }

    await env.cycle(zwave_codes={1: {"code": "1111", "in_use": True}})

    assert env.coordinator._periodic_retry_tracker == {}


# ---------------------------------------------------------------------------
# Dispatch failure isolation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_add_dispatch_failure_does_not_fail_the_cycle(env: CoordEnv) -> None:
    """A raising write is contained: the cycle completes and backoff advances.

    The failure injected is a REAL one: the sync service is unregistered, which
    is what a call landing in a startup/teardown window hits, and which
    ``async_call`` raises synchronously as ``ServiceNotFound``. (Registering a
    raising handler would not work -- the default non-blocking call schedules
    the handler, so its exception never reaches the coordinator.)
    """
    env.lock.set_code(1, "1111", "Alice")
    env.hass.services.async_remove(DOMAIN, "sync_slot_to_zwave")

    data = await env.cycle(zwave_codes={})

    assert data["connection_status"] is True
    # The attempt was still counted, so backoff advances instead of hot-looping.
    assert env.lock.code_slots[1].sync_attempts == 1
    assert env.lock.code_slots[1].is_synced is False


@pytest.mark.asyncio
async def test_remove_dispatch_failure_does_not_fail_the_cycle(env: CoordEnv) -> None:
    """A raising removal is isolated: the cycle completes, the PIN is retained.

    Retention matters: nulling the definition on a FAILED physical clear is the
    false-green that leaves a live code on the deadbolt with no record of it.
    """
    env.lock.set_code(1, "1111", "Alice")
    env.lock.code_slots[1].is_active = False
    env.hass.services.async_remove(DOMAIN, "sync_slot_to_zwave")

    data = await env.cycle(zwave_codes={1: {"code": "1111", "in_use": True}})

    assert data["connection_status"] is True
    assert env.lock.code_slots[1].pin_code == "1111"


@pytest.mark.asyncio
async def test_periodic_retry_failure_still_records_the_attempt(
    env: CoordEnv,
) -> None:
    """A failed periodic retry advances the tracker so it backs off, not loops."""
    env.lock.set_code(1, "1111", "Alice")
    slot = env.lock.code_slots[1]
    slot.sync_attempts = MAX_SYNC_ATTEMPTS
    _age(slot, 700)
    env.hass.services.async_remove(DOMAIN, "sync_slot_to_zwave")

    data = await env.cycle(zwave_codes={})

    assert data["connection_status"] is True
    tracker = env.coordinator._periodic_retry_tracker["lock.test_lock_slot_1"]
    assert tracker["periodic_attempts"] == 1
    assert "smart_lock_manager_sync_retry" in env.event_types()


@pytest.mark.asyncio
async def test_zone_owned_lock_mirrors_zone_codes_before_syncing(
    env: CoordEnv,
) -> None:
    """A zone member syncs the ZONE's codes, mirrored in before the decision.

    The mirror runs at step 0, so a code that exists only on the owning zone is
    still pushed to this member's deadbolt in the same cycle.
    """
    zone = Mock()
    zone.name = "Front Doors"
    zone.get_configured_codes_count = Mock(return_value=1)

    def _mirror(hass: HomeAssistant, lock: SmartLockManagerLock):
        lock.set_code(1, "1111", "Alice")
        return zone

    with patch(
        "custom_components.smart_lock_manager.coordinator."
        "mirror_owning_zone_to_member",
        side_effect=_mirror,
    ):
        await env.cycle(zwave_codes={})

    assert env.actions() == [(1, "enable")]


# ---------------------------------------------------------------------------
# Storage dirty-check (2026.8.2)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_first_cycle_persists_the_lock(env: CoordEnv) -> None:
    """The first cycle always writes: there is no prior snapshot to match."""
    await env.cycle()

    assert env.store.async_save.await_count == 1


@pytest.mark.asyncio
async def test_unchanged_lock_is_not_rewritten(env: CoordEnv) -> None:
    """An idle lock is not rewritten to flash every 30 seconds."""
    env.lock.set_code(1, "1111", "Alice")
    codes = {1: {"code": "1111", "in_use": True}}

    await env.cycle(zwave_codes=codes)
    first = env.store.async_save.await_count
    await env.cycle(zwave_codes=codes)
    await env.cycle(zwave_codes=codes)

    assert first == 1
    assert env.store.async_save.await_count == 1


@pytest.mark.asyncio
async def test_changed_lock_is_rewritten_on_the_same_cycle(env: CoordEnv) -> None:
    """A real change persists immediately rather than waiting for a later cycle."""
    env.lock.set_code(1, "1111", "Alice")
    codes = {1: {"code": "1111", "in_use": True}}
    await env.cycle(zwave_codes=codes)
    assert env.store.async_save.await_count == 1

    env.lock.set_code(2, "2222", "Bob")

    await env.cycle(zwave_codes=codes)

    assert env.store.async_save.await_count == 2
