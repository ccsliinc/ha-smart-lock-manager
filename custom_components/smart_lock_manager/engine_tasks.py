"""Background-task retention for the dev-gated engines.

Both engines (:mod:`.alert_engine` and :mod:`.auto_lock`) have to launch async
work from synchronous ``@callback`` contexts -- a state-change handler, a
``async_track_time_change`` trigger, an ``async_call_later`` expiry. The obvious
spelling is ``hass.async_create_task(coro)`` with the handle discarded, which
has two defects:

* Python only holds a WEAK reference to a running task from the event loop, so
  a task nobody retains can be garbage-collected mid-flight and simply vanish,
  and
* on unload there is nothing to cancel, so an in-flight task can keep running
  against an engine (and a ``hass``) that is being torn down underneath it.

These two helpers give a caller a strong reference for the task's lifetime and
a way to release everything still pending at stop. They are deliberately module
functions rather than a mixin so neither engine's class hierarchy changes.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Coroutine, Set, cast

from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)


def track_task(
    hass: HomeAssistant,
    tasks: Set["asyncio.Task[Any]"],
    coro: Coroutine[Any, Any, Any],
    name: str,
) -> "asyncio.Task[Any]":
    """Launch ``coro`` on the HA loop and retain a strong reference to it.

    - Description: Creates the task through ``hass.async_create_task`` (so HA
      still tracks it for shutdown), adds it to ``tasks``, and registers a
      done-callback that discards it again. Net effect: ``tasks`` holds exactly
      the tasks currently in flight, and a task that completes normally cleans
      up after itself with no bookkeeping at the call site.
    - Inputs: hass (HomeAssistant), tasks (the engine's retention set), coro
      (the coroutine to run), name (str, for the task name / logs).
    - Outputs: the created ``asyncio.Task``.
    """
    task = cast("asyncio.Task[Any]", hass.async_create_task(coro, name))
    tasks.add(task)
    task.add_done_callback(tasks.discard)
    return task


def cancel_tracked_tasks(tasks: Set["asyncio.Task[Any]"], owner: str) -> int:
    """Cancel every still-pending tracked task and clear the set.

    - Description: Called from an engine's synchronous ``async_stop``. Requests
      cancellation of anything still in flight so a teardown cannot race an
      in-progress dispatch or auto-lock, then empties the set. Cancellation is
      requested, not awaited: ``async_stop`` is a ``@callback`` and cannot
      await, and HA's own loop shutdown collects the cancelled tasks.
    - Inputs: tasks (the engine's retention set), owner (str, for the log line).
    - Outputs: int -- how many tasks were still pending and got cancelled.
    """
    pending = [task for task in tasks if not task.done()]
    for task in pending:
        task.cancel()
    tasks.clear()
    if pending:
        _LOGGER.debug("%s: cancelled %d in-flight task(s) on stop", owner, len(pending))
    return len(pending)
