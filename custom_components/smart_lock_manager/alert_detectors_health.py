"""Persistent-state health detectors for Smart Lock Manager (dev-gated).

Split out of :mod:`.alert_detectors` so neither file exceeds the 500-line
standard. This module holds the three PERSISTENT-STATE detectors — jam,
low_battery and offline — each of which can be stuck in a bad state without a
fresh state transition (a battery sitting at 0%, a lock left jammed, a member
that went unavailable hours ago). Those are exactly the conditions the periodic
HEALTH SWEEP (:meth:`AlertEngine._run_health_sweep`) re-evaluates on a cadence.

To let the state-change path and the sweep share ONE fire-condition, each
detector is factored into an ``_eval_*`` method (the state-trigger entrypoint,
which also owns recovery) and a ``_check_*`` core (the shared "is the persistent
bad condition true right now, and if so record exactly once" check). The sweep
calls only the ``_check_*`` cores; the ``_eval_*`` methods call the same cores
so behaviour is byte-identical to the pre-split inlined version.

These methods live on :class:`AlertHealthDetectorsMixin`, mixed into
:class:`~.alert_engine.AlertEngine` alongside
:class:`~.alert_detectors.AlertDetectorsMixin`. The mixin relies on the engine /
sibling mixin providing ``self.hass``, ``self._alerted``, ``self._offline_timers``,
``self._record(...)``, ``self._flag(...)``, ``self._detector_enabled(...)`` and
``self._zone_settings_for(...)``.

SECURITY: alert records carry entity ids, door names, severities and
human-readable messages only — never PIN codes.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime
from typing import Any, Callable, Dict, Optional

from homeassistant.core import callback
from homeassistant.helpers.event import async_call_later

from .models.zone import Zone

_LOGGER = logging.getLogger(__name__)

# Imported lazily-by-name from alert_detectors to avoid a circular import at
# module load (alert_detectors imports nothing from here). These are the same
# constants alert_detectors defines as their single source of truth.
from .alert_detectors import (  # noqa: E402  (kept after docstring/imports above)
    _OFFLINE_STATES,
    ALERT_JAM,
    ALERT_LOW_BATTERY,
    ALERT_OFFLINE,
    DEFAULT_LOW_BATTERY_THRESHOLD,
    DEFAULT_OFFLINE_DEBOUNCE_SECONDS,
    SEV_WARN,
)


class AlertHealthDetectorsMixin:
    """Persistent-state (jam / low_battery / offline) detector methods.

    Mixed into :class:`AlertEngine` next to
    :class:`~.alert_detectors.AlertDetectorsMixin`. Each detector exposes an
    ``_eval_*`` state-trigger entrypoint (owns recovery) and a ``_check_*``
    shared core (re-used by the periodic health sweep). The episode dedup flag
    makes both paths idempotent — recording at most one alert per bad episode.
    """

    # Provided by the engine / sibling mixin (declared for type checkers).
    hass: Any
    _alerted: Dict[str, Dict[str, Any]]
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

    def _flag(
        self, entity_id: str, kind: str
    ) -> Dict[str, Any]:  # pragma: no cover - provided by sibling mixin
        raise NotImplementedError

    def _detector_enabled(
        self, entity_id: str, configured: bool
    ) -> bool:  # pragma: no cover - provided by sibling mixin
        raise NotImplementedError

    def _zone_settings_for(
        self, entity_id: str
    ) -> Optional[Zone]:  # pragma: no cover - provided by sibling mixin
        raise NotImplementedError

    def _resolve_jam_sensor(
        self, entity_id: str
    ) -> str:  # pragma: no cover - provided by sibling mixin
        raise NotImplementedError

    # -- jam ----------------------------------------------------------------

    def _jam_context(self, entity_id: str) -> Optional[tuple[str, Dict[str, Any]]]:
        """Resolve the jam gating context for a member, or None if disabled.

        - Description: SHARED gate used by both the state path and the health
          sweep so the enable/severity/flag resolution lives in ONE place.
        - Inputs: entity_id (str member lock id).
        - Outputs: (severity, flag) tuple, or None when the detector is off.
        """
        zone = self._zone_settings_for(entity_id)
        jam_cfg = zone.settings.alerts.jam if zone is not None else None
        severity = jam_cfg.severity if jam_cfg is not None else "CRIT"
        if not self._detector_enabled(
            entity_id, bool(jam_cfg.enabled) if jam_cfg is not None else False
        ):
            return None
        return severity, self._flag(entity_id, ALERT_JAM)

    def _eval_jam(self, entity_id: str, value: str, attributes: Dict[str, Any]) -> None:
        """Jam / lock-failure detector (mirrors lock_doors.py jam context).

        Fires when the lock state is ``jammed`` OR a companion
        ``binary_sensor.<object_id>_jammed`` reads ``on``. Recovery when the
        member next reports ``locked``.

        - Inputs: entity_id (str), value (str normalized lock state),
          attributes (lock entity attributes; unused but kept for parity).
        - Outputs: None (records alerts).
        """
        ctx = self._jam_context(entity_id)
        if ctx is None:
            return
        severity, flag = ctx

        if self._check_jam(entity_id, value, severity, flag):
            return
        if flag.get("alerted") and value == "locked":
            self._record(
                entity_id,
                ALERT_JAM,
                severity,
                "Recovered from jam (locked)",
                is_recovery=True,
            )
            flag["alerted"] = False
            flag["last_nag"] = None

    def _check_jam(
        self,
        entity_id: str,
        value: str,
        severity: str,
        flag: Dict[str, Any],
        origin: str = "state_change",
    ) -> bool:
        """Fire the jam alert if the member is currently jammed (shared core).

        - Description: The SHARED check called by BOTH the state-trigger path
          (:meth:`_eval_jam`) and the periodic health sweep. Records exactly one
          jam alert when the member is jammed AND not already in an alerted
          episode, then sets the per-episode flag. Recovery (re-lock) is owned by
          the state path, NOT here — the sweep never records a jam recovery.
        - Inputs: entity_id (str), value (str normalized lock state), severity
          (str), flag (the mutable per-(entity, jam) alerted-state dict).
        - Outputs: bool — True if it recorded a NEW jam alert this call.
        """
        jammed = value == "jammed" or self._jam_sensor_on(entity_id)
        if not jammed:
            return False
        now = time.time()
        if flag.get("alerted"):
            # Ongoing jam: only timer-origin sweeps re-fire, throttled.
            if origin == "timer" and self._should_nag(flag, now):
                self._record(
                    entity_id, ALERT_JAM, severity, "Still jammed", origin="timer"
                )
                flag["last_nag"] = now
            return False
        self._record(entity_id, ALERT_JAM, severity, "Lock jammed", origin=origin)
        flag["alerted"] = True
        self._seed_nag(flag, now)
        return True

    def _jam_sensor_on(self, entity_id: str) -> bool:
        """Return True if the companion jam binary_sensor reads ``on``.

        - Description: Resolves the companion jam binary_sensor via the shared
          :meth:`_resolve_jam_sensor` (explicit ``member_meta.jam_sensor`` first,
          auto-discovery ``binary_sensor.<object_id>_jammed`` second), then reads
          its live state.
        - Inputs: entity_id (str lock entity id).
        - Outputs: bool.
        """
        jam = self.hass.states.get(self._resolve_jam_sensor(entity_id))
        return jam is not None and (jam.state or "").lower() == "on"

    # -- low_battery --------------------------------------------------------

    def _low_battery_context(
        self, entity_id: str
    ) -> Optional[tuple[int, Dict[str, Any]]]:
        """Resolve the low-battery gating context for a member, or None.

        - Inputs: entity_id (str member lock id).
        - Outputs: (threshold, flag) tuple, or None when the detector is off.
        """
        zone = self._zone_settings_for(entity_id)
        lb = zone.settings.alerts.low_battery if zone is not None else None
        threshold = lb.threshold if lb is not None else DEFAULT_LOW_BATTERY_THRESHOLD
        if not self._detector_enabled(
            entity_id, bool(lb.enabled) if lb is not None else False
        ):
            return None
        return threshold, self._flag(entity_id, ALERT_LOW_BATTERY)

    def _eval_low_battery(self, lock_entity_id: str, raw_value: str) -> None:
        """Low-battery detector — WARN when below threshold, recovery above.

        - Inputs: lock_entity_id (str), raw_value (str battery percent).
        - Outputs: None (records alerts).
        """
        try:
            percent = int(float(raw_value))
        except (TypeError, ValueError):
            return

        ctx = self._low_battery_context(lock_entity_id)
        if ctx is None:
            return
        threshold, flag = ctx

        if self._check_low_battery(lock_entity_id, percent, threshold, flag):
            return
        # Small hysteresis on recovery to avoid flapping at the threshold.
        if percent >= threshold + 5 and flag.get("alerted"):
            self._record(
                lock_entity_id,
                ALERT_LOW_BATTERY,
                SEV_WARN,
                f"Battery recovered ({percent}%)",
                is_recovery=True,
            )
            flag["alerted"] = False
            flag["last_nag"] = None

    def _check_low_battery(
        self,
        entity_id: str,
        percent: int,
        threshold: int,
        flag: Dict[str, Any],
        origin: str = "state_change",
    ) -> bool:
        """Fire the low-battery alert if below threshold (shared core).

        - Description: SHARED check called by BOTH the state path
          (:meth:`_eval_low_battery`) and the health sweep. Records exactly one
          WARN when ``percent < threshold`` and not already alerted, then sets
          the flag. Recovery (with hysteresis) is owned by the state path.
        - Inputs: entity_id (str), percent (int current battery), threshold
          (int), flag (the mutable per-(entity, low_battery) alerted dict).
        - Outputs: bool — True if it recorded a NEW low-battery alert this call.
        """
        if percent >= threshold:
            return False
        now = time.time()
        if flag.get("alerted"):
            # Ongoing low battery: only timer-origin sweeps re-fire, throttled.
            if origin == "timer" and self._should_nag(flag, now):
                self._record(
                    entity_id,
                    ALERT_LOW_BATTERY,
                    SEV_WARN,
                    f"Battery still low ({percent}%)",
                    origin="timer",
                )
                flag["last_nag"] = now
            return False
        self._record(
            entity_id,
            ALERT_LOW_BATTERY,
            SEV_WARN,
            f"Battery low ({percent}%)",
            origin=origin,
        )
        flag["alerted"] = True
        self._seed_nag(flag, now)
        return True

    # -- offline ------------------------------------------------------------

    def _offline_context(self, entity_id: str) -> Optional[Dict[str, Any]]:
        """Resolve the offline gating context for a member, or None.

        - Inputs: entity_id (str member lock id).
        - Outputs: the per-(entity, offline) flag dict, or None when disabled.
        """
        zone = self._zone_settings_for(entity_id)
        off_cfg = zone.settings.alerts.offline if zone is not None else None
        if not self._detector_enabled(
            entity_id, bool(off_cfg.enabled) if off_cfg is not None else False
        ):
            return None
        return self._flag(entity_id, ALERT_OFFLINE)

    def _eval_offline(self, entity_id: str, value: str) -> None:
        """Offline detector — WARN when unavailable/unknown (debounced).

        - Description: A transient Z-Wave blip should not alert, so an offline
          alert is armed via ``async_call_later`` and only fires if the member
          is STILL offline after the debounce window. Recovery fires when the
          member returns to a known state.
        - Inputs: entity_id (str), value (str normalized lock state).
        - Outputs: None (records alerts; schedules a debounce timer).
        """
        flag = self._offline_context(entity_id)
        if flag is None:
            return

        if value in _OFFLINE_STATES:
            if flag.get("alerted") or entity_id in self._offline_timers:
                return

            @callback
            def _fire(_now: datetime) -> None:
                self._offline_timers.pop(entity_id, None)
                state = self.hass.states.get(entity_id)
                live = (state.state if state is not None else "unavailable").lower()
                if live not in _OFFLINE_STATES:
                    return
                self._check_offline(
                    entity_id, live, self._flag(entity_id, ALERT_OFFLINE)
                )

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
            flag["last_nag"] = None

    def _check_offline(
        self,
        entity_id: str,
        value: str,
        flag: Dict[str, Any],
        origin: str = "state_change",
    ) -> bool:
        """Fire the offline alert if the member is offline (shared core).

        - Description: SHARED check called by the debounce-timer ``_fire`` path
          AND the periodic health sweep. Records exactly one WARN when the
          member is in an offline state AND not already alerted, then sets the
          flag. Used by the sweep DIRECTLY (no extra debounce): a member still
          offline at sweep time has already been offline at least one sweep
          interval, so the debounce intent is satisfied. Recovery (back online)
          is owned by the state path. The pending-timer guard in
          :meth:`_eval_offline` plus this episode flag prevent any double-record
          between the timer path and the sweep.
        - Inputs: entity_id (str), value (str normalized lock state), flag (the
          mutable per-(entity, offline) alerted dict).
        - Outputs: bool — True if it recorded a NEW offline alert this call.
        """
        if value not in _OFFLINE_STATES:
            return False
        now = time.time()
        if flag.get("alerted"):
            # Ongoing offline: only timer-origin sweeps re-fire, throttled.
            if origin == "timer" and self._should_nag(flag, now):
                self._record(
                    entity_id,
                    ALERT_OFFLINE,
                    SEV_WARN,
                    "Still offline (unavailable)",
                    origin="timer",
                )
                flag["last_nag"] = now
            return False
        self._record(
            entity_id,
            ALERT_OFFLINE,
            SEV_WARN,
            "Lock offline (unavailable)",
            origin=origin,
        )
        flag["alerted"] = True
        self._seed_nag(flag, now)
        return True
