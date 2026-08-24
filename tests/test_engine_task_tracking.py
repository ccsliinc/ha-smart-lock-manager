"""Tests for engine background-task retention (``engine_tasks.py``).

Both dev-gated engines launch async work from synchronous ``@callback``
contexts. Previously the task handles were discarded, so a task could be
garbage-collected mid-flight and an unload had nothing to cancel. These tests
assert the retention contract directly: a running task is HELD, a finished task
is RELEASED (so the set cannot grow without bound), and ``async_stop`` cancels
whatever is still in flight.
"""

from __future__ import annotations

import asyncio
from typing import Any, Set
from unittest.mock import patch

import pytest
from homeassistant.core import HomeAssistant

from custom_components.smart_lock_manager.alert_engine import AlertEngine
from custom_components.smart_lock_manager.auto_lock import AutoLockEngine
from custom_components.smart_lock_manager.engine_tasks import (
    cancel_tracked_tasks,
    track_task,
)

# ---------------------------------------------------------------------------
# The primitives
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_running_task_is_retained_and_released(hass: HomeAssistant) -> None:
    """A task is held for exactly as long as it runs, then discarded."""
    tasks: Set["asyncio.Task[Any]"] = set()
    gate = asyncio.Event()

    async def _work() -> None:
        await gate.wait()

    task = track_task(hass, tasks, _work(), "probe")
    await asyncio.sleep(0)

    assert tasks == {task}, "a running task must be strongly referenced"

    gate.set()
    await task
    await asyncio.sleep(0)

    assert tasks == set(), "a finished task must not be retained forever"


@pytest.mark.asyncio
async def test_failed_task_is_also_released(hass: HomeAssistant) -> None:
    """A task that raises still leaves the retention set clean."""
    tasks: Set["asyncio.Task[Any]"] = set()

    async def _boom() -> None:
        raise RuntimeError("dispatch failed")

    task = track_task(hass, tasks, _boom(), "probe")
    with pytest.raises(RuntimeError):
        await task
    await asyncio.sleep(0)

    assert tasks == set()


@pytest.mark.asyncio
async def test_cancel_tracked_tasks_cancels_only_pending(
    hass: HomeAssistant,
) -> None:
    """Cancellation reports how much was in flight and empties the set."""
    tasks: Set["asyncio.Task[Any]"] = set()
    gate = asyncio.Event()

    async def _work() -> None:
        await gate.wait()

    pending = track_task(hass, tasks, _work(), "pending")
    await asyncio.sleep(0)

    cancelled = cancel_tracked_tasks(tasks, "TestEngine")
    await asyncio.sleep(0)

    assert cancelled == 1
    assert pending.cancelled()
    assert tasks == set()


@pytest.mark.asyncio
async def test_cancel_with_nothing_in_flight_is_a_no_op(
    hass: HomeAssistant,
) -> None:
    """Stopping an idle engine cancels nothing and does not raise."""
    assert cancel_tracked_tasks(set(), "TestEngine") == 0


# ---------------------------------------------------------------------------
# AlertEngine: the notification dispatch task
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_alert_dispatch_task_is_retained_while_running(
    hass: HomeAssistant,
) -> None:
    """Recording an alert holds its dispatch task until the dispatch finishes."""
    engine = AlertEngine(hass)
    gate = asyncio.Event()

    async def _slow_notify(self, entity_id: str, record: dict) -> None:
        await gate.wait()

    with patch.object(AlertEngine, "_notify", _slow_notify):
        engine.record_external("lock.front", "auto_lock_failed", "CRIT", "stuck")
        await asyncio.sleep(0)

        assert len(engine._tasks) == 1

        gate.set()
        await hass.async_block_till_done()

    assert engine._tasks == set()


@pytest.mark.asyncio
async def test_alert_engine_stop_cancels_in_flight_dispatch(
    hass: HomeAssistant,
) -> None:
    """An unload cancels a dispatch mid-flight instead of orphaning it."""
    engine = AlertEngine(hass)

    async def _never_finishes(self, entity_id: str, record: dict) -> None:
        await asyncio.Event().wait()

    with patch.object(AlertEngine, "_notify", _never_finishes):
        engine.record_external("lock.front", "auto_lock_failed", "CRIT", "stuck")
        await asyncio.sleep(0)
        (task,) = tuple(engine._tasks)

        engine.async_stop()
        await asyncio.sleep(0)

    assert task.cancelled()
    assert engine._tasks == set()
    assert engine._started is False


# ---------------------------------------------------------------------------
# AutoLockEngine: the COB / idle-expiry tasks
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_auto_lock_engine_stop_cancels_in_flight_work(
    hass: HomeAssistant,
) -> None:
    """A pending COB / idle-expiry run is cancelled when the engine stops."""
    engine = AutoLockEngine(hass)
    gate = asyncio.Event()

    async def _work() -> None:
        await gate.wait()

    task = track_task(hass, engine._tasks, _work(), "cob")
    await asyncio.sleep(0)
    assert engine._tasks == {task}

    engine.async_stop()
    await asyncio.sleep(0)

    assert task.cancelled()
    assert engine._tasks == set()


@pytest.mark.asyncio
async def test_stop_is_idempotent_for_both_engines(hass: HomeAssistant) -> None:
    """Stopping twice is safe: no residue, no exception."""
    for engine in (AlertEngine(hass), AutoLockEngine(hass)):
        engine.async_stop()
        engine.async_stop()
        assert engine._tasks == set()
