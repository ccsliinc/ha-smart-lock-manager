"""OBSERVE-ONLY alert detector logic for Smart Lock Manager (dev-gated).

This module holds the per-member DETECTION primitives extracted from
:mod:`.alert_engine` so the engine file stays focused on lifecycle,
subscription topology, recording and persistence. The detectors live on
:class:`AlertDetectorsMixin`, which :class:`~.alert_engine.AlertEngine` inherits
— there is NO behaviour change, only a file split. The mixin relies on the
engine providing ``self.hass``, ``self._alerted``, ``self._sustained_timers``,
``self._record(...)`` and the zone-resolution helpers.

The detector-shared constants and the small pure helpers (``_parse_hhmm`` /
``_tiers_with_severity``) also live here as their single source of truth; the
engine re-exports the public names (``SEV_WARN`` / ``SEV_CRIT`` / the
``ALERT_*`` ids) for backward compatibility.

SECURITY: alert records carry entity ids, door names, severities and
human-readable messages only — never PIN codes.
"""

from __future__ import annotations

import logging
import time as _time
from datetime import datetime, time
from typing import Any, Callable, Dict, List, Optional

from homeassistant.core import callback
from homeassistant.helpers.event import async_call_later

from .dev_mock import is_dev_mock
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
from .zone_runtime import get_zone_for_lock

_LOGGER = logging.getLogger(__name__)

# Dev-only override: when True AND dev_mock is active, every detector observes
# regardless of the per-zone enable flag, using config thresholds when present
# and the mirror defaults otherwise — preserving dev continuity. In PRODUCTION
# (dev_mock off) this flag has no effect and the engine honors the per-zone
# enable flags strictly. Flip to False to disable the dev override entirely.
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

# Nag-policy classes (single source of truth). HEALTH conditions are persistent
# fault states (battery/jam/offline) that, once alerted, stay logically true on
# their own — so they alert ONCE then go silent until recovery (no hourly nag).
# DOOR conditions describe an operator-actionable open door that should keep
# nagging on the timer until the door is dealt with.
# - HEALTH_ALERT_TYPES: alert-type ids whose detector cores must NOT re-nag.
# - DOOR_ALERT_TYPES: alert-type ids that retain timer-driven re-alerting.
HEALTH_ALERT_TYPES = frozenset({ALERT_LOW_BATTERY, ALERT_JAM, ALERT_OFFLINE})
DOOR_ALERT_TYPES = frozenset({ALERT_OUTSIDE_HOURS, ALERT_SUSTAINED})

# Lock states the engine treats as "offline".
_OFFLINE_STATES = {"unavailable", "unknown"}


class AlertDetectorsMixin:
    """Per-member OBSERVE-ONLY detector methods for :class:`AlertEngine`.

    Mixed into the engine, which supplies ``hass``, the ``_alerted`` episode
    map, the ``_sustained_timers`` / ``_offline_timers`` handle maps and the
    ``_record`` recorder. Split out of ``alert_engine.py`` purely to keep that
    file under the size limit — behaviour is identical to the inlined version.
    """

    # Provided by the engine (declared here for type checkers / clarity).
    hass: Any
    _alerted: Dict[str, Dict[str, Any]]
    _sustained_timers: Dict[str, Callable[[], None]]
    _offline_timers: Dict[str, Callable[[], None]]

    def _record(
        self,
        entity_id: str,
        alert_type: str,
        severity: str,
        message: str,
        is_recovery: bool = False,
        origin: str = "state_change",
    ) -> None:  # pragma: no cover - provided by AlertEngine
        raise NotImplementedError

    def _should_nag(
        self, flag: Dict[str, Any], now: float
    ) -> bool:  # pragma: no cover - provided by AlertEngine
        raise NotImplementedError

    def _seed_nag(
        self, flag: Dict[str, Any], now: float
    ) -> None:  # pragma: no cover - provided by AlertEngine
        raise NotImplementedError

    # -- zone config resolution --------------------------------------------

    def _zone_settings_for(self, entity_id: str) -> Optional[Zone]:
        """Return the owning Zone for a member entity, or None if unhomed.

        - Inputs: entity_id (str lock entity id).
        - Outputs: the owning Zone (carrying ``settings``), or None.
        """
        return get_zone_for_lock(self.hass, entity_id)

    # -- companion-entity resolution (member_meta -> auto-discovery) --------

    def _resolve_jam_sensor(self, entity_id: str) -> str:
        """Resolve a member's jam binary_sensor id (member_meta first).

        - Description: SHARED resolver used by BOTH the jam state-path detector
          and the health sweep so the member_meta-then-auto-discovery resolution
          lives in ONE place. Returns the explicit
          ``settings.member_meta[entity_id].jam_sensor`` when configured, else
          the auto-discovery guess ``binary_sensor.<object_id>_jammed``.
        - Inputs: entity_id (str lock entity id).
        - Outputs: str binary_sensor entity id.
        """
        zone = self._zone_settings_for(entity_id)
        if zone is not None:
            meta = zone.settings.member_meta.get(entity_id)
            if meta is not None and meta.jam_sensor:
                return meta.jam_sensor
        object_id = entity_id.split(".", 1)[-1]
        return f"binary_sensor.{object_id}_jammed"

    def _resolve_battery_entity(self, entity_id: str) -> str:
        """Resolve a member's battery sensor id (member_meta first).

        - Description: SHARED resolver used by BOTH the low-battery state path
          and the health sweep. Returns the explicit
          ``settings.member_meta[entity_id].battery_entity`` when configured,
          else the auto-discovery guess ``sensor.<object_id>_battery``.
        - Inputs: entity_id (str lock entity id).
        - Outputs: str battery sensor entity id.
        """
        zone = self._zone_settings_for(entity_id)
        if zone is not None:
            meta = zone.settings.member_meta.get(entity_id)
            if meta is not None and meta.battery_entity:
                return meta.battery_entity
        object_id = entity_id.split(".", 1)[-1]
        return f"sensor.{object_id}_battery"

    def _detector_enabled(self, entity_id: str, configured: bool) -> bool:
        """Decide whether a detector should observe for this member.

        - Description: The observe-all override (``_OBSERVE_ALL_IN_DEV``) only
          applies when dev_mock is active — in dev, observation is kept on for
          ALL types so dev continuity is preserved even though per-zone toggles
          default to False. In PRODUCTION (dev_mock off) the per-zone
          ``enabled`` flag is honored strictly: a disabled detector does NOT
          observe, an enabled one does.
        - Inputs: entity_id (str), configured (bool — the zone's enable flag).
        - Outputs: bool.
        """
        if _OBSERVE_ALL_IN_DEV and is_dev_mock():
            return True
        return configured

    # -- alerted-state helpers (replace pyscript.alerted_*) -----------------

    def _flag(self, entity_id: str, kind: str) -> Dict[str, Any]:
        """Return the mutable alerted-state record for (entity, kind).

        - Description: ``last_nag`` (float epoch | None) tracks the last
          timer-nag/seed for the throttle. Old persisted blobs without the key
          read back as ``.get('last_nag') -> None`` (fire immediately), so the
          extended default is fully back-compatible.
        - Inputs: entity_id (str), kind (str alert-type id).
        - Outputs: dict (created on first access) with ``alerted`` + ``last_nag``.
        """
        key = f"{entity_id}|{kind}"
        return self._alerted.setdefault(key, {"alerted": False, "last_nag": None})

    # -- detectors ----------------------------------------------------------

    def _outside_hours_context(
        self, entity_id: str
    ) -> Optional[tuple[Optional[Zone], str, Dict[str, Any]]]:
        """Resolve the outside-hours gating context for a member, or None.

        - Description: SHARED gate used by both the state path and the sweep so
          the enable/severity/flag resolution lives in ONE place. Returns
          ``(zone, severity, flag)`` when the outside-hours detector should
          observe for this member, or ``None`` when it is disabled (honoring
          the dev observe-all override exactly like the other detectors).
        - Inputs: entity_id (str member lock id).
        - Outputs: (zone, severity, flag) tuple or None.
        """
        zone = self._zone_settings_for(entity_id)
        oh = zone.settings.alerts.outside_hours if zone is not None else None
        severity = oh.severity if oh is not None else SEV_CRIT
        if not self._detector_enabled(
            entity_id, bool(oh.enabled) if oh is not None else False
        ):
            return None
        flag = self._flag(entity_id, ALERT_OUTSIDE_HOURS)
        return zone, severity, flag

    def _eval_outside_hours(self, entity_id: str, value: str) -> None:
        """Outside-business-hours unlock detector (mirrors the pyscript).

        Fires CRIT when a member is ``unlocked`` outside the business window;
        emits a RECOVERY when a previously-alerted member returns to ``locked``.

        - Inputs: entity_id (str), value (str normalized lock state).
        - Outputs: None (records alerts).
        """
        resolved = self._outside_hours_context(entity_id)
        if resolved is None:
            return
        zone, severity, flag = resolved

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
            flag["last_nag"] = None
            return

        self._check_outside_hours(entity_id, value, zone, severity, flag)

    def _check_outside_hours(
        self,
        entity_id: str,
        value: str,
        zone: Optional[Zone],
        severity: str,
        flag: Dict[str, Any],
        origin: str = "state_change",
    ) -> None:
        """Fire the outside-hours alert if a member is unlocked off-hours.

        - Description: The SHARED core check called by BOTH the state-trigger
          path (:meth:`_eval_outside_hours`) and the periodic sweep
          (``AlertEngine._run_outside_hours_sweep``). Records exactly one
          outside-hours alert when the member is ``unlocked`` AND outside the
          zone's business window AND not already in an alerted episode, then
          sets the per-episode alerted flag. Recovery (re-lock) is handled by
          the caller's state path, NOT here — the sweep never sees re-locks.
        - Inputs: entity_id (str), value (str normalized lock state), zone
          (owning Zone or None), severity (str WARN/CRIT), flag (the mutable
          per-(entity, outside_hours) alerted-state dict).
        - Outputs: None (records an alert + flips ``flag['alerted']``).
        """
        if value != "unlocked":
            return
        if self._in_business_hours(zone):
            return
        now = _time.time()
        if flag.get("alerted"):
            # Ongoing episode: only timer-origin sweeps may re-fire, throttled to
            # one Nag per nag_interval. State-change re-evals never re-record.
            if origin == "timer" and self._should_nag(flag, now):
                self._record(
                    entity_id,
                    ALERT_OUTSIDE_HOURS,
                    severity,
                    "Still unlocked outside business hours",
                    origin="timer",
                )
                flag["last_nag"] = now
            return
        self._record(
            entity_id,
            ALERT_OUTSIDE_HOURS,
            severity,
            "Unlocked outside business hours",
            origin=origin,
        )
        flag["alerted"] = True
        # Seed last_nag at the initial alert so the first Nag waits a full
        # interval (prevents an Alert + immediate-Nag double-hit).
        self._seed_nag(flag, now)

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
                flag["last_nag"] = None

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
            # Tiered sustained-unlock fires are timer-driven -> Nags (snoozable).
            self._record(
                entity_id,
                ALERT_SUSTAINED,
                severity,
                f"Unlocked >{seconds}s without re-lock",
                origin="timer",
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
