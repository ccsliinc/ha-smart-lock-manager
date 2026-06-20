"""OBSERVE-ONLY alert detection engine for Smart Lock Manager (dev-gated).

This is the FIRST push of folding the office/home pyscript alerting into the
SLM integration. It is deliberately constrained:

* **OBSERVE-ONLY detection** — it DETECTS and RECORDS alerts only. Notification
  is delegated to the DRY-RUN dispatcher (see :mod:`.notifications`), which sends
  NOTHING unless the independent ``SLM_ENABLE_REAL_NOTIFY`` flag is set AND we
  are not in dev. In dev and in PROD OBSERVE it only records "would-notify"
  intents.
* **MODE-GATED construction** (Phase 4d) — the engine is instantiated when
  ``is_dev_mock() OR engines_enabled()`` (see :func:`.gating.engines_active` and
  ``async_setup_entry`` in ``__init__.py``). With both flags off (production
  default) the class is never constructed, so it cannot run alongside the live
  pyscripts and production behavior is 100% unchanged. Under ``SLM_ENABLE_ENGINES``
  (dev-mock off) it runs in PROD OBSERVE against the REAL office entities,
  detecting + recording in parallel with the pyscripts but sending nothing.
* **Pyscripts untouched** — the detection thresholds here MIRROR the existing
  pyscripts so the two can be compared, but the pyscripts are not modified or
  imported.

This module owns the engine ORCHESTRATION (lifecycle, subscription topology,
event routing, recording + persistence, the read API and the external-alert
entrypoint). The per-member DETECTION logic lives on
:class:`~.alert_detectors.AlertDetectorsMixin` and the DEV-ONLY simulation on
:class:`~.alert_dev.AlertDevSimMixin`; both are mixed into :class:`AlertEngine`
below. See those modules for the detector + dev_simulate detail. The shared
detector constants (``SEV_*`` / ``ALERT_*`` ids / default thresholds) are
defined in :mod:`.alert_detectors` and re-exported here for backward compat.

SECURITY: alert records carry entity ids, door names, severities and
human-readable messages only — never PIN codes.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any, Callable, Dict, List

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.event import (
    async_track_state_change_event,
    async_track_time_interval,
)

from .alert_detectors import (  # noqa: F401 - re-exported for backward compat
    ALERT_JAM,
    ALERT_LOW_BATTERY,
    ALERT_OFFLINE,
    ALERT_OUTSIDE_HOURS,
    ALERT_SUSTAINED,
    DEFAULT_BUSINESS_CLOSE,
    DEFAULT_BUSINESS_OPEN,
    DEFAULT_LOW_BATTERY_THRESHOLD,
    DEFAULT_OFFLINE_DEBOUNCE_SECONDS,
    DEFAULT_SUSTAINED_TIERS_WITH_SEV,
    DEFAULT_WORKDAY_SENSOR,
    SEV_CRIT,
    SEV_WARN,
    AlertDetectorsMixin,
)
from .alert_detectors_health import AlertHealthDetectorsMixin
from .alert_dev import AlertDevSimMixin
from .alert_sweeps import AlertSweepsMixin
from .alert_topology import AlertTopologyMixin
from .const import DOMAIN
from .dev_mock import is_dev_mock
from .gating import current_engine_mode, real_notify_enabled
from .notifications import NotificationDispatcher
from .storage import get_cached_global_settings, load_alert_log, save_alert_log
from .storage.global_settings import (
    ATTR_HEALTH_SWEEP_MINUTES,
    ATTR_OUTSIDE_HOURS_SWEEP_MINUTES,
)

_LOGGER = logging.getLogger(__name__)

# hass.data key holding the single per-process AlertEngine instance.
ALERT_ENGINE_KEY = f"{DOMAIN}_alert_engine"

# Rolling cap on the persisted alert list.
MAX_ALERTS = 100


class AlertEngine(
    AlertDetectorsMixin,
    AlertHealthDetectorsMixin,
    AlertSweepsMixin,
    AlertTopologyMixin,
    AlertDevSimMixin,
):
    """Per-process OBSERVE-ONLY alert detector for all zone member locks.

    Created ONCE under ``SLM_DEV_MOCK`` and stored at
    ``hass.data[ALERT_ENGINE_KEY]``. Owns the state listeners and the tiered
    ``async_call_later`` timers for every member lock across all zones. It only
    ever records alerts — it never notifies. The detection primitives come from
    :class:`~.alert_detectors.AlertDetectorsMixin` and the dev simulation from
    :class:`~.alert_dev.AlertDevSimMixin`.

    Attributes:
        hass: the HomeAssistant instance.
        alerts: rolling list of recorded alert dicts (most-recent last).
        _alerted: per-(member, kind) episode flags so recovery fires exactly
            once per episode, mirroring the pyscripts' ``alerted_*`` vars.
    """

    def __init__(self, hass: HomeAssistant) -> None:
        """Initialize an empty, unstarted engine.

        - Inputs: hass (HomeAssistant).
        - Outputs: None.
        """
        self.hass = hass
        self.alerts: List[Dict[str, Any]] = []
        # DRY-RUN notification dispatcher. dry_run is forced ON whenever we are
        # NOT cleared for a real send: always under dev-mock, and in PROD OBSERVE
        # unless the independent SLM_ENABLE_REAL_NOTIFY flag is explicitly set.
        # In dry-run it renders + records "would notify" intents and sends
        # NOTHING. Only (observe mode AND real-notify) lets a real send through.
        # See notifications.py and gating.py.
        self._dispatcher = NotificationDispatcher(
            hass, dry_run=is_dev_mock() or not real_notify_enabled()
        )
        # Episode flags: key f"{entity_id}|{kind}" -> dict with at least
        # {"alerted": bool}. Sustained additionally tracks "max_tier".
        self._alerted: Dict[str, Dict[str, Any]] = {}
        # Active subscriptions / timers, released on stop().
        self._unsubs: List[Callable[[], None]] = []
        self._sustained_timers: Dict[str, Callable[[], None]] = {}
        self._offline_timers: Dict[str, Callable[[], None]] = {}
        self._started = False

    # -- lifecycle ----------------------------------------------------------

    async def async_start(self) -> None:
        """Hydrate persisted state and subscribe to every member lock.

        - Description: Load the persisted alert log + alerted-state, then
          register one state-change listener per zone member entity (and its
          battery companion). Idempotent — a second call is a no-op.
        - Inputs: none (reads the zone registry + storage).
        - Outputs: None.
        """
        if self._started:
            return
        # Prime the global-settings cache so _subscribe can read the sweep
        # cadences synchronously inside its callback.
        from .storage import load_global_settings, load_snooze

        await load_global_settings(self.hass)
        await load_snooze(self.hass)
        blob = await load_alert_log(self.hass)
        self.alerts = list(blob.get("alerts", []))[-MAX_ALERTS:]
        self._alerted = dict(blob.get("alerted_state", {}))

        entities = self._subscribe()
        self._started = True
        _LOGGER.info(
            "AlertEngine started: mode=%s real_notify=%s monitoring %d entit(y/ies)",
            current_engine_mode(),
            real_notify_enabled(),
            len(entities),
        )

    @callback
    def _subscribe(self) -> List[str]:
        """Register the state listener + both periodic sweeps.

        - Description: SINGLE wiring path shared by :meth:`async_start` and
          :meth:`async_refresh`. Subscribes one state-change listener over the
          current monitored-entity set, then registers TWO interval triggers at
          the GLOBALLY-CONFIGURED cadences (read synchronously from the
          global-settings cache, primed by ``load_global_settings``):

          * the still-unlocked OUTSIDE-HOURS sweep at
            ``outside_hours_sweep_minutes`` (default 15) — fast, catches doors
            left unlocked past a per-zone ``close_time`` boundary; and
          * the persistent HEALTH sweep (jam / low_battery / offline) at
            ``health_sweep_minutes`` (default 60) — slower, since those
            conditions change slowly.

          ``async_track_time_interval`` is used (not the fixed quarter-hour
          ``async_track_time_change``) so an arbitrary N minutes works. All
          handles go into ``_unsubs`` so the existing
          :meth:`_teardown_subscriptions` releases them — no separate teardown.
          The per-episode alerted-flag dedup makes the repeated sweeps spam-safe.
          On a global-settings change, :meth:`async_refresh` tears down and
          re-subscribes here, picking up the new cadences with NO restart.
        - Inputs: none (reads the zone registry + the global-settings cache).
        - Outputs: the monitored-entity list (for logging).
        """
        entities = self._monitored_entities()
        if entities:
            self._unsubs.append(
                async_track_state_change_event(
                    self.hass, entities, self._handle_state_event
                )
            )
        settings = get_cached_global_settings()
        outside_minutes = settings[ATTR_OUTSIDE_HOURS_SWEEP_MINUTES]
        health_minutes = settings[ATTR_HEALTH_SWEEP_MINUTES]
        # Outside-hours boundary sweep at the configurable fast cadence.
        self._unsubs.append(
            async_track_time_interval(
                self.hass,
                self._run_outside_hours_sweep,
                timedelta(minutes=outside_minutes),
            )
        )
        # Persistent health sweep (jam / low_battery / offline) at the slower
        # configurable cadence.
        self._unsubs.append(
            async_track_time_interval(
                self.hass,
                self._run_health_sweep,
                timedelta(minutes=health_minutes),
            )
        )
        return entities

    @callback
    def async_stop(self) -> None:
        """Release all listeners and pending timers.

        - Inputs: none.
        - Outputs: None.
        """
        self._teardown_subscriptions()
        self._started = False
        _LOGGER.info("AlertEngine (observe-only) stopped")

    @callback
    def _teardown_subscriptions(self) -> None:
        """Cancel every active listener and pending timer (idempotent).

        - Description: Releases the state-change listener and all sustained /
          offline ``async_call_later`` handles. Leaves ``_started`` and the
          recorded ``alerts`` / ``_alerted`` episode state untouched so the
          engine can be re-subscribed without losing history. Safe to call
          repeatedly — the handle collections are cleared as they are released.
        - Inputs: none.
        - Outputs: None.
        """
        for unsub in self._unsubs:
            unsub()
        self._unsubs.clear()
        for cancel in list(self._sustained_timers.values()):
            cancel()
        self._sustained_timers.clear()
        for cancel in list(self._offline_timers.values()):
            cancel()
        self._offline_timers.clear()

    @callback
    def async_refresh(self) -> None:
        """Re-read the zone registry and rebuild member subscriptions live.

        - Description: Called when a zone's settings change so a newly enabled
          detector (or a changed member set) takes effect WITHOUT an HA restart.
          Tears down the existing state listener + pending timers, then
          re-subscribes over the current monitored-entity set. Idempotent: old
          listeners/timers are cancelled first so no duplicates accumulate
          across repeated settings edits. A no-op if the engine never started.
        - Inputs: none (reads the zone registry).
        - Outputs: None.
        """
        if not self._started:
            return
        self._teardown_subscriptions()
        entities = self._subscribe()
        _LOGGER.info(
            "AlertEngine refreshed: now monitoring %d entit(y/ies)", len(entities)
        )

    # -- recording ----------------------------------------------------------

    def _record(
        self,
        entity_id: str,
        alert_type: str,
        severity: str,
        message: str,
        is_recovery: bool = False,
        origin: str = "state_change",
    ) -> None:
        """Append one structured alert record and persist the log.

        - Description: Builds the canonical record dict, appends it to the
          rolling capped list, and schedules an async persist. Recording is the
          ONLY action — no notification is ever sent.
        - Inputs: entity_id (str), alert_type (str), severity (str WARN/CRIT),
          message (str human-readable), is_recovery (bool), origin (str — one of
          ``"state_change"`` for rising/falling-edge Alerts/Recoveries emitted by
          the ``_eval_*`` detectors, or ``"timer"`` for periodic-sweep Nags;
          ``state_change`` always dispatches, ``timer`` is snoozable + throttled).
        - Outputs: None.
        """
        zone_id, zone_name, door_name = self._zone_for(entity_id)
        state_obj = self.hass.states.get(entity_id)
        friendly_name = (
            state_obj.attributes.get("friendly_name") if state_obj else None
        ) or door_name
        last_changed = (
            state_obj.last_changed.isoformat()
            if state_obj is not None and state_obj.last_changed is not None
            else "unknown"
        )
        record: Dict[str, Any] = {
            "timestamp": datetime.now().isoformat(),
            "zone_id": zone_id,
            "zone_name": zone_name,
            "member_entity_id": entity_id,
            "door_name": door_name,
            "friendly_name": friendly_name,
            "last_changed": last_changed,
            "alert_type": alert_type,
            "severity": severity,
            "message": message,
            "is_recovery": is_recovery,
            "origin": origin,
            "snoozed": False,
            # Filled asynchronously by the DRY-RUN dispatcher (see _notify). The
            # engine still records ZERO real sends; these are "would-notify"
            # intents surfaced to the API/panel for parity checking.
            "notify_intents": [],
        }
        self.alerts.append(record)
        if len(self.alerts) > MAX_ALERTS:
            self.alerts = self.alerts[-MAX_ALERTS:]
        _LOGGER.info(
            "AlertEngine RECORDED (observe-only, no notification): "
            "%s %s %s on %s (%s)",
            "RECOVERY" if is_recovery else "ALERT",
            severity,
            alert_type,
            door_name,
            zone_name,
        )
        # Route to the DRY-RUN dispatcher for the owning zone (records intents,
        # sends nothing), then persist. The dispatch is async and mutates the
        # record in place before the final persist.
        self.hass.async_create_task(self._notify(entity_id, record))

    # -- nag throttle (shared by the detector cores) ------------------------

    def _should_nag(self, flag: Dict[str, Any], now: float) -> bool:
        """Return True if an ongoing episode is due for a repeat timer-nag.

        - Description: Throttles timer-origin re-alerts of an ALREADY-alerted
          episode to at most one per ``nag_interval_minutes`` (global setting).
          ``flag['last_nag']`` is the epoch of the last nag/seed; ``None`` means
          never stamped (fire immediately). The caller stamps ``last_nag`` to
          ``now`` whenever this returns True.
        - Inputs: flag (the per-episode alerted-state dict), now (float epoch).
        - Outputs: bool — True when the nag interval has elapsed.
        """
        nag_interval = get_cached_global_settings().get("nag_interval_minutes", 60) * 60
        last = flag.get("last_nag")
        return last is None or (now - last) >= nag_interval

    @staticmethod
    def _seed_nag(flag: Dict[str, Any], now: float) -> None:
        """Stamp ``last_nag`` so the first/next nag waits a full interval.

        - Description: Called by a core when it records a NEW (rising-edge or
          sweep-discovered) alert — seeding ``last_nag`` to ``now`` prevents the
          immediately-following sweep from firing a back-to-back Nag (the
          Alert+immediate-Nag double-hit). The first repeat-Nag then lands one
          full ``nag_interval`` after the initial alert.
        - Inputs: flag (the per-episode alerted-state dict), now (float epoch).
        - Outputs: None.
        """
        flag["last_nag"] = now

    async def _notify(self, entity_id: str, record: Dict[str, Any]) -> None:
        """Run the DRY-RUN dispatcher for a record, then persist.

        - Description: Resolves the owning zone's ``settings.notify`` config and,
          if email and/or mobile is enabled, asks the dispatcher to render the
          "would-notify" intents (no real send in dev). The intents are written
          back onto the record so the API/panel can show them. Always persists,
          even when no channel is enabled (intents stays empty).
        - Inputs: entity_id (str member lock id), record (dict alert record).
        - Outputs: None.
        """
        from .storage import snooze_active

        snoozed = snooze_active(record.get("zone_id"))
        if snoozed:
            record["snoozed"] = True
            if record.get("origin") == "timer":
                _LOGGER.info(
                    "AlertEngine: NAG suppressed (snoozed timer-origin): %s on %s",
                    record.get("alert_type"),
                    entity_id,
                )
                await self._persist()
                return
            # state_change Alert/Recovery: snooze NEVER suppresses a state edge.
            record["snooze_bypassed"] = True
            _LOGGER.info(
                "AlertEngine: snoozed but state_change edge -> dispatching: %s on %s",
                record.get("alert_type"),
                entity_id,
            )

        zone = self._zone_settings_for(entity_id)
        if zone is not None:
            notify = zone.settings.notify
            if notify.email.enabled or notify.mobile.enabled:
                try:
                    record["notify_intents"] = await self._dispatcher.dispatch(
                        record, notify
                    )
                except Exception as exc:  # noqa: BLE001 - never crash recording
                    _LOGGER.error("AlertEngine: dispatch failed: %s", exc)
        await self._persist()

    async def _persist(self) -> None:
        """Persist the current alert log + alerted-state to storage.

        - Inputs: none.
        - Outputs: None.
        """
        await save_alert_log(
            self.hass,
            {"alerts": self.alerts, "alerted_state": self._alerted},
        )

    # -- read API -----------------------------------------------------------

    def serialize(self) -> List[Dict[str, Any]]:
        """Return the recorded alerts most-recent-first (PIN-free by design).

        - Inputs: none.
        - Outputs: list of alert record dicts.
        """
        return list(reversed(self.alerts))

    # -- external alert routing (auto-lock failures) ------------------------

    def record_external(
        self,
        entity_id: str,
        alert_type: str,
        severity: str,
        message: str,
    ) -> None:
        """Record an alert raised by another engine (e.g. auto-lock failure).

        - Description: Public entrypoint so the :mod:`..auto_lock` engine can
          surface a final lock-failure into the SAME recorded-alert + DRY-RUN
          notify stream the detectors use. It reuses :meth:`_record`, so the
          alert lands in Dev Alerts and produces "would-notify" intents per the
          owning zone's notify config — exactly like a detector alert. Recording
          is the only action; nothing is ever really sent in dev.
        - Inputs: entity_id (str member lock id), alert_type (str id, e.g.
          ``auto_lock_failed``), severity (str WARN/CRIT), message (str).
        - Outputs: None.
        """
        self._record(entity_id, alert_type, severity, message)
