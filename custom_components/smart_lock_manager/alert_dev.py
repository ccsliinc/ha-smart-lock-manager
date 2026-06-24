"""DEV-ONLY alert simulation helpers for Smart Lock Manager (dev-gated).

Extracted from :mod:`.alert_engine` so the engine file stays under the size
limit. :class:`AlertDevSimMixin` carries the ``dev_simulate`` entrypoint used by
the DEV-MOCK-ONLY ``dev_simulate_alert`` service to drive a detector path
on-demand without waiting for real conditions. Mixed into
:class:`~.alert_engine.AlertEngine`, which provides ``_record`` / ``_flag`` /
``_eval_low_battery`` and the dev-gated construction. No behaviour change — this
is a pure file split.

SECURITY: simulated records carry entity ids, door names, severities and
human-readable messages only — never PIN codes.
"""

from __future__ import annotations

import logging
from typing import Any, Dict

from .alert_detectors import (
    ALERT_JAM,
    ALERT_LOW_BATTERY,
    ALERT_OFFLINE,
    ALERT_OUTSIDE_HOURS,
    ALERT_SUSTAINED,
    DEFAULT_LOW_BATTERY_THRESHOLD,
    SEV_CRIT,
    SEV_WARN,
)

_LOGGER = logging.getLogger(__name__)


class AlertDevSimMixin:
    """DEV-ONLY ``dev_simulate`` entrypoint for :class:`AlertEngine`."""

    def _flag(
        self, entity_id: str, kind: str
    ) -> Dict[str, Any]:  # pragma: no cover - provided by AlertEngine
        raise NotImplementedError

    def _record(
        self,
        entity_id: str,
        alert_type: str,
        severity: str,
        message: str,
        is_recovery: bool = False,
    ) -> None:  # pragma: no cover - provided by AlertEngine
        raise NotImplementedError

    def _cancel_sustained(
        self, entity_id: str
    ) -> None:  # pragma: no cover - provided by AlertEngine
        raise NotImplementedError

    def _eval_low_battery(
        self, lock_entity_id: str, raw_value: str
    ) -> None:  # pragma: no cover - provided by AlertEngine
        raise NotImplementedError

    def _eval_outside_hours(
        self, entity_id: str, value: str
    ) -> None:  # pragma: no cover - provided by AlertDetectorsMixin
        raise NotImplementedError

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
