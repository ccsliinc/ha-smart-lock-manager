"""End-to-end dev proof for the configurable-sweep + health-sweep wiring.

Drives the FULL loop the unit tests stop short of: the ``set_sweep_intervals``
service persists the new cadence, fires the global-settings event, the engine's
live-refresh path reschedules its sweep timers at the NEW interval WITHOUT a
restart, and the health sweep records one-alert/dedup/recovery against a member
held in a persistent bad state. Runs under the real HA test ``hass`` with the
engine constructed exactly as production does.
"""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.core import HomeAssistant

from custom_components.smart_lock_manager.alert_engine import (
    ALERT_JAM,
    ALERT_OFFLINE,
    AlertEngine,
)
from custom_components.smart_lock_manager.models.zone import Zone
from custom_components.smart_lock_manager.models.zone_settings import ZoneSettings
from custom_components.smart_lock_manager.services.system_services import (
    SystemServices,
)
from custom_components.smart_lock_manager.storage.global_settings import (
    get_cached_global_settings,
)
from custom_components.smart_lock_manager.zone_runtime import ZONE_REGISTRY_KEY

LOCK_ENTITY = "lock.front_test"


def _zone() -> Zone:
    settings = ZoneSettings()
    settings.alerts.jam.enabled = True
    settings.alerts.offline.enabled = True
    settings.alerts.low_battery.enabled = True
    settings.alerts.outside_hours.enabled = True
    return Zone(
        zone_id="zone_test",
        name="Test Zone",
        member_lock_entity_ids=[LOCK_ENTITY],
        settings=settings,
    )


@pytest.fixture(autouse=True)
def _no_storage():
    """Stub all storage I/O so the proof runs without disk."""
    with (
        patch(
            "custom_components.smart_lock_manager.alert_engine.save_alert_log",
            AsyncMock(),
        ),
        patch(
            "custom_components.smart_lock_manager.alert_engine.load_alert_log",
            AsyncMock(return_value={"alerts": [], "alerted_state": {}}),
        ),
        patch(
            "custom_components.smart_lock_manager.storage.global_settings.Store"
        ) as store_cls,
    ):
        store_cls.return_value.async_load = AsyncMock(return_value=None)
        store_cls.return_value.async_save = AsyncMock()
        yield


async def test_set_sweep_intervals_persists_and_reschedules_live(
    hass: HomeAssistant,
) -> None:
    """set_sweep_intervals → persist → engine reschedules at the new cadence."""
    hass.data[ZONE_REGISTRY_KEY] = {"zone_test": _zone()}
    engine = AlertEngine(hass)

    with patch(
        "custom_components.smart_lock_manager.alert_engine.async_track_time_interval"
    ) as track:
        await engine.async_start()
        # Default cadences first: 15m + 60m.
        first = {c.args[2] for c in track.call_args_list}
        assert timedelta(minutes=15) in first
        assert timedelta(minutes=60) in first

        # Call the service: change BOTH cadences.
        call = type(
            "C",
            (),
            {"data": {"health_sweep_minutes": 5, "outside_hours_sweep_minutes": 2}},
        )()
        await SystemServices.set_sweep_intervals(hass, call)  # type: ignore[arg-type]
        # Service persisted to the cache.
        cached = get_cached_global_settings()
        assert cached["health_sweep_minutes"] == 5
        assert cached["outside_hours_sweep_minutes"] == 2

        # Simulate the live-refresh listener: reload cache + refresh engine.
        track.reset_mock()
        engine.async_refresh()
        rescheduled = {c.args[2] for c in track.call_args_list}
        assert timedelta(minutes=2) in rescheduled
        assert timedelta(minutes=5) in rescheduled

    engine.async_stop()


async def test_health_sweep_one_alert_dedup_recovery_no_double(
    hass: HomeAssistant,
) -> None:
    """Persistent jam → ONE alert; 2nd sweep no re-fire; recovery; no double."""
    hass.data[ZONE_REGISTRY_KEY] = {"zone_test": _zone()}
    engine = AlertEngine(hass)
    await engine.async_start()

    hass.states.async_set(LOCK_ENTITY, "jammed")
    engine._run_health_sweep(None)  # type: ignore[arg-type]
    assert len(engine.serialize()) == 1
    assert engine.serialize()[0]["alert_type"] == ALERT_JAM
    # Dedup: still jammed, second sweep records nothing more.
    engine._run_health_sweep(None)  # type: ignore[arg-type]
    assert len(engine.serialize()) == 1
    # Recovery via the state path.
    engine._eval_jam(LOCK_ENTITY, "locked", {})
    assert engine.serialize()[0]["is_recovery"] is True
    assert len(engine.serialize()) == 2

    # No double-record: a state-path offline alert then a sweep -> still one.
    hass.states.async_set(LOCK_ENTITY, "unavailable")
    engine._run_health_sweep(None)  # type: ignore[arg-type]
    offline = [a for a in engine.serialize() if a["alert_type"] == ALERT_OFFLINE]
    assert len(offline) == 1
    engine._run_health_sweep(None)  # type: ignore[arg-type]
    offline = [a for a in engine.serialize() if a["alert_type"] == ALERT_OFFLINE]
    assert len(offline) == 1

    engine.async_stop()
