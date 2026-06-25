"""Tests for the zone-model AlertEngine + AutoLockEngine (Phase 4 engines).

Covers the OBSERVE-ONLY alert detectors, the dev-simulation entrypoint, the
auto-lock verify/retry path, the OBSERVE "would-lock" recording, and the live
re-subscribe (``async_refresh``) wired to zone-settings changes. These engines
run only under ``SLM_DEV_MOCK`` / ``SLM_ENABLE_ENGINES`` and never notify; the
tests assert on the RECORDED alert/outcome streams, never on real sends.

The engines live across the split modules (alert_engine / alert_detectors /
alert_dev and auto_lock / auto_lock_verify); importing the engine classes and
driving them end-to-end here proves the mixin composition is intact.
"""

from __future__ import annotations

import os
import time
from datetime import datetime
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.core import HomeAssistant

from custom_components.smart_lock_manager.alert_engine import (
    ALERT_JAM,
    ALERT_LOW_BATTERY,
    ALERT_OFFLINE,
    ALERT_OUTSIDE_HOURS,
    ALERT_SUSTAINED,
    AlertEngine,
)
from custom_components.smart_lock_manager.auto_lock import (
    MODE_SCHEDULED,
    AutoLockEngine,
    real_autolock_enabled,
)
from custom_components.smart_lock_manager.auto_lock_verify import _dig_bolt_status
from custom_components.smart_lock_manager.models.zone import Zone
from custom_components.smart_lock_manager.models.zone_settings import ZoneSettings
from custom_components.smart_lock_manager.notifications import (
    build_alert_body,
    build_alert_subject,
)
from custom_components.smart_lock_manager.storage import (
    clear_global_snooze,
    clear_zone_snooze,
    global_snooze_active,
    set_global_snooze,
    set_zone_snooze,
    snooze_active,
    zone_snooze_active,
)
from custom_components.smart_lock_manager.zone_runtime import ZONE_REGISTRY_KEY

LOCK_ENTITY = "lock.front_test"
SECOND_LOCK_ENTITY = "lock.back_test"
BATTERY_ENTITY = "sensor.front_test_battery"


def _build_zone(**enable: bool) -> Zone:
    """Build a one-member zone with all alert detectors enabled by default."""
    settings = ZoneSettings()
    settings.alerts.outside_hours.enabled = enable.get("outside_hours", True)
    settings.alerts.sustained_unlock.enabled = enable.get("sustained_unlock", True)
    settings.alerts.jam.enabled = enable.get("jam", True)
    settings.alerts.low_battery.enabled = enable.get("low_battery", True)
    settings.alerts.offline.enabled = enable.get("offline", True)
    settings.scheduled_auto_lock.enabled = enable.get("scheduled", False)
    settings.idle_auto_lock.enabled = enable.get("idle", False)
    return Zone(
        zone_id="zone_test",
        name="Test Zone",
        member_lock_entity_ids=[LOCK_ENTITY],
        settings=settings,
    )


def _register_zone(hass: HomeAssistant, zone: Zone) -> None:
    hass.data[ZONE_REGISTRY_KEY] = {zone.zone_id: zone}


@pytest.fixture
def alert_engine(hass: HomeAssistant) -> AlertEngine:
    """Build an AlertEngine over one fully-enabled zone (storage stubbed)."""
    _register_zone(hass, _build_zone())
    return AlertEngine(hass)


class TestAlertEngineLifecycle:
    """Start / stop / refresh lifecycle + topology."""

    async def test_start_subscribes_and_serializes(
        self, hass: HomeAssistant, alert_engine: AlertEngine
    ) -> None:
        """Start subscribes locks + batteries; second start is a no-op."""
        with patch(
            "custom_components.smart_lock_manager.alert_engine.load_alert_log",
            AsyncMock(return_value={"alerts": [], "alerted_state": {}}),
        ):
            await alert_engine.async_start()
        assert alert_engine._started is True
        # Lock + companion battery entity are both monitored.
        monitored = alert_engine._monitored_entities()
        assert LOCK_ENTITY in monitored
        assert BATTERY_ENTITY in monitored
        assert alert_engine.serialize() == []
        # Second start is a no-op. Three handles: state listener + the two
        # periodic sweep interval triggers (outside-hours + health).
        await alert_engine.async_start()
        assert len(alert_engine._unsubs) == 3
        alert_engine.async_stop()
        assert alert_engine._started is False
        assert alert_engine._unsubs == []

    async def test_refresh_is_idempotent_no_duplicate_listeners(
        self, hass: HomeAssistant, alert_engine: AlertEngine
    ) -> None:
        """Repeated refresh leaves exactly one state listener + one sweep."""
        with patch(
            "custom_components.smart_lock_manager.alert_engine.load_alert_log",
            AsyncMock(return_value={"alerts": [], "alerted_state": {}}),
        ):
            await alert_engine.async_start()
        for _ in range(3):
            alert_engine.async_refresh()
        # Exactly three handles (state listener + the two sweep triggers)
        # regardless of how many refreshes ran — old handles are torn down
        # first each time.
        assert len(alert_engine._unsubs) == 3

    def test_refresh_before_start_is_noop(self, alert_engine: AlertEngine) -> None:
        """Refresh before start does nothing."""
        alert_engine.async_refresh()
        assert alert_engine._unsubs == []


class TestAlertDetectors:
    """Per-member detector logic (records, no notify)."""

    @pytest.fixture(autouse=True)
    def _no_persist(self) -> None:
        """Stub alert-log persistence so detectors don't hit storage."""
        # _record schedules async_create_task(self._notify); the real hass
        # fixture runs it, but _notify only persists/dispatches — stub storage.
        with patch(
            "custom_components.smart_lock_manager.alert_engine.save_alert_log",
            AsyncMock(),
        ):
            yield

    def test_low_battery_alert_and_recovery(self, alert_engine: AlertEngine) -> None:
        """Below threshold -> one WARN; recovery only above hysteresis."""
        # Below the 20% default threshold -> WARN recorded once.
        alert_engine._eval_low_battery(LOCK_ENTITY, "10")
        alert_engine._eval_low_battery(LOCK_ENTITY, "10")  # no duplicate
        alerts = alert_engine.serialize()
        assert len(alerts) == 1
        assert alerts[0]["alert_type"] == ALERT_LOW_BATTERY
        assert alerts[0]["severity"] == "WARN"
        # Recovery only above threshold + hysteresis (>=25).
        alert_engine._eval_low_battery(LOCK_ENTITY, "30")
        recovered = alert_engine.serialize()
        assert recovered[0]["is_recovery"] is True

    def test_low_battery_ignores_nonnumeric(self, alert_engine: AlertEngine) -> None:
        """A non-numeric battery reading records nothing."""
        alert_engine._eval_low_battery(LOCK_ENTITY, "unknown")
        assert alert_engine.serialize() == []

    def test_jam_alert_and_recovery(
        self, hass: HomeAssistant, alert_engine: AlertEngine
    ) -> None:
        """A jammed state records CRIT; a re-lock records recovery."""
        alert_engine._eval_jam(LOCK_ENTITY, "jammed", {})
        alerts = alert_engine.serialize()
        assert alerts[0]["alert_type"] == ALERT_JAM
        assert alerts[0]["severity"] == "CRIT"
        # Re-lock clears the jam episode.
        alert_engine._eval_jam(LOCK_ENTITY, "locked", {})
        assert alert_engine.serialize()[0]["is_recovery"] is True

    def test_outside_hours_unlock_records(self, alert_engine: AlertEngine) -> None:
        """An unlock outside business hours records an alert."""
        # Force "outside business hours" so the unlock alerts.
        with patch.object(AlertEngine, "_in_business_hours", return_value=False):
            alert_engine._eval_outside_hours(LOCK_ENTITY, "unlocked")
        alerts = alert_engine.serialize()
        assert alerts[0]["alert_type"] == ALERT_OUTSIDE_HOURS

    def test_outside_hours_suppressed_during_business_hours(
        self, alert_engine: AlertEngine
    ) -> None:
        """An unlock during business hours records nothing."""
        with patch.object(AlertEngine, "_in_business_hours", return_value=True):
            alert_engine._eval_outside_hours(LOCK_ENTITY, "unlocked")
        assert alert_engine.serialize() == []

    def test_dev_simulate_each_type_records(self, alert_engine: AlertEngine) -> None:
        """dev_simulate records one alert for each of the five types."""
        for alert_type in (
            ALERT_OUTSIDE_HOURS,
            ALERT_SUSTAINED,
            ALERT_JAM,
            ALERT_LOW_BATTERY,
            ALERT_OFFLINE,
        ):
            alert_engine.dev_simulate(alert_type, LOCK_ENTITY)
        types = {a["alert_type"] for a in alert_engine.serialize()}
        assert types == {
            ALERT_OUTSIDE_HOURS,
            ALERT_SUSTAINED,
            ALERT_JAM,
            ALERT_LOW_BATTERY,
            ALERT_OFFLINE,
        }

    def test_dev_simulate_unknown_type_is_noop(self, alert_engine: AlertEngine) -> None:
        """dev_simulate with an unknown type records nothing."""
        alert_engine.dev_simulate("bogus", LOCK_ENTITY)
        assert alert_engine.serialize() == []

    def test_record_external_routes_through_record(
        self, alert_engine: AlertEngine
    ) -> None:
        """record_external lands as a normal recorded alert with zone info."""
        alert_engine.record_external(LOCK_ENTITY, "auto_lock_failed", "CRIT", "boom")
        alerts = alert_engine.serialize()
        assert alerts[0]["alert_type"] == "auto_lock_failed"
        assert alerts[0]["zone_name"] == "Test Zone"


class TestOutsideHoursSweep:
    """Periodic boundary sweep for the still-unlocked-outside-hours gap."""

    @pytest.fixture(autouse=True)
    def _no_persist(self) -> None:
        """Stub alert-log persistence so the sweep doesn't hit storage."""
        with patch(
            "custom_components.smart_lock_manager.alert_engine.save_alert_log",
            AsyncMock(),
        ):
            yield

    def test_boundary_unlock_caught_by_sweep_once(
        self, hass: HomeAssistant, alert_engine: AlertEngine
    ) -> None:
        """A door unlocked-during-hours, still unlocked off-hours -> ONE alert.

        Mirrors the production gap: the state path did NOT alert (it unlocked
        in-hours), so only the boundary sweep can catch it. A second sweep does
        NOT re-fire (per-episode alerted-flag dedup).
        """
        hass.states.async_set(LOCK_ENTITY, "unlocked")
        with patch.object(AlertEngine, "_in_business_hours", return_value=False):
            alert_engine._run_outside_hours_sweep(datetime.now())
            first = alert_engine.serialize()
            assert len(first) == 1
            assert first[0]["alert_type"] == ALERT_OUTSIDE_HOURS
            assert first[0]["is_recovery"] is False
            # Second sweep is idempotent — no re-fire.
            alert_engine._run_outside_hours_sweep(datetime.now())
            assert len(alert_engine.serialize()) == 1

    def test_sweep_no_alert_when_locked_at_boundary(
        self, hass: HomeAssistant, alert_engine: AlertEngine
    ) -> None:
        """A member LOCKED at the boundary records nothing on the sweep."""
        hass.states.async_set(LOCK_ENTITY, "locked")
        with patch.object(AlertEngine, "_in_business_hours", return_value=False):
            alert_engine._run_outside_hours_sweep(datetime.now())
        assert alert_engine.serialize() == []

    def test_sweep_no_alert_during_business_hours(
        self, hass: HomeAssistant, alert_engine: AlertEngine
    ) -> None:
        """Unlocked but still inside business hours -> sweep records nothing."""
        hass.states.async_set(LOCK_ENTITY, "unlocked")
        with patch.object(AlertEngine, "_in_business_hours", return_value=True):
            alert_engine._run_outside_hours_sweep(datetime.now())
        assert alert_engine.serialize() == []

    def test_state_path_then_sweep_no_double_record(
        self, hass: HomeAssistant, alert_engine: AlertEngine
    ) -> None:
        """Fresh off-hours state alert + later sweep -> no double-record.

        The state path records one alert; the sweep then sees the SAME
        already-alerted episode and records nothing further.
        """
        hass.states.async_set(LOCK_ENTITY, "unlocked")
        with patch.object(AlertEngine, "_in_business_hours", return_value=False):
            alert_engine._eval_outside_hours(LOCK_ENTITY, "unlocked")
            assert len(alert_engine.serialize()) == 1
            alert_engine._run_outside_hours_sweep(datetime.now())
            assert len(alert_engine.serialize()) == 1

    def test_sweep_alert_then_relock_recovers(
        self, hass: HomeAssistant, alert_engine: AlertEngine
    ) -> None:
        """After the sweep alerts, a re-lock via the state path recovers once."""
        hass.states.async_set(LOCK_ENTITY, "unlocked")
        with patch.object(AlertEngine, "_in_business_hours", return_value=False):
            alert_engine._run_outside_hours_sweep(datetime.now())
            assert len(alert_engine.serialize()) == 1
            # Re-lock — recovery fires (any time of day) and clears the episode.
            alert_engine._eval_outside_hours(LOCK_ENTITY, "locked")
        recovered = alert_engine.serialize()
        assert len(recovered) == 2
        assert recovered[0]["is_recovery"] is True

    def test_sweep_skipped_when_detector_disabled_in_production(
        self, hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """With dev_mock OFF and outside_hours disabled, the sweep is a no-op."""
        _register_zone(hass, _build_zone(outside_hours=False))
        engine = AlertEngine(hass)
        monkeypatch.setattr(
            "custom_components.smart_lock_manager.alert_detectors.is_dev_mock",
            lambda: False,
        )
        hass.states.async_set(LOCK_ENTITY, "unlocked")
        with patch.object(AlertEngine, "_in_business_hours", return_value=False):
            engine._run_outside_hours_sweep(datetime.now())
        assert engine.serialize() == []

    async def test_start_registers_both_interval_sweeps(
        self, hass: HomeAssistant, alert_engine: AlertEngine
    ) -> None:
        """Starting the engine registers BOTH configurable interval sweeps.

        The outside-hours sweep runs at the default 15-minute cadence and the
        health sweep at the default 60-minute cadence, both via
        ``async_track_time_interval`` (arbitrary N works), reading the
        globally-configured intervals from the primed cache.
        """
        from datetime import timedelta

        with (
            patch(
                "custom_components.smart_lock_manager.alert_engine.load_alert_log",
                AsyncMock(return_value={"alerts": [], "alerted_state": {}}),
            ),
            patch(
                "custom_components.smart_lock_manager.storage." "load_global_settings",
                AsyncMock(return_value=None),
            ),
            patch(
                "custom_components.smart_lock_manager.alert_engine."
                "get_cached_global_settings",
                return_value={
                    "outside_hours_sweep_minutes": 15,
                    "health_sweep_minutes": 60,
                },
            ),
            patch(
                "custom_components.smart_lock_manager.alert_engine."
                "async_track_time_interval"
            ) as track,
        ):
            await alert_engine.async_start()
        # Two interval triggers: outside-hours (15m) + health (60m).
        assert track.call_count == 2
        intervals = {call.args[2] for call in track.call_args_list}
        assert timedelta(minutes=15) in intervals
        assert timedelta(minutes=60) in intervals


class TestHealthSweep:
    """Periodic health sweep for persistent jam / low_battery / offline."""

    @pytest.fixture(autouse=True)
    def _no_persist(self) -> None:
        """Stub alert-log persistence so the sweep doesn't hit storage."""
        with patch(
            "custom_components.smart_lock_manager.alert_engine.save_alert_log",
            AsyncMock(),
        ):
            yield

    def test_jam_caught_by_sweep_once_then_recovers(
        self, hass: HomeAssistant, alert_engine: AlertEngine
    ) -> None:
        """A persistently-jammed member -> ONE alert; 2nd sweep no re-fire."""
        hass.states.async_set(LOCK_ENTITY, "jammed")
        alert_engine._run_health_sweep(datetime.now())
        first = alert_engine.serialize()
        assert len(first) == 1
        assert first[0]["alert_type"] == ALERT_JAM
        assert first[0]["is_recovery"] is False
        # Still jammed on the next sweep -> idempotent, no re-fire.
        alert_engine._run_health_sweep(datetime.now())
        assert len(alert_engine.serialize()) == 1
        # Clearing the state via the state path records exactly one recovery.
        alert_engine._eval_jam(LOCK_ENTITY, "locked", {})
        recovered = alert_engine.serialize()
        assert len(recovered) == 2
        assert recovered[0]["is_recovery"] is True

    def test_jam_sensor_on_caught_by_sweep(
        self, hass: HomeAssistant, alert_engine: AlertEngine
    ) -> None:
        """A companion jam binary_sensor 'on' (lock not jammed) still alerts."""
        hass.states.async_set(LOCK_ENTITY, "locked")
        hass.states.async_set("binary_sensor.front_test_jammed", "on")
        alert_engine._run_health_sweep(datetime.now())
        alerts = alert_engine.serialize()
        assert len(alerts) == 1
        assert alerts[0]["alert_type"] == ALERT_JAM

    def test_offline_caught_by_sweep_once_then_recovers(
        self, hass: HomeAssistant, alert_engine: AlertEngine
    ) -> None:
        """A persistently-offline member -> ONE alert; 2nd sweep no re-fire."""
        hass.states.async_set(LOCK_ENTITY, "unavailable")
        alert_engine._run_health_sweep(datetime.now())
        first = alert_engine.serialize()
        assert len(first) == 1
        assert first[0]["alert_type"] == ALERT_OFFLINE
        # Second sweep idempotent.
        alert_engine._run_health_sweep(datetime.now())
        assert len(alert_engine.serialize()) == 1
        # Back online via the state path -> one recovery.
        hass.states.async_set(LOCK_ENTITY, "locked")
        alert_engine._eval_offline(LOCK_ENTITY, "locked")
        recovered = alert_engine.serialize()
        assert len(recovered) == 2
        assert recovered[0]["is_recovery"] is True

    def test_low_battery_caught_by_sweep_once(
        self, hass: HomeAssistant, alert_engine: AlertEngine
    ) -> None:
        """A battery stuck below threshold -> ONE alert; 2nd sweep no re-fire."""
        hass.states.async_set(LOCK_ENTITY, "locked")
        hass.states.async_set(BATTERY_ENTITY, "5")
        alert_engine._run_health_sweep(datetime.now())
        first = alert_engine.serialize()
        assert len(first) == 1
        assert first[0]["alert_type"] == ALERT_LOW_BATTERY
        assert first[0]["severity"] == "WARN"
        alert_engine._run_health_sweep(datetime.now())
        assert len(alert_engine.serialize()) == 1

    def test_state_path_then_sweep_no_double_record(
        self, hass: HomeAssistant, alert_engine: AlertEngine
    ) -> None:
        """A state-path jam alert + later sweep -> no double-record."""
        alert_engine._eval_jam(LOCK_ENTITY, "jammed", {})
        assert len(alert_engine.serialize()) == 1
        # The sweep sees the SAME already-alerted episode -> records nothing.
        hass.states.async_set(LOCK_ENTITY, "jammed")
        alert_engine._run_health_sweep(datetime.now())
        assert len(alert_engine.serialize()) == 1

    def test_sweep_healthy_member_records_nothing(
        self, hass: HomeAssistant, alert_engine: AlertEngine
    ) -> None:
        """A locked member with good battery and online -> sweep is silent."""
        hass.states.async_set(LOCK_ENTITY, "locked")
        hass.states.async_set(BATTERY_ENTITY, "90")
        alert_engine._run_health_sweep(datetime.now())
        assert alert_engine.serialize() == []

    def test_sweep_does_not_touch_sustained(
        self, hass: HomeAssistant, alert_engine: AlertEngine
    ) -> None:
        """The health sweep never records a sustained_unlock alert."""
        hass.states.async_set(LOCK_ENTITY, "unlocked")
        alert_engine._run_health_sweep(datetime.now())
        types = {a["alert_type"] for a in alert_engine.serialize()}
        assert ALERT_SUSTAINED not in types

    def test_sweep_skipped_when_detectors_disabled_in_production(
        self, hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """dev_mock OFF + all health detectors disabled -> sweep is a no-op."""
        _register_zone(hass, _build_zone(jam=False, offline=False, low_battery=False))
        engine = AlertEngine(hass)
        monkeypatch.setattr(
            "custom_components.smart_lock_manager.alert_detectors.is_dev_mock",
            lambda: False,
        )
        monkeypatch.setattr(
            "custom_components.smart_lock_manager.alert_sweeps.is_dev_mock",
            lambda: False,
        )
        hass.states.async_set(LOCK_ENTITY, "jammed")
        hass.states.async_set(BATTERY_ENTITY, "1")
        engine._run_health_sweep(datetime.now())
        assert engine.serialize() == []


class TestMemberMetaResolution:
    """member_meta companion-entity overrides resolve before auto-discovery.

    Real-world Z-Wave locks expose jam / battery companions whose ids do not
    match the auto-discovery convention. These tests assert the detectors and
    the health sweep resolve the configured ``member_meta`` entity FIRST and
    fall back to auto-discovery when it is absent.
    """

    @pytest.fixture(autouse=True)
    def _no_persist(self) -> None:
        """Stub alert-log persistence so the sweep doesn't hit storage."""
        with patch(
            "custom_components.smart_lock_manager.alert_engine.save_alert_log",
            AsyncMock(),
        ):
            yield

    @staticmethod
    def _zone_with_meta(**meta: str) -> Zone:
        """One-member zone with all health detectors on + member_meta override."""
        from custom_components.smart_lock_manager.models.zone_settings import (
            MemberMeta,
        )

        zone = _build_zone()
        zone.settings.member_meta[LOCK_ENTITY] = MemberMeta(
            jam_sensor=meta.get("jam_sensor", ""),
            battery_entity=meta.get("battery_entity", ""),
        )
        return zone

    def test_jam_resolves_member_meta_sensor_sweep(self, hass: HomeAssistant) -> None:
        """A non-conventional jam_sensor reading 'on' fires the jam sweep."""
        _register_zone(
            hass, self._zone_with_meta(jam_sensor="binary_sensor.front_lock_jammed")
        )
        engine = AlertEngine(hass)
        # Lock itself is fine; only the explicitly-configured jam sensor is on.
        hass.states.async_set(LOCK_ENTITY, "locked")
        hass.states.async_set("binary_sensor.front_lock_jammed", "on")
        # The auto-discovery name is intentionally absent / off.
        engine._run_health_sweep(datetime.now())
        alerts = engine.serialize()
        assert len(alerts) == 1
        assert alerts[0]["alert_type"] == ALERT_JAM

    def test_jam_member_meta_subscribed_state_path(self, hass: HomeAssistant) -> None:
        """The configured jam sensor is monitored AND drives the state path."""
        custom_jam = "binary_sensor.demo_side_access_control_lock_jammed"
        _register_zone(hass, self._zone_with_meta(jam_sensor=custom_jam))
        engine = AlertEngine(hass)
        # The custom jam sensor is in the subscribed entity set.
        assert custom_jam in engine._monitored_entities()
        # A state change on it routes back to the lock and fires jam.
        hass.states.async_set(LOCK_ENTITY, "locked")
        hass.states.async_set(custom_jam, "on")
        event = type(
            "E",
            (),
            {
                "data": {
                    "entity_id": custom_jam,
                    "new_state": hass.states.get(custom_jam),
                }
            },
        )()
        engine._handle_state_event(event)  # type: ignore[arg-type]
        alerts = engine.serialize()
        assert len(alerts) == 1
        assert alerts[0]["alert_type"] == ALERT_JAM

    def test_battery_resolves_member_meta_entity_sweep(
        self, hass: HomeAssistant
    ) -> None:
        """A non-conventional battery_entity below threshold fires low_battery."""
        _register_zone(
            hass,
            self._zone_with_meta(battery_entity="sensor.front_battery_level"),
        )
        engine = AlertEngine(hass)
        hass.states.async_set(LOCK_ENTITY, "locked")
        hass.states.async_set("sensor.front_battery_level", "8")
        # The auto-discovery name is absent.
        engine._run_health_sweep(datetime.now())
        alerts = engine.serialize()
        assert len(alerts) == 1
        assert alerts[0]["alert_type"] == ALERT_LOW_BATTERY

    def test_battery_member_meta_subscribed_state_path(
        self, hass: HomeAssistant
    ) -> None:
        """The configured battery entity is monitored AND drives the state path."""
        custom_batt = "sensor.front_battery_level"
        _register_zone(hass, self._zone_with_meta(battery_entity=custom_batt))
        engine = AlertEngine(hass)
        assert custom_batt in engine._monitored_entities()
        hass.states.async_set(custom_batt, "9")
        event = type(
            "E",
            (),
            {
                "data": {
                    "entity_id": custom_batt,
                    "new_state": hass.states.get(custom_batt),
                }
            },
        )()
        engine._handle_state_event(event)  # type: ignore[arg-type]
        alerts = engine.serialize()
        assert len(alerts) == 1
        assert alerts[0]["alert_type"] == ALERT_LOW_BATTERY

    def test_auto_discovery_fallback_when_no_member_meta(
        self, hass: HomeAssistant, alert_engine: AlertEngine
    ) -> None:
        """With no member_meta, the auto-discovery companions still resolve."""
        # No member_meta on this zone (alert_engine fixture uses _build_zone()).
        assert BATTERY_ENTITY in alert_engine._monitored_entities()
        assert "binary_sensor.front_test_jammed" in alert_engine._monitored_entities()
        hass.states.async_set(LOCK_ENTITY, "locked")
        hass.states.async_set("binary_sensor.front_test_jammed", "on")
        hass.states.async_set(BATTERY_ENTITY, "5")
        alert_engine._run_health_sweep(datetime.now())
        types = {a["alert_type"] for a in alert_engine.serialize()}
        assert ALERT_JAM in types
        assert ALERT_LOW_BATTERY in types

    def test_battery_unknown_state_no_crash_no_alert(self, hass: HomeAssistant) -> None:
        """A member_meta battery reading 'unavailable' -> no crash, no alert."""
        _register_zone(
            hass,
            self._zone_with_meta(battery_entity="sensor.front_battery_level"),
        )
        engine = AlertEngine(hass)
        hass.states.async_set(LOCK_ENTITY, "locked")
        hass.states.async_set("sensor.front_battery_level", "unavailable")
        engine._run_health_sweep(datetime.now())
        assert engine.serialize() == []
        # State path too: an unknown reading is ignored safely.
        engine._eval_low_battery(LOCK_ENTITY, "unknown")
        assert engine.serialize() == []


class TestSweepIntervalConfig:
    """The global set_sweep_intervals settings store + validation."""

    async def test_load_defaults_when_absent(self, hass: HomeAssistant) -> None:
        """An unprimed store yields the documented cadence defaults."""
        from custom_components.smart_lock_manager.storage.global_settings import (
            DEFAULT_HEALTH_SWEEP_MINUTES,
            DEFAULT_OUTSIDE_HOURS_SWEEP_MINUTES,
            load_global_settings,
        )

        with patch(
            "custom_components.smart_lock_manager.storage.global_settings.Store"
        ) as store_cls:
            store_cls.return_value.async_load = AsyncMock(return_value=None)
            settings = await load_global_settings(hass)
        assert (
            settings["outside_hours_sweep_minutes"]
            == DEFAULT_OUTSIDE_HOURS_SWEEP_MINUTES
        )
        assert settings["health_sweep_minutes"] == DEFAULT_HEALTH_SWEEP_MINUTES

    async def test_save_persists_and_caches(self, hass: HomeAssistant) -> None:
        """Saving a partial blob merges, persists, and re-primes the cache."""
        from custom_components.smart_lock_manager.storage.global_settings import (
            get_cached_global_settings,
            save_global_settings,
        )

        saved = {}

        async def _fake_save(blob):
            saved.update(blob)

        with patch(
            "custom_components.smart_lock_manager.storage.global_settings.Store"
        ) as store_cls:
            store_cls.return_value.async_save = AsyncMock(side_effect=_fake_save)
            await save_global_settings(hass, {"health_sweep_minutes": 30})
        assert saved["health_sweep_minutes"] == 30
        # Outside-hours retains its default in the merged persisted blob.
        assert "outside_hours_sweep_minutes" in saved
        assert get_cached_global_settings()["health_sweep_minutes"] == 30

    def test_out_of_bounds_values_are_clamped_to_defaults(self) -> None:
        """Values outside [1, 1440] are rejected, falling back to defaults."""
        from custom_components.smart_lock_manager.storage.global_settings import (
            DEFAULT_HEALTH_SWEEP_MINUTES,
            _shape,
        )

        shaped = _shape({"health_sweep_minutes": 99999, "bogus": 1})
        assert shaped["health_sweep_minutes"] == DEFAULT_HEALTH_SWEEP_MINUTES

    def test_service_schema_rejects_zero_and_requires_a_key(self) -> None:
        """The voluptuous schema enforces the 1..1440 range + at-least-one."""
        import voluptuous as vol

        from custom_components.smart_lock_manager import (
            SET_SWEEP_INTERVALS_SCHEMA,
        )

        # Valid.
        assert SET_SWEEP_INTERVALS_SCHEMA({"outside_hours_sweep_minutes": 5}) == {
            "outside_hours_sweep_minutes": 5
        }
        # Zero is below the minimum.
        with pytest.raises(vol.Invalid):
            SET_SWEEP_INTERVALS_SCHEMA({"health_sweep_minutes": 0})
        # Above the maximum.
        with pytest.raises(vol.Invalid):
            SET_SWEEP_INTERVALS_SCHEMA({"health_sweep_minutes": 5000})
        # Empty payload must be rejected (at-least-one-key).
        with pytest.raises(vol.Invalid):
            SET_SWEEP_INTERVALS_SCHEMA({})


class TestDetectorGating:
    """``_detector_enabled`` honors dev_mock vs production observe semantics."""

    def test_dev_mock_observes_disabled_detector(
        self, alert_engine: AlertEngine, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """With dev_mock ON, a disabled detector still observes (observe-all)."""
        monkeypatch.setattr(
            "custom_components.smart_lock_manager.alert_detectors.is_dev_mock",
            lambda: True,
        )
        assert alert_engine._detector_enabled(LOCK_ENTITY, configured=False) is True

    def test_production_skips_disabled_detector(
        self, alert_engine: AlertEngine, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """With dev_mock OFF, a disabled detector does NOT observe."""
        monkeypatch.setattr(
            "custom_components.smart_lock_manager.alert_detectors.is_dev_mock",
            lambda: False,
        )
        assert alert_engine._detector_enabled(LOCK_ENTITY, configured=False) is False

    def test_production_observes_enabled_detector(
        self, alert_engine: AlertEngine, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """With dev_mock OFF, an enabled detector observes (honors zone flag)."""
        monkeypatch.setattr(
            "custom_components.smart_lock_manager.alert_detectors.is_dev_mock",
            lambda: False,
        )
        assert alert_engine._detector_enabled(LOCK_ENTITY, configured=True) is True


class TestAlertSubjects:
    """Pyscript-parity email subject wording (keyed on member entity id)."""

    def test_subject_prefix_derived_from_location_name(self) -> None:
        """The subject prefix is derived from the HA location name."""
        from custom_components.smart_lock_manager.notifications_bodies import (
            subject_prefix_for,
        )

        assert subject_prefix_for("My Home") == "My Home -"
        # Blank / missing location name falls back to the generic prefix.
        assert subject_prefix_for("") == "Home Assistant -"
        assert subject_prefix_for(None) == "Home Assistant -"

    def test_subject_uses_supplied_prefix(self) -> None:
        """A supplied prefix is used verbatim for the subject line."""
        alert = {
            "alert_type": ALERT_SUSTAINED,
            "member_entity_id": "lock.demo_front",
            "message": "Unlocked >15s without re-lock",
            "is_recovery": False,
        }
        assert build_alert_subject(alert, "My Home -") == (
            "My Home - lock.demo_front unlocked >15s"
        )

    def test_sustained_subject_default_prefix(self) -> None:
        """sustained_unlock -> '{prefix} {entity} unlocked >{n}s'."""
        alert = {
            "alert_type": ALERT_SUSTAINED,
            "member_entity_id": "lock.demo_front",
            "door_name": "Demo Front",
            "message": "Unlocked >15s without re-lock",
            "is_recovery": False,
        }
        assert build_alert_subject(alert) == (
            "Home Assistant - lock.demo_front unlocked >15s"
        )

    def test_sustained_recovery_subject(self) -> None:
        """sustained_unlock recovery -> '{prefix} {entity} locked again'."""
        alert = {
            "alert_type": ALERT_SUSTAINED,
            "member_entity_id": "lock.demo_side",
            "is_recovery": True,
            "message": "Re-locked after sustained-unlock alert",
        }
        assert (
            build_alert_subject(alert) == "Home Assistant - lock.demo_side locked again"
        )

    def test_outside_hours_subject(self) -> None:
        """outside_hours -> '{prefix} door {entity} unlocked outside ...'."""
        alert = {
            "alert_type": ALERT_OUTSIDE_HOURS,
            "member_entity_id": "lock.demo_back",
            "message": "Unlocked outside business hours",
            "is_recovery": False,
        }
        assert build_alert_subject(alert) == (
            "Home Assistant - door lock.demo_back unlocked outside business hours"
        )

    def test_outside_hours_recovery_subject(self) -> None:
        """outside_hours recovery -> '{prefix} {entity} locked again'."""
        alert = {
            "alert_type": ALERT_OUTSIDE_HOURS,
            "member_entity_id": "lock.demo_back",
            "is_recovery": True,
        }
        assert (
            build_alert_subject(alert) == "Home Assistant - lock.demo_back locked again"
        )

    def test_auto_lock_failed_subject_uses_name(self) -> None:
        """auto_lock_failed -> '{prefix} {name} FAILED to auto-lock at COB'."""
        alert = {
            "alert_type": "auto_lock_failed",
            "member_entity_id": "lock.demo_front",
            "door_name": "Demo Front",
            "message": "scheduled auto-lock FAILED after 3 attempt(s)",
        }
        # Uses the friendly NAME (not the entity id).
        assert build_alert_subject(alert) == (
            "Home Assistant - Demo Front FAILED to auto-lock at COB"
        )

    def test_slm_only_subjects(self) -> None:
        """Native-only types (jam/low_battery/offline) use the house style."""
        jam = {
            "alert_type": ALERT_JAM,
            "member_entity_id": "lock.demo_office",
            "is_recovery": False,
        }
        assert build_alert_subject(jam) == "Home Assistant - lock.demo_office jammed"

        battery = {
            "alert_type": ALERT_LOW_BATTERY,
            "member_entity_id": "lock.demo_unit_a",
            "message": "Battery low (8%)",
            "is_recovery": False,
        }
        assert build_alert_subject(battery) == (
            "Home Assistant - lock.demo_unit_a battery low (8%)"
        )

        offline = {
            "alert_type": ALERT_OFFLINE,
            "member_entity_id": "lock.demo_unit_b",
            "is_recovery": False,
        }
        assert (
            build_alert_subject(offline) == "Home Assistant - lock.demo_unit_b offline"
        )

    def test_unknown_type_falls_back_to_house_style(self) -> None:
        """An unknown alert_type still yields a non-empty '{prefix} ...'."""
        alert = {
            "alert_type": "brand_new_type",
            "member_entity_id": "lock.x",
            "message": "something happened",
        }
        assert build_alert_subject(alert) == (
            "Home Assistant - lock.x something happened"
        )


class TestNotificationRouting:
    """Every alert type routes through the dispatcher per the zone notify cfg."""

    @pytest.fixture(autouse=True)
    def _no_persist(self) -> None:
        """Stub alert-log persistence so recording doesn't hit storage."""
        with patch(
            "custom_components.smart_lock_manager.alert_engine.save_alert_log",
            AsyncMock(),
        ):
            yield

    @pytest.fixture
    def notify_engine(self, hass: HomeAssistant) -> AlertEngine:
        """Build an engine over a zone with BOTH notify channels enabled."""
        zone = _build_zone()
        zone.settings.notify.email.enabled = True
        zone.settings.notify.mobile.enabled = True
        _register_zone(hass, zone)
        return AlertEngine(hass)

    async def _intents_for(self, engine: AlertEngine, alert_type: str) -> list:
        """Simulate one alert and return its rendered notify_intents.

        Stubs the SMTP2GO creds so the DRY-RUN email renders without reading
        secrets.yaml from disk, then awaits the async ``_notify`` dispatch task
        ``_record`` schedules.
        """
        creds = {
            "user": "u",
            "pass": "p",
            "from": "from@x",
            "to": "to@x",
            "kind_to": {"alert": []},
        }
        with patch.object(
            engine._dispatcher.email, "_creds", AsyncMock(return_value=creds)
        ):
            engine.dev_simulate(alert_type, LOCK_ENTITY)
            # _record schedules async_create_task(self._notify); await it.
            await engine.hass.async_block_till_done()
        match = next(a for a in engine.serialize() if a["alert_type"] == alert_type)
        return match["notify_intents"]

    async def test_low_battery_and_offline_route_email_and_mobile(
        self, hass: HomeAssistant, notify_engine: AlertEngine
    ) -> None:
        """low_battery + offline produce email+mobile intents (DRY-RUN)."""
        for alert_type in (ALERT_LOW_BATTERY, ALERT_OFFLINE):
            intents = await self._intents_for(notify_engine, alert_type)
            channels = {i["channel"] for i in intents}
            assert channels == {"email", "mobile"}, alert_type
            # Everything stays DRY-RUN — nothing is actually sent.
            assert all(i["dry_run"] is True for i in intents), alert_type
            assert all(i["sent"] is False for i in intents), alert_type
            email = next(i for i in intents if i["channel"] == "email")
            # The alert subject (post-wrap) carries the per-install prefix
            # (derived from the HA location name) plus the entity line.
            assert LOCK_ENTITY in email["subject"], alert_type


class TestAutoLockEngine:
    """Auto-lock verify/retry, OBSERVE recording, refresh, dev triggers."""

    @pytest.fixture(autouse=True)
    def _dev_mock_on(self) -> None:
        """Force SLM_DEV_MOCK on so the engine may issue (dummy) locks."""
        with patch.dict(os.environ, {"SLM_DEV_MOCK": "1"}):
            yield

    @pytest.fixture
    def auto_engine(self, hass: HomeAssistant) -> AutoLockEngine:
        """Build an AutoLockEngine over a COB+idle-enabled zone."""
        _register_zone(hass, _build_zone(scheduled=True, idle=True))
        return AutoLockEngine(hass)

    async def test_start_refresh_stop_idempotent(
        self, hass: HomeAssistant, auto_engine: AutoLockEngine
    ) -> None:
        """Repeated refresh keeps the subscription count stable (no dupes)."""
        await auto_engine.async_start()
        assert auto_engine._started is True
        before = len(auto_engine._unsubs)
        assert before >= 1  # COB trigger + idle listener
        for _ in range(3):
            auto_engine.async_refresh()
        # Same subscription count after repeated refreshes (no accumulation).
        assert len(auto_engine._unsubs) == before
        auto_engine.async_stop()
        assert auto_engine._unsubs == []

    def test_refresh_before_start_is_noop(self, auto_engine: AutoLockEngine) -> None:
        """Refresh before start does nothing."""
        auto_engine.async_refresh()
        assert auto_engine._unsubs == []

    async def test_refresh_subscribes_newly_enabled_idle(
        self, hass: HomeAssistant
    ) -> None:
        """Enabling idle_auto_lock then refreshing arms the idle timer live.

        Proves Task 2: a settings change is picked up WITHOUT restart. The zone
        starts with idle OFF (engine starts with no idle listener); after the
        toggle flips and async_refresh runs, an unlock now arms an idle timer.
        """
        zone = _build_zone(idle=False)
        _register_zone(hass, zone)
        engine = AutoLockEngine(hass)
        await engine.async_start()
        # Idle disabled at start -> unlock does not arm a timer.
        hass.states.async_set(LOCK_ENTITY, "unlocked")
        await hass.async_block_till_done()
        assert engine._idle_timers == {}

        # Simulate update_zone_settings flipping idle on, then the live refresh.
        zone.settings.idle_auto_lock.enabled = True
        engine.async_refresh()

        # A fresh unlock now arms the idle timer — no restart needed.
        hass.states.async_set(LOCK_ENTITY, "locked")
        hass.states.async_set(LOCK_ENTITY, "unlocked")
        await hass.async_block_till_done()
        assert LOCK_ENTITY in engine._idle_timers
        engine.async_stop()

    async def test_dev_trigger_scheduled_locks_member(
        self, hass: HomeAssistant, auto_engine: AutoLockEngine
    ) -> None:
        """A scheduled dev_trigger locks the member and records success."""
        hass.states.async_set(LOCK_ENTITY, "locked")
        calls: list = []

        async def _lock_handler(call) -> None:
            calls.append(call.data.get("entity_id"))

        hass.services.async_register("lock", "lock", _lock_handler)
        with patch(
            "custom_components.smart_lock_manager.auto_lock_verify.MOCK_BOLT.read",
            return_value="locked",
        ):
            await auto_engine.dev_trigger("zone_test", MODE_SCHEDULED)
        records = auto_engine.serialize()
        assert records[0]["mode"] == MODE_SCHEDULED
        assert records[0]["result"] == "success"
        assert LOCK_ENTITY in calls

    async def test_dev_trigger_fail_verify_routes_alert(
        self, hass: HomeAssistant, auto_engine: AutoLockEngine
    ) -> None:
        """A forced verify failure records a fail and routes a CRIT alert."""
        from custom_components.smart_lock_manager.alert_engine import (
            ALERT_ENGINE_KEY,
        )

        hass.states.async_set(LOCK_ENTITY, "unlocked")
        # An AlertEngine must be present for the failure to route.
        alert = AlertEngine(hass)
        hass.data[ALERT_ENGINE_KEY] = alert
        # settle_seconds 0 keeps the retry loop instant.
        zone = hass.data[ZONE_REGISTRY_KEY]["zone_test"]
        zone.settings.scheduled_auto_lock.settle_seconds = 0
        zone.settings.scheduled_auto_lock.max_attempts = 1

        async def _lock_handler(call) -> None:
            return None

        hass.services.async_register("lock", "lock", _lock_handler)
        with patch(
            "custom_components.smart_lock_manager.alert_engine.save_alert_log",
            AsyncMock(),
        ):
            await auto_engine.dev_trigger("zone_test", MODE_SCHEDULED, fail_verify=True)
        records = auto_engine.serialize()
        assert records[0]["result"] == "failed"
        # The failure was surfaced as a CRIT alert on the alert engine.
        assert any(a["alert_type"] == "auto_lock_failed" for a in alert.serialize())

    async def test_observe_mode_records_would_lock(self, hass: HomeAssistant) -> None:
        """In OBSERVE posture the engine records a would_lock, issues nothing."""
        # Not dev-mock and no real-autolock flag -> OBSERVE: record intent only.
        _register_zone(hass, _build_zone(scheduled=True))
        engine = AutoLockEngine(hass)
        hass.states.async_set(LOCK_ENTITY, "unlocked")
        calls: list = []

        async def _lock_handler(call) -> None:
            calls.append(call.data.get("entity_id"))

        hass.services.async_register("lock", "lock", _lock_handler)
        with patch.dict(
            os.environ, {"SLM_DEV_MOCK": "", "SLM_ENABLE_REAL_AUTOLOCK": ""}
        ):
            await engine.dev_trigger("zone_test", MODE_SCHEDULED)
        records = engine.serialize()
        assert records[0]["result"] == "would_lock"
        # No real lock.lock issued in OBSERVE.
        assert calls == []


class TestAutoLockHelpers:
    """Pure module helpers."""

    def test_real_autolock_enabled_flag(self) -> None:
        """The real-autolock flag is truthy only for explicit on-values."""
        with patch.dict(os.environ, {"SLM_ENABLE_REAL_AUTOLOCK": "true"}):
            assert real_autolock_enabled() is True
        with patch.dict(os.environ, {"SLM_ENABLE_REAL_AUTOLOCK": "no"}):
            assert real_autolock_enabled() is False

    def test_dig_bolt_status_flat_and_nested(self) -> None:
        """Dig boltStatus from flat + nested dicts, None otherwise."""
        assert _dig_bolt_status({"boltStatus": "Locked"}) == "locked"
        assert _dig_bolt_status({"lock.x": {"boltStatus": "Unlocked"}}) == "unlocked"
        assert _dig_bolt_status("not-a-dict") is None
        assert _dig_bolt_status({"other": 1}) is None


class TestAlertBodies:
    """Plain-text email BODY wording (recovery-aware, byte-for-byte)."""

    def test_outside_hours_body_alert(self) -> None:
        """outside_hours alert body lists lock / state / timestamps."""
        alert = {
            "alert_type": ALERT_OUTSIDE_HOURS,
            "member_entity_id": "lock.demo_front",
            "friendly_name": "Demo Front",
            "timestamp": "2026-06-19T10:00:00",
            "last_changed": "2026-06-19T09:55:00",
            "message": "Unlocked outside business hours",
            "severity": "CRIT",
            "is_recovery": False,
        }
        assert build_alert_body(alert) == (
            "Lock: Demo Front (lock.demo_front)\n"
            "State: unlocked\n"
            "Timestamp: 2026-06-19T10:00:00\n"
            "Last changed: 2026-06-19T09:55:00"
        )

    def test_outside_hours_body_recovery(self) -> None:
        """outside_hours recovery body closes the alert."""
        alert = {
            "alert_type": ALERT_OUTSIDE_HOURS,
            "member_entity_id": "lock.demo_front",
            "friendly_name": "Demo Front",
            "timestamp": "2026-06-19T10:00:00",
            "last_changed": "2026-06-19T09:55:00",
            "message": "Unlocked outside business hours",
            "severity": "CRIT",
            "is_recovery": True,
        }
        assert build_alert_body(alert) == (
            "Demo Front (lock.demo_front) was previously alerted as unlocked.\n"
            "Now showing state: locked.\n"
            "Last unlocked at: 2026-06-19T09:55:00\n"
            "Recovery timestamp: 2026-06-19T10:00:00\n"
            "Alert closed."
        )

    def test_sustained_body_alert(self) -> None:
        """sustained_unlock alert body lists elapsed seconds + severity."""
        alert = {
            "alert_type": ALERT_SUSTAINED,
            "member_entity_id": "lock.demo_side",
            "friendly_name": "Demo Side",
            "timestamp": "2026-06-19T10:00:00",
            "last_changed": "2026-06-19T09:59:00",
            "message": "Unlocked >30s without re-lock (dev-simulated)",
            "severity": "CRIT",
            "is_recovery": False,
        }
        assert build_alert_body(alert) == (
            "Lock: Demo Side (lock.demo_side)\n"
            "State: unlocked\n"
            "Elapsed: 30s without re-lock\n"
            "Severity: CRIT"
        )

    def test_sustained_body_recovery(self) -> None:
        """sustained_unlock recovery body closes the alert."""
        alert = {
            "alert_type": ALERT_SUSTAINED,
            "member_entity_id": "lock.demo_side",
            "friendly_name": "Demo Side",
            "timestamp": "2026-06-19T10:00:00",
            "last_changed": "2026-06-19T09:59:00",
            "message": "Unlocked >30s without re-lock (dev-simulated)",
            "severity": "CRIT",
            "is_recovery": True,
        }
        assert build_alert_body(alert) == (
            "Demo Side (lock.demo_side) is now locked again.\n"
            "Previously alerted as sustained-unlocked.\n"
            "Last unlocked at: 2026-06-19T09:59:00\n"
            "Recovery timestamp: 2026-06-19T10:00:00\n"
            "Alert closed."
        )

    def test_jam_body_alert(self) -> None:
        """Jam alert body lists lock / jammed state / detail / timestamps."""
        alert = {
            "alert_type": ALERT_JAM,
            "member_entity_id": "lock.demo_office",
            "friendly_name": "Demo Office",
            "timestamp": "2026-06-19T10:00:00",
            "last_changed": "2026-06-19T09:58:00",
            "message": "Lock reported jammed",
            "is_recovery": False,
        }
        assert build_alert_body(alert) == (
            "Lock: Demo Office (lock.demo_office)\n"
            "State: jammed\n"
            "Detail: Lock reported jammed\n"
            "Timestamp: 2026-06-19T10:00:00\n"
            "Last changed: 2026-06-19T09:58:00"
        )

    def test_jam_body_recovery(self) -> None:
        """Jam recovery body closes the alert."""
        alert = {
            "alert_type": ALERT_JAM,
            "member_entity_id": "lock.demo_office",
            "friendly_name": "Demo Office",
            "timestamp": "2026-06-19T10:00:00",
            "message": "Jam cleared",
            "is_recovery": True,
        }
        assert build_alert_body(alert) == (
            "Demo Office (lock.demo_office) was previously alerted as jammed.\n"
            "Now showing state: jam cleared.\n"
            "Recovery timestamp: 2026-06-19T10:00:00\n"
            "Alert closed."
        )

    def test_low_battery_body_alert(self) -> None:
        """low_battery alert body carries the percent + detail."""
        alert = {
            "alert_type": ALERT_LOW_BATTERY,
            "member_entity_id": "lock.demo_unit_a",
            "friendly_name": "Demo Unit A",
            "timestamp": "2026-06-19T10:00:00",
            "message": "Battery low (8%)",
            "is_recovery": False,
        }
        assert build_alert_body(alert) == (
            "Lock: Demo Unit A (lock.demo_unit_a)\n"
            "State: battery low (8%)\n"
            "Detail: Battery low (8%)\n"
            "Timestamp: 2026-06-19T10:00:00"
        )

    def test_low_battery_body_recovery(self) -> None:
        """low_battery recovery body closes the alert with percent."""
        alert = {
            "alert_type": ALERT_LOW_BATTERY,
            "member_entity_id": "lock.demo_unit_a",
            "friendly_name": "Demo Unit A",
            "timestamp": "2026-06-19T10:00:00",
            "message": "Battery recovered (30%)",
            "is_recovery": True,
        }
        assert build_alert_body(alert) == (
            "Demo Unit A (lock.demo_unit_a) battery recovered (30%).\n"
            "Detail: Battery recovered (30%)\n"
            "Recovery timestamp: 2026-06-19T10:00:00\n"
            "Alert closed."
        )

    def test_offline_body_alert(self) -> None:
        """Offline alert body lists offline state / detail / timestamps."""
        alert = {
            "alert_type": ALERT_OFFLINE,
            "member_entity_id": "lock.demo_unit_b",
            "friendly_name": "Demo Unit B",
            "timestamp": "2026-06-19T10:00:00",
            "last_changed": "2026-06-19T09:50:00",
            "message": "No response from node",
            "is_recovery": False,
        }
        assert build_alert_body(alert) == (
            "Lock: Demo Unit B (lock.demo_unit_b)\n"
            "State: offline\n"
            "Detail: No response from node\n"
            "Timestamp: 2026-06-19T10:00:00\n"
            "Last changed: 2026-06-19T09:50:00"
        )

    def test_offline_body_recovery(self) -> None:
        """Offline recovery body closes the alert."""
        alert = {
            "alert_type": ALERT_OFFLINE,
            "member_entity_id": "lock.demo_unit_b",
            "friendly_name": "Demo Unit B",
            "timestamp": "2026-06-19T10:00:00",
            "message": "Node responded",
            "is_recovery": True,
        }
        assert build_alert_body(alert) == (
            "Demo Unit B (lock.demo_unit_b) is back online.\n"
            "Recovery timestamp: 2026-06-19T10:00:00\n"
            "Alert closed."
        )

    def test_auto_lock_failed_body(self) -> None:
        """auto_lock_failed body carries the failure detail + physical check."""
        alert = {
            "alert_type": "auto_lock_failed",
            "member_entity_id": "lock.demo_back",
            "friendly_name": "Demo Back",
            "timestamp": "2026-06-19T17:30:00",
            "last_changed": "2026-06-19T17:29:00",
            "message": "FAILED to auto-lock after 3 attempts",
            "severity": "CRIT",
        }
        assert build_alert_body(alert) == (
            "Lock: Demo Back (lock.demo_back)\n"
            "State: failed to auto-lock\n"
            "Detail: FAILED to auto-lock after 3 attempts\n"
            "Timestamp: 2026-06-19T17:30:00\n"
            "\n"
            "Physical check needed — the bolt could not be confirmed thrown."
        )


class TestAlertSnooze:
    """Module-cache snooze accessors + the engine record-but-suppress path."""

    ZONE_ID = "zone_test"

    @pytest.fixture(autouse=True)
    def _reset_snooze_cache(self) -> None:
        """Reset the module-level snooze cache before AND after each test."""
        clear_global_snooze()
        for zone_id in ("zone_a", "zone_b", self.ZONE_ID):
            clear_zone_snooze(zone_id)
        yield
        clear_global_snooze()
        for zone_id in ("zone_a", "zone_b", self.ZONE_ID):
            clear_zone_snooze(zone_id)

    @pytest.fixture(autouse=True)
    def _no_persist(self) -> None:
        """Stub alert-log persistence so recording doesn't hit storage."""
        with patch(
            "custom_components.smart_lock_manager.alert_engine.save_alert_log",
            AsyncMock(),
        ):
            yield

    @pytest.fixture
    def notify_engine(self, hass: HomeAssistant) -> AlertEngine:
        """Build an engine over a zone with BOTH notify channels enabled."""
        zone = _build_zone()
        zone.settings.notify.email.enabled = True
        zone.settings.notify.mobile.enabled = True
        _register_zone(hass, zone)
        return AlertEngine(hass)

    async def _simulate(self, engine: AlertEngine, alert_type: str) -> dict:
        """Simulate one alert, await the dispatch, return its newest record."""
        creds = {
            "user": "u",
            "pass": "p",
            "from": "from@x",
            "to": "to@x",
            "kind_to": {"alert": []},
        }
        with patch.object(
            engine._dispatcher.email, "_creds", AsyncMock(return_value=creds)
        ):
            engine.dev_simulate(alert_type, LOCK_ENTITY)
            await engine.hass.async_block_till_done()
        return next(
            a for a in reversed(engine.serialize()) if a["alert_type"] == alert_type
        )

    def test_global_expiry_unit(self) -> None:
        """A future global deadline is active; a past one is not."""
        set_global_snooze(time.time() + 100)
        assert global_snooze_active() is True
        set_global_snooze(time.time() - 100)
        assert global_snooze_active() is False
        # now_epoch arg crosses the boundary against a fixed deadline.
        deadline = time.time()
        set_global_snooze(deadline)
        assert global_snooze_active(now_epoch=deadline - 50) is True
        assert global_snooze_active(now_epoch=deadline + 50) is False

    def test_per_zone_vs_global_isolation(self) -> None:
        """Per-zone snooze is isolated; global covers every zone."""
        now = time.time()
        set_zone_snooze("zone_a", now + 100)
        assert zone_snooze_active("zone_a") is True
        assert zone_snooze_active("zone_b") is False
        assert global_snooze_active() is False
        assert snooze_active("zone_a") is True
        assert snooze_active("zone_b") is False

        set_global_snooze(now + 100)
        assert snooze_active("zone_b") is True

        clear_global_snooze()
        assert snooze_active("zone_b") is False
        assert snooze_active("zone_a") is True

    async def test_snooze_does_not_suppress_state_change_edges(
        self, hass: HomeAssistant, notify_engine: AlertEngine
    ) -> None:
        """State-change Alerts/Recoveries bypass snooze (always dispatch).

        Under the origin model, ``dev_simulate`` records origin="state_change"
        (a rising/falling edge). Those ALWAYS dispatch — snooze must NOT suppress
        them — but the record is still flagged ``snoozed`` with
        ``snooze_bypassed`` so the API/panel can show the bypass.
        """
        # Resolve the test lock's zone id off a real recorded alert.
        record = await self._simulate(notify_engine, ALERT_LOW_BATTERY)
        zone_id = record["zone_id"]
        assert zone_id == self.ZONE_ID
        # NO snooze -> notified, not snoozed.
        assert record["notify_intents"] != []
        assert record["snoozed"] is False
        assert record["origin"] == "state_change"

        # ZONE snooze -> state_change edge STILL dispatches (bypass), flagged.
        set_zone_snooze(zone_id, time.time() + 1000)
        record = await self._simulate(notify_engine, ALERT_OFFLINE)
        assert record["notify_intents"] != []
        assert record["snoozed"] is True
        assert record["snooze_bypassed"] is True
        assert any(a["alert_type"] == ALERT_OFFLINE for a in notify_engine.serialize())

        # GLOBAL snooze (zone cleared) -> still dispatches (state_change).
        clear_zone_snooze(zone_id)
        set_global_snooze(time.time() + 1000)
        record = await self._simulate(notify_engine, ALERT_JAM)
        assert record["notify_intents"] != []
        assert record["snoozed"] is True
        assert record["snooze_bypassed"] is True

        # RESUME -> notifies again, no snooze flags.
        clear_global_snooze()
        clear_zone_snooze(zone_id)
        record = await self._simulate(notify_engine, ALERT_OUTSIDE_HOURS)
        assert record["notify_intents"] != []
        assert record["snoozed"] is False

    async def test_snooze_suppresses_timer_origin_nag(
        self, hass: HomeAssistant, notify_engine: AlertEngine
    ) -> None:
        """Timer-origin Nags ARE suppressed by snooze (recorded, no dispatch)."""
        zone_id = self.ZONE_ID
        set_zone_snooze(zone_id, time.time() + 1000)
        creds = {
            "user": "u",
            "pass": "p",
            "from": "from@x",
            "to": "to@x",
            "kind_to": {"alert": []},
        }
        with patch.object(
            notify_engine._dispatcher.email, "_creds", AsyncMock(return_value=creds)
        ):
            # Record a timer-origin Nag directly; snooze must suppress dispatch.
            notify_engine._record(
                LOCK_ENTITY,
                ALERT_OFFLINE,
                "WARN",
                "Still offline (unavailable)",
                origin="timer",
            )
            await notify_engine.hass.async_block_till_done()
        record = next(
            a
            for a in reversed(notify_engine.serialize())
            if a["alert_type"] == ALERT_OFFLINE
        )
        assert record["origin"] == "timer"
        assert record["snoozed"] is True
        assert "snooze_bypassed" not in record
        assert record["notify_intents"] == []

    def test_nag_throttle_and_seed(
        self, hass: HomeAssistant, alert_engine: AlertEngine
    ) -> None:
        """Rising-edge Alert seeds last_nag; timer re-fires only after interval.

        Drives the shared ``_check_outside_hours`` core directly. The rising-edge
        (state_change) Alert seeds ``last_nag`` so an immediately-following timer
        sweep does NOT double-hit; only after a full nag_interval does a timer
        sweep re-fire a single Nag.
        """
        flag = alert_engine._flag(LOCK_ENTITY, ALERT_OUTSIDE_HOURS)

        def _count() -> int:
            return sum(
                1
                for a in alert_engine.serialize()
                if a["alert_type"] == ALERT_OUTSIDE_HOURS
            )

        # nag_interval = 1 minute (60s) for the test.
        with (
            patch(
                "custom_components.smart_lock_manager.alert_engine."
                "get_cached_global_settings",
                return_value={"nag_interval_minutes": 1},
            ),
            patch.object(alert_engine, "_in_business_hours", return_value=False),
        ):
            # Rising-edge Alert (state_change): records once, seeds last_nag.
            alert_engine._check_outside_hours(
                LOCK_ENTITY, "unlocked", None, "CRIT", flag
            )
            assert _count() == 1
            assert flag["alerted"] is True
            seeded = flag["last_nag"]
            assert seeded is not None

            # Immediate timer sweep: throttled (seed < interval) -> NO new record.
            alert_engine._check_outside_hours(
                LOCK_ENTITY, "unlocked", None, "CRIT", flag, origin="timer"
            )
            assert _count() == 1

            # Pretend a full interval has elapsed -> exactly ONE re-Nag fires.
            flag["last_nag"] = seeded - 61
            alert_engine._check_outside_hours(
                LOCK_ENTITY, "unlocked", None, "CRIT", flag, origin="timer"
            )
            assert _count() == 2
            nags = [
                a
                for a in alert_engine.serialize()
                if a["alert_type"] == ALERT_OUTSIDE_HOURS and a["origin"] == "timer"
            ]
            assert len(nags) == 1

            # A second immediate timer sweep is throttled again.
            alert_engine._check_outside_hours(
                LOCK_ENTITY, "unlocked", None, "CRIT", flag, origin="timer"
            )
            assert _count() == 2

    def test_recovery_clears_last_nag(
        self, hass: HomeAssistant, alert_engine: AlertEngine
    ) -> None:
        """Falling-edge Recovery resets alerted AND last_nag for a clean episode."""
        flag = alert_engine._flag(LOCK_ENTITY, ALERT_OUTSIDE_HOURS)
        with patch.object(alert_engine, "_in_business_hours", return_value=False):
            alert_engine._eval_outside_hours(LOCK_ENTITY, "unlocked")
        assert flag["alerted"] is True
        assert flag["last_nag"] is not None
        # Recovery on re-lock.
        alert_engine._eval_outside_hours(LOCK_ENTITY, "locked")
        assert flag["alerted"] is False
        assert flag["last_nag"] is None
        recovery = next(
            a
            for a in alert_engine.serialize()
            if a["alert_type"] == ALERT_OUTSIDE_HOURS and a["is_recovery"]
        )
        assert recovery["is_recovery"] is True
        assert recovery["origin"] == "state_change"


class TestHealthNagPolicy:
    """CHANGE 1: HEALTH conditions alert ONCE then stay silent until recovery.

    DOOR conditions (outside_hours) keep their hourly timer re-nag. These tests
    drive the cores via the public ``_eval_*`` / sweep entrypoints and assert on
    the recorded stream — no re-nag for health, recovery still fires.
    """

    @pytest.fixture(autouse=True)
    def _no_persist(self) -> None:
        """Stub alert-log persistence so detectors don't hit storage."""
        with patch(
            "custom_components.smart_lock_manager.alert_engine.save_alert_log",
            AsyncMock(),
        ):
            yield

    def test_health_alert_fires_once_then_silent_on_sweep(
        self, hass: HomeAssistant, alert_engine: AlertEngine
    ) -> None:
        """low_battery alerts ONCE; repeated sweeps never re-nag; recovery fires."""
        # Rising-edge alert via the state path -> exactly one record.
        alert_engine._eval_low_battery(LOCK_ENTITY, "10")
        assert len(alert_engine.serialize()) == 1

        # Force the nag clock way back and sweep repeatedly: HEALTH must NOT
        # re-nag even though the nag interval has clearly elapsed.
        flag = alert_engine._flag(LOCK_ENTITY, ALERT_LOW_BATTERY)
        hass.states.async_set(LOCK_ENTITY, "locked")
        hass.states.async_set(BATTERY_ENTITY, "5")
        for _ in range(3):
            flag["last_nag"] = 0
            alert_engine._run_health_sweep(datetime.now())
        assert len(alert_engine.serialize()) == 1

        # Recovery via the state path still fires (above threshold + hysteresis).
        alert_engine._eval_low_battery(LOCK_ENTITY, "30")
        recovered = alert_engine.serialize()
        assert len(recovered) == 2
        assert recovered[0]["is_recovery"] is True

    def test_door_alert_still_renags_after_interval(
        self, hass: HomeAssistant, alert_engine: AlertEngine
    ) -> None:
        """outside_hours (DOOR) keeps re-nagging on the timer after the interval."""
        hass.states.async_set(LOCK_ENTITY, "unlocked")
        with patch(
            "custom_components.smart_lock_manager.alert_engine."
            "get_cached_global_settings",
            return_value={"nag_interval_minutes": 1},
        ):
            with patch.object(AlertEngine, "_in_business_hours", return_value=False):
                # Initial alert via the state path.
                alert_engine._eval_outside_hours(LOCK_ENTITY, "unlocked")
                assert len(alert_engine.serialize()) == 1

                # Elapse the nag clock, then a timer-origin sweep re-nags.
                flag = alert_engine._flag(LOCK_ENTITY, ALERT_OUTSIDE_HOURS)
                flag["last_nag"] = 0
                alert_engine._run_outside_hours_sweep(datetime.now())
        assert len(alert_engine.serialize()) == 2


class TestMutedStore:
    """CHANGE 2: per-(member, alert_type) mute store semantics (sync)."""

    @pytest.fixture(autouse=True)
    def _clear_muted_cache(self):
        """Reset the module-level muted cache around each test."""
        from custom_components.smart_lock_manager.storage import muted as muted_mod

        muted_mod._CACHE.clear()
        yield
        muted_mod._CACHE.clear()

    def test_is_muted_exact_pair_and_all(self) -> None:
        """is_muted matches the exact (member, type) pair OR the 'all' sentinel."""
        from custom_components.smart_lock_manager.storage import (
            clear_mute,
            is_muted,
            set_mute,
        )

        set_mute(LOCK_ENTITY, ALERT_LOW_BATTERY)
        assert is_muted(LOCK_ENTITY, ALERT_LOW_BATTERY) is True
        assert is_muted(LOCK_ENTITY, ALERT_JAM) is False
        assert is_muted("lock.other", ALERT_LOW_BATTERY) is False

        clear_mute(LOCK_ENTITY, ALERT_LOW_BATTERY)
        assert is_muted(LOCK_ENTITY, ALERT_LOW_BATTERY) is False

        set_mute(LOCK_ENTITY, "all")
        assert is_muted(LOCK_ENTITY, ALERT_JAM) is True
        assert is_muted(LOCK_ENTITY, ALERT_OFFLINE) is True

    def test_clear_mute_all_drops_everything(self) -> None:
        """clear_mute('all') removes every mute for the member."""
        from custom_components.smart_lock_manager.storage import (
            clear_mute,
            is_muted,
            set_mute,
        )

        set_mute(LOCK_ENTITY, ALERT_LOW_BATTERY)
        set_mute(LOCK_ENTITY, ALERT_JAM)
        clear_mute(LOCK_ENTITY, "all")
        assert is_muted(LOCK_ENTITY, ALERT_LOW_BATTERY) is False
        assert is_muted(LOCK_ENTITY, ALERT_JAM) is False

    def test_clear_one_type_drops_member_when_empty(self) -> None:
        """Clearing the last type for a member drops the member entirely."""
        from custom_components.smart_lock_manager.storage import (
            clear_mute,
            muted_state_for_api,
            set_mute,
        )

        set_mute(LOCK_ENTITY, ALERT_LOW_BATTERY)
        clear_mute(LOCK_ENTITY, ALERT_LOW_BATTERY)
        assert LOCK_ENTITY not in muted_state_for_api()["muted"]

    def test_muted_state_for_api_shape(self) -> None:
        """muted_state_for_api returns sorted JSON-safe lists keyed by member."""
        from custom_components.smart_lock_manager.storage import (
            muted_state_for_api,
            set_mute,
        )

        set_mute(LOCK_ENTITY, ALERT_OFFLINE)
        set_mute(LOCK_ENTITY, ALERT_LOW_BATTERY)
        state = muted_state_for_api()
        assert state == {
            "muted": {LOCK_ENTITY: sorted([ALERT_OFFLINE, ALERT_LOW_BATTERY])}
        }


class TestMuteSuppression:
    """CHANGE 2: a muted (member, type) is fully silent in the notify path."""

    @pytest.fixture(autouse=True)
    def _no_persist(self) -> None:
        """Stub alert-log persistence so recording doesn't hit storage."""
        with patch(
            "custom_components.smart_lock_manager.alert_engine.save_alert_log",
            AsyncMock(),
        ):
            yield

    @pytest.fixture(autouse=True)
    def _clear_muted_cache(self):
        """Reset the module-level muted cache around each test."""
        from custom_components.smart_lock_manager.storage import muted as muted_mod

        muted_mod._CACHE.clear()
        yield
        muted_mod._CACHE.clear()

    @pytest.fixture
    def notify_engine(self, hass: HomeAssistant) -> AlertEngine:
        """Build an engine over a zone with BOTH notify channels enabled."""
        zone = _build_zone()
        zone.settings.notify.email.enabled = True
        zone.settings.notify.mobile.enabled = True
        _register_zone(hass, zone)
        return AlertEngine(hass)

    @pytest.fixture
    def two_member_notify_engine(self, hass: HomeAssistant) -> AlertEngine:
        """Build an engine over a TWO-member zone, both notify channels on.

        Member A is ``LOCK_ENTITY``; member B is ``SECOND_LOCK_ENTITY``. Both
        resolve to the same notify-enabled zone (``has_member`` matches either),
        which lets a per-(member, type) mute on A be proven NOT to silence B.
        """
        zone = _build_zone()
        zone.member_lock_entity_ids = [LOCK_ENTITY, SECOND_LOCK_ENTITY]
        zone.settings.notify.email.enabled = True
        zone.settings.notify.mobile.enabled = True
        _register_zone(hass, zone)
        return AlertEngine(hass)

    async def test_mute_does_not_affect_different_member(
        self, hass: HomeAssistant, two_member_notify_engine: AlertEngine
    ) -> None:
        """Muting member A's low_battery leaves member B's low_battery LIVE.

        Spec requirement: a per-(member, type) mute is scoped to that ONE
        member. With two member locks in the same zone, muting A's low_battery
        must NOT silence the SAME alert_type on a DIFFERENT member B.
        """
        from custom_components.smart_lock_manager.storage import set_mute

        engine = two_member_notify_engine
        set_mute(LOCK_ENTITY, ALERT_LOW_BATTERY)
        creds = {
            "user": "u",
            "pass": "p",
            "from": "from@x",
            "to": "to@x",
            "kind_to": {"alert": []},
        }
        with patch.object(
            engine._dispatcher.email, "_creds", AsyncMock(return_value=creds)
        ):
            engine._eval_low_battery(LOCK_ENTITY, "10")
            engine._eval_low_battery(SECOND_LOCK_ENTITY, "10")
            await engine.hass.async_block_till_done()

        records = engine.serialize()
        member_a = next(
            r
            for r in records
            if r["member_entity_id"] == LOCK_ENTITY
            and r["alert_type"] == ALERT_LOW_BATTERY
        )
        member_b = next(
            r
            for r in records
            if r["member_entity_id"] == SECOND_LOCK_ENTITY
            and r["alert_type"] == ALERT_LOW_BATTERY
        )
        # A (muted): suppressed and silent.
        assert member_a["muted"] is True
        assert member_a["notify_intents"] == []
        # B (different member, same type): STILL dispatches.
        assert member_b["muted"] is False
        assert member_b["notify_intents"] != []

    async def _record_low_battery(self, engine: AlertEngine) -> dict:
        """Fire a low_battery alert and return its (post-dispatch) record."""
        creds = {
            "user": "u",
            "pass": "p",
            "from": "from@x",
            "to": "to@x",
            "kind_to": {"alert": []},
        }
        with patch.object(
            engine._dispatcher.email, "_creds", AsyncMock(return_value=creds)
        ):
            engine._eval_low_battery(LOCK_ENTITY, "10")
            await engine.hass.async_block_till_done()
        return next(
            a for a in engine.serialize() if a["alert_type"] == ALERT_LOW_BATTERY
        )

    async def test_mute_suppresses_initial_nag_recovery(
        self, hass: HomeAssistant, notify_engine: AlertEngine
    ) -> None:
        """A muted (member, type) records muted=True and dispatches nothing."""
        from custom_components.smart_lock_manager.storage import set_mute

        set_mute(LOCK_ENTITY, ALERT_LOW_BATTERY)
        record = await self._record_low_battery(notify_engine)
        assert record["muted"] is True
        assert record["notify_intents"] == []

    async def test_mute_does_not_affect_other_type(
        self, hass: HomeAssistant, notify_engine: AlertEngine
    ) -> None:
        """Muting low_battery leaves a jam alert on the SAME member un-muted.

        NOTE: the one-member fixture can't exercise a DIFFERENT member; the
        different-TYPE case is covered here, which is sufficient for the gate.
        """
        from custom_components.smart_lock_manager.storage import set_mute

        set_mute(LOCK_ENTITY, ALERT_LOW_BATTERY)
        creds = {
            "user": "u",
            "pass": "p",
            "from": "from@x",
            "to": "to@x",
            "kind_to": {"alert": []},
        }
        with patch.object(
            notify_engine._dispatcher.email, "_creds", AsyncMock(return_value=creds)
        ):
            notify_engine._eval_jam(LOCK_ENTITY, "jammed", {})
            await notify_engine.hass.async_block_till_done()
        jam = next(a for a in notify_engine.serialize() if a["alert_type"] == ALERT_JAM)
        assert jam["muted"] is False
        assert jam["notify_intents"] != []

    async def test_unmute_re_enables(
        self, hass: HomeAssistant, notify_engine: AlertEngine
    ) -> None:
        """After clear_mute, a fresh alert dispatches normally (not muted)."""
        from custom_components.smart_lock_manager.storage import clear_mute, set_mute

        set_mute(LOCK_ENTITY, ALERT_LOW_BATTERY)
        clear_mute(LOCK_ENTITY, ALERT_LOW_BATTERY)
        record = await self._record_low_battery(notify_engine)
        assert record["muted"] is False
        assert record["notify_intents"] != []

    async def test_mute_all_mutes_every_type(
        self, hass: HomeAssistant, notify_engine: AlertEngine
    ) -> None:
        """set_mute('all') mutes every type and suppresses an alert."""
        from custom_components.smart_lock_manager.storage import is_muted, set_mute

        set_mute(LOCK_ENTITY, "all")
        assert is_muted(LOCK_ENTITY, ALERT_JAM) is True
        assert is_muted(LOCK_ENTITY, ALERT_LOW_BATTERY) is True
        record = await self._record_low_battery(notify_engine)
        assert record["muted"] is True
        assert record["notify_intents"] == []

    def test_muted_appears_in_zones_payload(
        self, hass: HomeAssistant, alert_engine: AlertEngine
    ) -> None:
        """The zones DATA API payload surfaces the muted state for the panel."""
        from custom_components.smart_lock_manager.api.zones import build_zones_payload
        from custom_components.smart_lock_manager.storage import set_mute

        set_mute(LOCK_ENTITY, ALERT_LOW_BATTERY)
        payload = build_zones_payload(hass)
        assert "muted" in payload
        assert ALERT_LOW_BATTERY in payload["muted"]["muted"][LOCK_ENTITY]
