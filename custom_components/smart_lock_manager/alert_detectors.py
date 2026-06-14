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
from datetime import datetime, time
from typing import Any, Callable, Dict, List, Optional

from homeassistant.core import callback
from homeassistant.helpers.event import async_call_later

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
    ) -> None:  # pragma: no cover - provided by AlertEngine
        raise NotImplementedError

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
