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
from custom_components.smart_lock_manager.notifications import build_alert_subject
from custom_components.smart_lock_manager.zone_runtime import ZONE_REGISTRY_KEY

LOCK_ENTITY = "lock.front_test"
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
        # Second start is a no-op.
        await alert_engine.async_start()
        assert len(alert_engine._unsubs) == 1
        alert_engine.async_stop()
        assert alert_engine._started is False
        assert alert_engine._unsubs == []

    async def test_refresh_is_idempotent_no_duplicate_listeners(
        self, hass: HomeAssistant, alert_engine: AlertEngine
    ) -> None:
        """Repeated refresh leaves exactly one state listener (no dupes)."""
        with patch(
            "custom_components.smart_lock_manager.alert_engine.load_alert_log",
            AsyncMock(return_value={"alerts": [], "alerted_state": {}}),
        ):
            await alert_engine.async_start()
        for _ in range(3):
            alert_engine.async_refresh()
        # Exactly one state listener regardless of how many refreshes ran.
        assert len(alert_engine._unsubs) == 1

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


class TestAlertSubjects:
    """Pyscript-parity email subject wording (keyed on member entity id)."""

    def test_sustained_subject_matches_pyscript(self) -> None:
        """sustained_unlock -> 'office HA - {entity} unlocked >{n}s'."""
        alert = {
            "alert_type": ALERT_SUSTAINED,
            "member_entity_id": "lock.front_north",
            "door_name": "Front North",
            "message": "Unlocked >15s without re-lock",
            "is_recovery": False,
        }
        assert (
            build_alert_subject(alert) == "office HA - lock.front_north unlocked >15s"
        )

    def test_sustained_recovery_subject(self) -> None:
        """sustained_unlock recovery -> 'office HA - {entity} locked again'."""
        alert = {
            "alert_type": ALERT_SUSTAINED,
            "member_entity_id": "lock.front_middle_door_lock",
            "is_recovery": True,
            "message": "Re-locked after sustained-unlock alert",
        }
        assert (
            build_alert_subject(alert)
            == "office HA - lock.front_middle_door_lock locked again"
        )

    def test_outside_hours_subject_matches_pyscript(self) -> None:
        """outside_hours -> 'office HA - door {entity} unlocked outside ...'."""
        alert = {
            "alert_type": ALERT_OUTSIDE_HOURS,
            "member_entity_id": "lock.rear",
            "message": "Unlocked outside business hours",
            "is_recovery": False,
        }
        assert build_alert_subject(alert) == (
            "office HA - door lock.rear unlocked outside business hours"
        )

    def test_outside_hours_recovery_subject(self) -> None:
        """outside_hours recovery -> 'office HA - {entity} locked again'."""
        alert = {
            "alert_type": ALERT_OUTSIDE_HOURS,
            "member_entity_id": "lock.rear",
            "is_recovery": True,
        }
        assert build_alert_subject(alert) == "office HA - lock.rear locked again"

    def test_auto_lock_failed_subject_uses_emdash_and_name(self) -> None:
        """auto_lock_failed -> 'office HA — {name} FAILED to auto-lock at COB'."""
        alert = {
            "alert_type": "auto_lock_failed",
            "member_entity_id": "lock.front_north",
            "door_name": "Front North",
            "message": "scheduled auto-lock FAILED after 3 attempt(s)",
        }
        # EM-DASH (—), and the friendly NAME, exactly like lock_doors.py.
        assert build_alert_subject(alert) == (
            "office HA — Front North FAILED to auto-lock at COB"
        )

    def test_slm_only_subjects(self) -> None:
        """Native-only types (jam/low_battery/offline) use the house style."""
        jam = {
            "alert_type": ALERT_JAM,
            "member_entity_id": "lock.bathroom",
            "is_recovery": False,
        }
        assert build_alert_subject(jam) == "office HA - lock.bathroom jammed"

        battery = {
            "alert_type": ALERT_LOW_BATTERY,
            "member_entity_id": "lock.suite_105",
            "message": "Battery low (8%)",
            "is_recovery": False,
        }
        assert build_alert_subject(battery) == (
            "office HA - lock.suite_105 battery low (8%)"
        )

        offline = {
            "alert_type": ALERT_OFFLINE,
            "member_entity_id": "lock.suite_106",
            "is_recovery": False,
        }
        assert build_alert_subject(offline) == "office HA - lock.suite_106 offline"

    def test_unknown_type_falls_back_to_house_style(self) -> None:
        """An unknown alert_type still yields a non-empty 'office HA - ...'."""
        alert = {
            "alert_type": "brand_new_type",
            "member_entity_id": "lock.x",
            "message": "something happened",
        }
        assert build_alert_subject(alert) == ("office HA - lock.x something happened")


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
            # The new pyscript-style subject (post-wrap) is present.
            assert "office HA -" in email["subject"], alert_type


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
