"""OBSERVE-ONLY alert detection engine for Smart Lock Manager (dev-gated).

This is the FIRST push of folding the office/home pyscript alerting into the
SLM integration. It is deliberately constrained:

* **OBSERVE-ONLY** — it DETECTS and RECORDS alerts only. It sends ZERO
  notifications: no email, no ``notify`` service, no ``persistent_notification``.
  There is intentionally no import of, or call into, any notification path.
* **DEV-GATED** — the engine is only ever instantiated when ``is_dev_mock()``
  is true (see ``async_setup_entry`` in ``__init__.py``). In production the
  class is never constructed, so it cannot run alongside the live pyscripts and
  production behavior is 100% unchanged.
* **Pyscripts untouched** — the detection thresholds here MIRROR the existing
  pyscripts so the two can be compared, but the pyscripts are not modified or
  imported.

Detectors (per zone member lock), built on HA primitives
(``async_track_state_change_event`` + ``async_call_later``), NOT pyscript idioms:

1. Outside-hours unlock  — member unlocked outside business hours
   (default 08:30-17:30, workday). Severity CRIT. Recovery on re-lock.
   Mirrors ``unlocked_outside_business.py``.
2. Sustained unlock      — member stays unlocked 15s (WARN) / 30s (CRIT) /
   45s (CRIT). Recovery on re-lock. Mirrors ``front_middle_lock.py``.
3. Jam / lock-failure    — member jammed (jam binary_sensor on, or lock state
   ``jammed``). Severity CRIT. Mirrors ``lock_doors.py`` jam context.
4. Low-battery / offline — battery below threshold (default 20%) -> WARN;
   member unavailable/unknown -> WARN.

Each detected condition is recorded as a structured entry and appended to a
rolling, capped list persisted via :mod:`storage.alert_storage`.

SECURITY: alert records carry entity ids, door names, severities and
human-readable messages only — never PIN codes.
"""

from __future__ import annotations

import logging
from datetime import datetime, time
from typing import Any, Callable, Dict, List, Optional

from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers.event import (
    async_call_later,
    async_track_state_change_event,
)

from .const import DOMAIN
from .models.zone import Zone
from .models.zone_settings import (
    DEFAULT_CLOSE_TIME,
)
from .models.zone_settings import (
    DEFAULT_LOW_BATTERY_THRESHOLD as SETTINGS_LOW_BATTERY_THRESHOLD,
)
from .models.zone_settings import (
    DEFAULT_OPEN_TIME,
    DEFAULT_SUSTAINED_TIERS,
    DEFAULT_WEEKDAYS,
    DEFAULT_WORKDAY_ENTITY,
)
from .storage import load_alert_log, save_alert_log
from .zone_runtime import get_zone_for_lock, get_zone_registry

_LOGGER = logging.getLogger(__name__)

# hass.data key holding the single per-process AlertEngine instance.
ALERT_ENGINE_KEY = f"{DOMAIN}_alert_engine"

# --- Dev defaults (mirror the pyscripts) -----------------------------------
# These DEV DEFAULTS are now SOURCED from models.zone_settings so the zone
# config schema and the observe-only engine share ONE source of truth — turning
# a zone's setting on reproduces exactly the engine's fallback behaviour.
#
# RECONCILIATION (default-OFF config vs. the dev observe-only engine):
#   The Phase-4a zone settings default every alert toggle to enabled=False so a
#   migrated zone changes nothing in PRODUCTION. But this engine only ever runs
#   under SLM_DEV_MOCK and is OBSERVE-ONLY (records, never notifies). To keep
#   dev continuity, the engine treats an UNCONFIGURED / disabled toggle as
#   "observe anyway using the mirror defaults" (see ``_detector_enabled`` ->
#   ``_OBSERVE_ALL_IN_DEV``). It still READS any per-zone overrides the user set
#   (business-hours window/gate, sustained tiers, low-battery threshold) so
#   editing config is reflected live; it just does not go silent merely because
#   ``enabled`` defaults to False. A future non-dev engine (Phase 4d) will honor
#   the enable flags strictly before sending real notifications.

# When True (always, in this dev-gated engine) every detector observes
# regardless of the per-zone enable flag, using config thresholds when present
# and the mirror defaults otherwise. Flip to False to make the engine honor the
# per-zone enable flags strictly (intended for the future production engine).
_OBSERVE_ALL_IN_DEV = True

# Severity vocabulary (mirrors the pyscripts' send_alert vocabulary).
SEV_WARN = "WARN"
SEV_CRIT = "CRIT"


def _parse_hhmm(value: str, fallback: time) -> time:
    """Parse an ``HH:MM`` string into a ``time``, falling back on error.

    - Inputs: value (str like "08:30"), fallback (time used on parse failure).
    - Outputs: datetime.time.
    """
    try:
        hour, minute = (int(part) for part in value.split(":", 1))
        return time(hour, minute)
    except (TypeError, ValueError, AttributeError):
        return fallback


def _tiers_with_severity(seconds_list: List[int]) -> tuple:
    """Map a list of tier seconds to ``(seconds, severity)`` pairs.

    - Description: First tier is WARN, all later tiers CRIT — the legacy
      front_middle_lock.py escalation shape.
    - Inputs: seconds_list (list[int]).
    - Outputs: tuple of (int seconds, str severity).
    """
    return tuple(
        (seconds, SEV_WARN if index == 0 else SEV_CRIT)
        for index, seconds in enumerate(seconds_list)
    )


# Outside-hours window + gate (mirror unlocked_outside_business.py), sourced
# from the shared zone-settings defaults.
DEFAULT_BUSINESS_OPEN = _parse_hhmm(DEFAULT_OPEN_TIME, time(8, 30))
DEFAULT_BUSINESS_CLOSE = _parse_hhmm(DEFAULT_CLOSE_TIME, time(17, 30))
DEFAULT_WORKDAY_SENSOR = DEFAULT_WORKDAY_ENTITY

# Sustained-unlock tiers as (seconds, severity), derived from the shared tier
# seconds (mirror front_middle_lock.py: first tier WARN, later tiers CRIT).
DEFAULT_SUSTAINED_TIERS_WITH_SEV = _tiers_with_severity(DEFAULT_SUSTAINED_TIERS)

# Low-battery threshold (percent) and offline-debounce window (seconds).
DEFAULT_LOW_BATTERY_THRESHOLD = SETTINGS_LOW_BATTERY_THRESHOLD
DEFAULT_OFFLINE_DEBOUNCE_SECONDS = 60

# Alert-type identifiers used in records + the alerted-state map.
ALERT_OUTSIDE_HOURS = "outside_hours"
ALERT_SUSTAINED = "sustained_unlock"
ALERT_JAM = "jam"
ALERT_LOW_BATTERY = "low_battery"
ALERT_OFFLINE = "offline"

# Rolling cap on the persisted alert list.
MAX_ALERTS = 100

# Lock states the engine treats as "offline".
_OFFLINE_STATES = {"unavailable", "unknown"}


class AlertEngine:
    """Per-process OBSERVE-ONLY alert detector for all zone member locks.

    Created ONCE under ``SLM_DEV_MOCK`` and stored at
    ``hass.data[ALERT_ENGINE_KEY]``. Owns the state listeners and the tiered
    ``async_call_later`` timers for every member lock across all zones. It only
    ever records alerts — it never notifies.

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
        blob = await load_alert_log(self.hass)
        self.alerts = list(blob.get("alerts", []))[-MAX_ALERTS:]
        self._alerted = dict(blob.get("alerted_state", {}))

        entities = self._monitored_entities()
        if entities:
            self._unsubs.append(
                async_track_state_change_event(
                    self.hass, entities, self._handle_state_event
                )
            )
        self._started = True
        _LOGGER.info(
            "AlertEngine (observe-only) started: monitoring %d entit(y/ies)",
            len(entities),
        )

    @callback
    def async_stop(self) -> None:
        """Release all listeners and pending timers.

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
        self._started = False
        _LOGGER.info("AlertEngine (observe-only) stopped")

    # -- topology -----------------------------------------------------------

    def _monitored_entities(self) -> List[str]:
        """Return every entity the engine should watch (locks + batteries).

        - Description: All zone member lock entity ids plus their companion
          ``sensor.<object_id>_battery`` entities (best-effort) so the
          low-battery detector gets state events.
        - Inputs: none (reads the zone registry).
        - Outputs: de-duplicated list of entity_id strings.
        """
        entities: List[str] = []
        for zone in get_zone_registry(self.hass).values():
            for entity_id in zone.member_lock_entity_ids:
                entities.append(entity_id)
                entities.append(self._battery_entity_for(entity_id))
        # De-dup while preserving order.
        seen: set = set()
        result: List[str] = []
        for ent in entities:
            if ent not in seen:
                seen.add(ent)
                result.append(ent)
        return result

    @staticmethod
    def _battery_entity_for(lock_entity_id: str) -> str:
        """Return the companion battery sensor id for a lock entity.

        - Inputs: lock_entity_id (str), e.g. ``lock.front_north``.
        - Outputs: str, e.g. ``sensor.front_north_battery``.
        """
        object_id = lock_entity_id.split(".", 1)[-1]
        return f"sensor.{object_id}_battery"

    # -- zone config resolution --------------------------------------------

    def _zone_settings_for(self, entity_id: str) -> Optional[Zone]:
        """Return the owning Zone for a member entity, or None if unhomed.

        - Inputs: entity_id (str lock entity id).
        - Outputs: the owning Zone (carrying ``settings``), or None.
        """
        return get_zone_for_lock(self.hass, entity_id)

    def _detector_enabled(self, entity_id: str, configured: bool) -> bool:
        """Decide whether a detector should observe for this member.

        - Description: In this dev-gated observe-only engine, observation is
          kept on for ALL types (``_OBSERVE_ALL_IN_DEV``) so dev continuity is
          preserved even though per-zone toggles default to False. When that
          flag is False (the future production engine) the per-zone ``enabled``
          flag is honored strictly.
        - Inputs: entity_id (str), configured (bool — the zone's enable flag).
        - Outputs: bool.
        """
        if _OBSERVE_ALL_IN_DEV:
            return True
        return configured

    def _zone_for(self, entity_id: str) -> tuple[Optional[str], Optional[str], str]:
        """Return (zone_id, zone_name, door_name) for a member entity.

        - Inputs: entity_id (str lock entity id).
        - Outputs: tuple(zone_id, zone_name, door_name); door_name falls back
          to the live HA friendly_name then the entity id.
        """
        for zone in get_zone_registry(self.hass).values():
            if zone.has_member(entity_id):
                door = self._friendly_name(entity_id)
                return zone.zone_id, zone.name, door
        return None, None, self._friendly_name(entity_id)

    def _friendly_name(self, entity_id: str) -> str:
        """Return the live HA friendly name for an entity, or the id.

        - Inputs: entity_id (str).
        - Outputs: str display name (never empty).
        """
        state = self.hass.states.get(entity_id)
        if state is not None:
            name = state.attributes.get("friendly_name")
            if name:
                return str(name)
        return entity_id

    # -- event routing ------------------------------------------------------

    @callback
    def _handle_state_event(self, event: Event) -> None:
        """Route a state-change event to the relevant detectors.

        - Description: Battery-sensor events drive the low-battery detector;
          lock entity events drive the unlock-based detectors (outside-hours,
          sustained), jam, and offline detectors.
        - Inputs: event (HA state_changed Event).
        - Outputs: None (records alerts as a side effect).
        """
        entity_id = event.data.get("entity_id", "")
        new_state = event.data.get("new_state")
        if new_state is None:
            return

        # Battery sensor path.
        if entity_id.startswith("sensor.") and entity_id.endswith("_battery"):
            lock_entity = self._lock_for_battery(entity_id)
            if lock_entity:
                self._eval_low_battery(lock_entity, new_state.state)
            return

        # Lock entity path.
        if entity_id.startswith("lock."):
            value = (new_state.state or "unknown").lower()
            self._eval_offline(entity_id, value)
            self._eval_jam(entity_id, value, new_state.attributes)
            self._eval_outside_hours(entity_id, value)
            self._eval_sustained(entity_id, value)

    def _lock_for_battery(self, battery_entity_id: str) -> Optional[str]:
        """Resolve a battery sensor back to its monitored lock entity.

        - Inputs: battery_entity_id (str).
        - Outputs: the lock entity id if it is a monitored member, else None.
        """
        object_id = battery_entity_id[len("sensor.") : -len("_battery")]
        candidate = f"lock.{object_id}"
        for zone in get_zone_registry(self.hass).values():
            if zone.has_member(candidate):
                return candidate
        return None

    # -- alerted-state helpers (replace pyscript.alerted_*) -----------------

    def _flag(self, entity_id: str, kind: str) -> Dict[str, Any]:
        """Return the mutable alerted-state record for (entity, kind).

        - Inputs: entity_id (str), kind (str alert-type id).
        - Outputs: dict (created on first access) with at least ``alerted``.
        """
        key = f"{entity_id}|{kind}"
        return self._alerted.setdefault(key, {"alerted": False})

    # -- detectors ----------------------------------------------------------

    def _eval_outside_hours(self, entity_id: str, value: str) -> None:
        """Outside-business-hours unlock detector (mirrors the pyscript).

        Fires CRIT when a member is ``unlocked`` outside the business window;
        emits a RECOVERY when a previously-alerted member returns to ``locked``.

        - Inputs: entity_id (str), value (str normalized lock state).
        - Outputs: None (records alerts).
        """
        zone = self._zone_settings_for(entity_id)
        oh = zone.settings.alerts.outside_hours if zone is not None else None
        severity = oh.severity if oh is not None else SEV_CRIT
        if not self._detector_enabled(
            entity_id, bool(oh.enabled) if oh is not None else False
        ):
            return

        flag = self._flag(entity_id, ALERT_OUTSIDE_HOURS)

        # Recovery: locked again after a prior alert (any time of day).
        if value == "locked" and flag.get("alerted"):
            self._record(
                entity_id,
                ALERT_OUTSIDE_HOURS,
                severity,
                "Locked again after outside-hours unlock alert",
                is_recovery=True,
            )
            flag["alerted"] = False
            return

        if value != "unlocked":
            return
        if self._in_business_hours(zone):
            return
        if flag.get("alerted"):
            return
        self._record(
            entity_id,
            ALERT_OUTSIDE_HOURS,
            severity,
            "Unlocked outside business hours",
        )
        flag["alerted"] = True

    def _in_business_hours(self, zone: Optional[Zone] = None) -> bool:
        """Return True if NOW is inside the zone's business window.

        - Description: Mirrors ``unlocked_outside_business.py`` — inside the
          open/close window AND a workday. Reads the owning zone's
          ``business_hours`` config when present (open/close times, the
          workday-sensor toggle + entity, or an explicit day-of-week list),
          FALLING BACK to the mirror-the-pyscript defaults (08:30-17:30,
          ``binary_sensor.workday_sensor``, Mon-Fri) for any absent field.
        - Inputs: zone (Zone or None) — the owning zone whose config is read.
        - Outputs: bool.
        """
        now = datetime.now()
        bh = zone.settings.business_hours if zone is not None else None

        open_t = (
            _parse_hhmm(bh.open_time, DEFAULT_BUSINESS_OPEN)
            if bh is not None
            else DEFAULT_BUSINESS_OPEN
        )
        close_t = (
            _parse_hhmm(bh.close_time, DEFAULT_BUSINESS_CLOSE)
            if bh is not None
            else DEFAULT_BUSINESS_CLOSE
        )
        in_window = open_t < now.time() < close_t

        # Day gate: workday sensor when the zone opts into it (or when no zone
        # config exists, preserving the legacy default), else day-of-week list.
        use_sensor = bh.use_workday_sensor if bh is not None else True
        if bh is not None and not use_sensor:
            days = bh.days or DEFAULT_WEEKDAYS
            is_workday = now.weekday() in days
        else:
            sensor_id = bh.workday_entity if bh is not None else DEFAULT_WORKDAY_SENSOR
            workday_state = self.hass.states.get(sensor_id)
            if workday_state is not None:
                is_workday = (workday_state.state or "").lower() == "on"
            else:
                is_workday = now.weekday() < 5  # Mon-Fri fallback
        return in_window and is_workday

    def _eval_sustained(self, entity_id: str, value: str) -> None:
        """Sustained-unlock tiered detector (mirrors the pyscript).

        On ``unlocked`` it schedules the 15/30/45s tier chain; on ``locked`` it
        cancels pending tiers and, if any tier fired, records a RECOVERY.

        - Inputs: entity_id (str), value (str normalized lock state).
        - Outputs: None (records alerts; schedules timers).
        """
        su = None
        zone = self._zone_settings_for(entity_id)
        if zone is not None:
            su = zone.settings.alerts.sustained_unlock
        if not self._detector_enabled(
            entity_id, bool(su.enabled) if su is not None else False
        ):
            return

        flag = self._flag(entity_id, ALERT_SUSTAINED)

        if value == "unlocked":
            self._cancel_sustained(entity_id)
            flag["max_tier"] = 0
            self._schedule_tier(entity_id, 0)
            return

        if value == "locked":
            self._cancel_sustained(entity_id)
            if flag.get("alerted"):
                self._record(
                    entity_id,
                    ALERT_SUSTAINED,
                    SEV_CRIT,
                    "Re-locked after sustained-unlock alert",
                    is_recovery=True,
                )
                flag["alerted"] = False
                flag["max_tier"] = 0

    def _sustained_tiers_for(self, entity_id: str) -> tuple:
        """Resolve the (seconds, severity) tier chain for a member.

        - Description: Reads the owning zone's
          ``alerts.sustained_unlock.tiers`` (seconds list) and maps it to
          (seconds, severity) pairs, FALLING BACK to the mirror default tiers
          (15s WARN / 30s CRIT / 45s CRIT) when no zone config is present.
        - Inputs: entity_id (str).
        - Outputs: tuple of (int seconds, str severity).
        """
        zone = self._zone_settings_for(entity_id)
        if zone is not None and zone.settings.alerts.sustained_unlock.tiers:
            return _tiers_with_severity(zone.settings.alerts.sustained_unlock.tiers)
        return DEFAULT_SUSTAINED_TIERS_WITH_SEV

    def _schedule_tier(self, entity_id: str, tier_index: int) -> None:
        """Arm the timer for one sustained-unlock tier.

        - Inputs: entity_id (str), tier_index (int into the resolved tier list).
        - Outputs: None.
        """
        tiers = self._sustained_tiers_for(entity_id)
        if tier_index >= len(tiers):
            return
        seconds, severity = tiers[tier_index]
        # Delay between tiers is the DELTA from the previous tier's elapsed
        # time so the cumulative elapsed matches the configured tier seconds.
        prev = tiers[tier_index - 1][0] if tier_index else 0
        delay = seconds - prev

        @callback
        def _fire(_now: datetime) -> None:
            # Still scheduled? (cancellation removes the handle).
            if entity_id not in self._sustained_timers:
                return
            state = self.hass.states.get(entity_id)
            if state is None or (state.state or "").lower() != "unlocked":
                self._cancel_sustained(entity_id)
                return
            flag = self._flag(entity_id, ALERT_SUSTAINED)
            flag["alerted"] = True
            flag["max_tier"] = seconds
            self._record(
                entity_id,
                ALERT_SUSTAINED,
                severity,
                f"Unlocked >{seconds}s without re-lock",
            )
            self._sustained_timers.pop(entity_id, None)
            self._schedule_tier(entity_id, tier_index + 1)

        self._sustained_timers[entity_id] = async_call_later(self.hass, delay, _fire)

    def _cancel_sustained(self, entity_id: str) -> None:
        """Cancel any pending sustained-unlock timer for a member.

        - Inputs: entity_id (str).
        - Outputs: None.
        """
        cancel = self._sustained_timers.pop(entity_id, None)
        if cancel:
            cancel()

    def _eval_jam(self, entity_id: str, value: str, attributes: Dict[str, Any]) -> None:
        """Jam / lock-failure detector (mirrors lock_doors.py jam context).

        Fires CRIT when the lock state is ``jammed`` OR a companion
        ``binary_sensor.<object_id>_jammed`` reads ``on``. Recovery when the
        member next reports ``locked``.

        - Inputs: entity_id (str), value (str normalized lock state),
          attributes (lock entity attributes; unused but kept for parity).
        - Outputs: None (records alerts).
        """
        zone = self._zone_settings_for(entity_id)
        jam_cfg = zone.settings.alerts.jam if zone is not None else None
        severity = jam_cfg.severity if jam_cfg is not None else SEV_CRIT
        if not self._detector_enabled(
            entity_id, bool(jam_cfg.enabled) if jam_cfg is not None else False
        ):
            return

        flag = self._flag(entity_id, ALERT_JAM)
        jammed = value == "jammed" or self._jam_sensor_on(entity_id)

        if jammed and not flag.get("alerted"):
            self._record(entity_id, ALERT_JAM, severity, "Lock jammed")
            flag["alerted"] = True
            return
        if not jammed and flag.get("alerted") and value == "locked":
            self._record(
                entity_id,
                ALERT_JAM,
                severity,
                "Recovered from jam (locked)",
                is_recovery=True,
            )
            flag["alerted"] = False

    def _jam_sensor_on(self, entity_id: str) -> bool:
        """Return True if the companion jam binary_sensor reads ``on``.

        - Inputs: entity_id (str lock entity id).
        - Outputs: bool.
        """
        object_id = entity_id.split(".", 1)[-1]
        jam = self.hass.states.get(f"binary_sensor.{object_id}_jammed")
        return jam is not None and (jam.state or "").lower() == "on"

    def _eval_low_battery(self, lock_entity_id: str, raw_value: str) -> None:
        """Low-battery detector — WARN when below threshold, recovery above.

        - Inputs: lock_entity_id (str), raw_value (str battery percent).
        - Outputs: None (records alerts).
        """
        try:
            percent = int(float(raw_value))
        except (TypeError, ValueError):
            return

        zone = self._zone_settings_for(lock_entity_id)
        lb = zone.settings.alerts.low_battery if zone is not None else None
        threshold = lb.threshold if lb is not None else DEFAULT_LOW_BATTERY_THRESHOLD
        if not self._detector_enabled(
            lock_entity_id, bool(lb.enabled) if lb is not None else False
        ):
            return

        flag = self._flag(lock_entity_id, ALERT_LOW_BATTERY)

        if percent < threshold and not flag.get("alerted"):
            self._record(
                lock_entity_id,
                ALERT_LOW_BATTERY,
                SEV_WARN,
                f"Battery low ({percent}%)",
            )
            flag["alerted"] = True
        # Small hysteresis on recovery to avoid flapping at the threshold.
        elif percent >= threshold + 5 and flag.get("alerted"):
            self._record(
                lock_entity_id,
                ALERT_LOW_BATTERY,
                SEV_WARN,
                f"Battery recovered ({percent}%)",
                is_recovery=True,
            )
            flag["alerted"] = False

    def _eval_offline(self, entity_id: str, value: str) -> None:
        """Offline detector — WARN when unavailable/unknown (debounced).

        - Description: A transient Z-Wave blip should not alert, so an offline
          alert is armed via ``async_call_later`` and only fires if the member
          is STILL offline after the debounce window. Recovery fires when the
          member returns to a known state.
        - Inputs: entity_id (str), value (str normalized lock state).
        - Outputs: None (records alerts; schedules a debounce timer).
        """
        zone = self._zone_settings_for(entity_id)
        off_cfg = zone.settings.alerts.offline if zone is not None else None
        if not self._detector_enabled(
            entity_id, bool(off_cfg.enabled) if off_cfg is not None else False
        ):
            return

        flag = self._flag(entity_id, ALERT_OFFLINE)

        if value in _OFFLINE_STATES:
            if flag.get("alerted") or entity_id in self._offline_timers:
                return

            @callback
            def _fire(_now: datetime) -> None:
                self._offline_timers.pop(entity_id, None)
                state = self.hass.states.get(entity_id)
                still = state is None or (state.state or "").lower() in _OFFLINE_STATES
                if not still:
                    return
                self._record(
                    entity_id, ALERT_OFFLINE, SEV_WARN, "Lock offline (unavailable)"
                )
                self._flag(entity_id, ALERT_OFFLINE)["alerted"] = True

            self._offline_timers[entity_id] = async_call_later(
                self.hass, DEFAULT_OFFLINE_DEBOUNCE_SECONDS, _fire
            )
            return

        # Came back to a known state -> cancel pending + recover if alerted.
        cancel = self._offline_timers.pop(entity_id, None)
        if cancel:
            cancel()
        if flag.get("alerted"):
            self._record(
                entity_id,
                ALERT_OFFLINE,
                SEV_WARN,
                "Lock back online",
                is_recovery=True,
            )
            flag["alerted"] = False

    # -- recording ----------------------------------------------------------

    def _record(
        self,
        entity_id: str,
        alert_type: str,
        severity: str,
        message: str,
        is_recovery: bool = False,
    ) -> None:
        """Append one structured alert record and persist the log.

        - Description: Builds the canonical record dict, appends it to the
          rolling capped list, and schedules an async persist. Recording is the
          ONLY action — no notification is ever sent.
        - Inputs: entity_id (str), alert_type (str), severity (str WARN/CRIT),
          message (str human-readable), is_recovery (bool).
        - Outputs: None.
        """
        zone_id, zone_name, door_name = self._zone_for(entity_id)
        record: Dict[str, Any] = {
            "timestamp": datetime.now().isoformat(),
            "zone_id": zone_id,
            "zone_name": zone_name,
            "member_entity_id": entity_id,
            "door_name": door_name,
            "alert_type": alert_type,
            "severity": severity,
            "message": message,
            "is_recovery": is_recovery,
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
        self.hass.async_create_task(self._persist())

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

    # -- dev simulation -----------------------------------------------------

    def dev_simulate(self, alert_type: str, entity_id: str, **kwargs: Any) -> None:
        """Drive a detector directly for on-demand dev testing.

        - Description: DEV-ONLY entrypoint (called from the
          ``dev_simulate_alert`` service) that invokes a detector path without
          waiting for real conditions. The engine itself is already dev-gated.
        - Inputs:
            alert_type: one of outside_hours / sustained_unlock / jam /
                low_battery / offline.
            entity_id: target member lock entity id.
            kwargs: optional per-type overrides:
                - outside_hours: ``recover`` (bool) to drive the locked-recovery
                  path; otherwise records an unlock alert directly (bypassing the
                  time gate so it is testable any time of day).
                - sustained_unlock: ``seconds`` (int, default 15) tier, or
                  ``recover`` (bool).
                - jam: ``recover`` (bool).
                - low_battery: ``percent`` (int, default below threshold).
                - offline: ``recover`` (bool).
        - Outputs: None (records alert(s)).
        """
        if alert_type == ALERT_OUTSIDE_HOURS:
            flag = self._flag(entity_id, ALERT_OUTSIDE_HOURS)
            if kwargs.get("recover"):
                flag["alerted"] = True
                self._eval_outside_hours(entity_id, "locked")
            else:
                self._record(
                    entity_id,
                    ALERT_OUTSIDE_HOURS,
                    SEV_CRIT,
                    "Unlocked outside business hours (dev-simulated)",
                )
                flag["alerted"] = True
        elif alert_type == ALERT_SUSTAINED:
            if kwargs.get("recover"):
                flag = self._flag(entity_id, ALERT_SUSTAINED)
                flag["alerted"] = True
                self._cancel_sustained(entity_id)
                self._record(
                    entity_id,
                    ALERT_SUSTAINED,
                    SEV_CRIT,
                    "Re-locked after sustained-unlock alert (dev-simulated)",
                    is_recovery=True,
                )
                flag["alerted"] = False
            else:
                seconds = int(kwargs.get("seconds", 15))
                severity = SEV_WARN if seconds < 30 else SEV_CRIT
                self._flag(entity_id, ALERT_SUSTAINED)["alerted"] = True
                self._record(
                    entity_id,
                    ALERT_SUSTAINED,
                    severity,
                    f"Unlocked >{seconds}s without re-lock (dev-simulated)",
                )
        elif alert_type == ALERT_JAM:
            if kwargs.get("recover"):
                self._flag(entity_id, ALERT_JAM)["alerted"] = True
                self._record(
                    entity_id,
                    ALERT_JAM,
                    SEV_CRIT,
                    "Recovered from jam (dev-simulated)",
                    is_recovery=True,
                )
                self._flag(entity_id, ALERT_JAM)["alerted"] = False
            else:
                self._record(
                    entity_id, ALERT_JAM, SEV_CRIT, "Lock jammed (dev-simulated)"
                )
                self._flag(entity_id, ALERT_JAM)["alerted"] = True
        elif alert_type == ALERT_LOW_BATTERY:
            percent = int(kwargs.get("percent", DEFAULT_LOW_BATTERY_THRESHOLD - 5))
            self._eval_low_battery(entity_id, str(percent))
        elif alert_type == ALERT_OFFLINE:
            if kwargs.get("recover"):
                self._flag(entity_id, ALERT_OFFLINE)["alerted"] = True
                self._record(
                    entity_id,
                    ALERT_OFFLINE,
                    SEV_WARN,
                    "Lock back online (dev-simulated)",
                    is_recovery=True,
                )
                self._flag(entity_id, ALERT_OFFLINE)["alerted"] = False
            else:
                self._record(
                    entity_id, ALERT_OFFLINE, SEV_WARN, "Lock offline (dev-simulated)"
                )
                self._flag(entity_id, ALERT_OFFLINE)["alerted"] = True
        else:
            _LOGGER.warning("dev_simulate: unknown alert_type %s", alert_type)
