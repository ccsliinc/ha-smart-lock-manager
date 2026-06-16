"""Periodic alert sweeps for Smart Lock Manager (dev-gated).

The state-change detector path only re-evaluates a condition when a member lock
emits a fresh state event. That leaves a gap class for PERSISTENT conditions: a
door left unlocked past close, a battery stuck below threshold, a lock left
jammed, a member that went offline hours ago — none re-fire without a
transition. These two periodic sweeps close that gap by re-running the SAME
shared fire-condition checks the state path uses, on a configurable cadence.

Both sweeps live on :class:`AlertSweepsMixin`, mixed into
:class:`~.alert_engine.AlertEngine`. They are registered (and torn down) by the
engine's :meth:`_subscribe` / :meth:`_teardown_subscriptions` at the
globally-configured cadences (see :mod:`.storage.global_settings`). Split out of
``alert_engine.py`` purely to keep that file under the 500-line standard —
behaviour is identical to the inlined version. The mixin relies on the engine /
sibling mixins providing ``self.hass``, the shared ``_check_*`` /
``_*_context`` detector cores and ``self._battery_entity_for(...)``.

SECURITY: alert records carry entity ids, door names, severities and
human-readable messages only — never PIN codes.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, Optional

from homeassistant.core import callback

from .dev_mock import is_dev_mock
from .models.zone import Zone
from .zone_runtime import get_zone_registry

_LOGGER = logging.getLogger(__name__)


class AlertSweepsMixin:
    """Periodic outside-hours + health sweep methods for :class:`AlertEngine`.

    Mixed into the engine, which supplies ``hass`` and (via the detector mixins)
    the shared ``_check_*`` cores + ``_*_context`` gates. Each sweep re-runs the
    persistent fire-condition on a cadence; the per-episode alerted flag makes
    repeated sweeps idempotent and prevents double-recording versus the state
    path (both share the same flag). Recovery is always owned by the state path.
    """

    hass: Any

    # -- cross-mixin methods (provided by the engine / detector mixins) ------
    # Declared here so the type checker knows the sweeps may call them; the real
    # implementations live on AlertEngine / AlertDetectorsMixin /
    # AlertHealthDetectorsMixin via the mixin composition.

    def _outside_hours_context(
        self, entity_id: str
    ) -> Optional[tuple[Any, str, Dict[str, Any]]]:  # pragma: no cover
        raise NotImplementedError

    def _check_outside_hours(
        self,
        entity_id: str,
        value: str,
        zone: Optional[Zone],
        severity: str,
        flag: Dict[str, Any],
    ) -> None:  # pragma: no cover
        raise NotImplementedError

    def _jam_context(
        self, entity_id: str
    ) -> Optional[tuple[str, Dict[str, Any]]]:  # pragma: no cover
        raise NotImplementedError

    def _check_jam(
        self, entity_id: str, value: str, severity: str, flag: Dict[str, Any]
    ) -> bool:  # pragma: no cover
        raise NotImplementedError

    def _offline_context(
        self, entity_id: str
    ) -> Optional[Dict[str, Any]]:  # pragma: no cover
        raise NotImplementedError

    def _check_offline(
        self, entity_id: str, value: str, flag: Dict[str, Any]
    ) -> bool:  # pragma: no cover
        raise NotImplementedError

    def _low_battery_context(
        self, entity_id: str
    ) -> Optional[tuple[int, Dict[str, Any]]]:  # pragma: no cover
        raise NotImplementedError

    def _check_low_battery(
        self, entity_id: str, percent: int, threshold: int, flag: Dict[str, Any]
    ) -> bool:  # pragma: no cover
        raise NotImplementedError

    def _battery_entity_for(self, lock_entity_id: str) -> str:  # pragma: no cover
        raise NotImplementedError

    @callback
    def _run_outside_hours_sweep(self, _now: datetime) -> None:
        """Periodic boundary sweep for the still-unlocked-outside-hours gap.

        - Description: The state-trigger path only re-evaluates outside_hours on
          a lock STATE CHANGE, so a door unlocked DURING business hours that
          stays unlocked past close is never re-checked at the boundary. This
          sweep — fired at the configurable ``outside_hours_sweep_minutes``
          cadence by the interval trigger registered in
          :meth:`AlertEngine._subscribe` — closes that gap. For every zone with
          the outside_hours detector enabled, it reads each member's LIVE state
          and runs the SAME shared off-hours check the state path uses
          (:meth:`_check_outside_hours`): an unlocked member outside the zone's
          business window records exactly one outside_hours alert. The
          per-episode alerted flag makes repeated sweeps idempotent (no
          re-fire), and recovery-on-relock stays owned by the state path. ONLY
          outside_hours is swept here (sustained_unlock is NOT — its own timer
          chain already covers the duration).
        - Inputs: _now (datetime supplied by the interval trigger; unused — the
          detector reads ``datetime.now()`` itself).
        - Outputs: None (records alerts as a side effect).
        """
        for zone in get_zone_registry(self.hass).values():
            if not zone.settings.alerts.outside_hours.enabled and not is_dev_mock():
                continue
            for entity_id in zone.member_lock_entity_ids:
                resolved = self._outside_hours_context(entity_id)
                if resolved is None:
                    continue
                resolved_zone, severity, flag = resolved
                state = self.hass.states.get(entity_id)
                value = (state.state if state is not None else "unknown").lower()
                self._check_outside_hours(
                    entity_id, value, resolved_zone, severity, flag
                )

    @callback
    def _run_health_sweep(self, _now: datetime) -> None:
        """Periodic sweep for the persistent jam / low_battery / offline gap.

        - Description: The state-trigger path only re-evaluates these three
          PERSISTENT conditions on a state CHANGE, so a member that has been
          jammed / at low battery / offline for a long time without a fresh
          transition would never re-fire. This sweep — fired at the configurable
          ``health_sweep_minutes`` cadence by the interval trigger registered in
          :meth:`AlertEngine._subscribe` — closes that gap. For every zone, for
          each member, it reads the member's LIVE state and runs the SAME shared
          check the state path uses (:meth:`_check_jam` / :meth:`_check_offline`
          / :meth:`_check_low_battery`), each gated by its own context helper so
          a disabled detector is skipped exactly like the state path. The
          per-episode alerted flag makes repeated sweeps idempotent (no re-fire
          while still bad) and prevents any double-record versus the state path
          (shared flag). Recovery stays owned by the state path (the sweep never
          records a recovery). ``sustained_unlock`` is deliberately NOT swept —
          its own timer chain already covers it.
        - Inputs: _now (datetime from the interval trigger; unused — the checks
          read live state themselves).
        - Outputs: None (records alerts as a side effect).
        """
        for zone in get_zone_registry(self.hass).values():
            alerts_cfg = zone.settings.alerts
            for entity_id in zone.member_lock_entity_ids:
                state = self.hass.states.get(entity_id)
                value = (state.state if state is not None else "unknown").lower()

                if alerts_cfg.jam.enabled or is_dev_mock():
                    ctx = self._jam_context(entity_id)
                    if ctx is not None:
                        severity, flag = ctx
                        self._check_jam(entity_id, value, severity, flag)

                if alerts_cfg.offline.enabled or is_dev_mock():
                    offline_flag = self._offline_context(entity_id)
                    if offline_flag is not None:
                        self._check_offline(entity_id, value, offline_flag)

                if alerts_cfg.low_battery.enabled or is_dev_mock():
                    self._sweep_low_battery(entity_id)

    def _sweep_low_battery(self, entity_id: str) -> None:
        """Re-evaluate the low-battery condition from the live battery sensor.

        - Description: Health-sweep helper. Reads the companion battery sensor's
          live value and runs the shared :meth:`_check_low_battery` core so a
          battery sitting below threshold re-fires (once) even with no fresh
          state event. No-op when the sensor is missing / non-numeric or the
          detector is disabled.
        - Inputs: entity_id (str member lock id).
        - Outputs: None (records an alert as a side effect).
        """
        battery_entity = self._battery_entity_for(entity_id)
        state = self.hass.states.get(battery_entity)
        if state is None:
            return
        try:
            percent = int(float(state.state))
        except (TypeError, ValueError):
            return
        ctx = self._low_battery_context(entity_id)
        if ctx is None:
            return
        threshold, flag = ctx
        self._check_low_battery(entity_id, percent, threshold, flag)
