"""Lock + verify + retry + recording helpers for the AUTO-LOCK engine.

Extracted from :mod:`.auto_lock` so the engine file stays under the size limit.
:class:`AutoLockVerifyMixin` carries the shared per-member securing path used by
BOTH the scheduled COB run and the idle-expiry path — issue ``lock.lock``
(gated), wait, verify (Door Lock CC boltStatus or heuristic), retry, and on
final failure route a CRIT alert — plus the outcome/observe recording and the
failure-alert routing. Mixed into :class:`~.auto_lock.AutoLockEngine`, which
provides ``hass``, ``records``, ``_may_execute`` and ``_friendly_name`` is
defined here. No behaviour change — this is a pure file split.

SECURITY: auto-lock records carry entity ids, door names, severities and
human-readable messages only — never PIN codes.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from .alert_engine import ALERT_ENGINE_KEY, SEV_CRIT
from .dev_mock import MOCK_BOLT, is_dev_mock
from .models.zone import Zone

_LOGGER = logging.getLogger(__name__)

# Door Lock Command Class for the zwave_js boltStatus read (port of the
# pyscript constant).
DOOR_LOCK_CC = 98

# Auto-lock-failure alert identifier (routed through the AlertEngine).
ALERT_AUTO_LOCK_FAILED = "auto_lock_failed"

# Rolling cap on the in-memory auto-lock outcome record list.
MAX_RECORDS = 100


class AutoLockVerifyMixin:
    """Securing / verify / retry / recording methods for :class:`AutoLockEngine`.

    Mixed into the engine, which supplies ``hass``, the ``records`` list, the
    mode identifiers and ``_may_execute``. Split out of ``auto_lock.py`` purely
    to keep that file under the size limit — behaviour is identical.
    """

    # Provided by the engine (declared for type checkers / clarity).
    hass: Any
    records: List[Dict[str, Any]]

    def _may_execute(self) -> bool:  # pragma: no cover - provided by AutoLockEngine
        raise NotImplementedError

    # -- lock + verify + retry (shared by both modes) -----------------------

    async def _secure_member(
        self, zone: Zone, entity_id: str, mode: str, cfg: Any
    ) -> Dict[str, Any]:
        """Lock one member and verify, with up to ``max_attempts`` retries.

        - Description: Faithful port of the pyscript ``_secure_lock`` loop:
          issue ``lock.lock`` (gated), wait ``settle_seconds``, verify, retry.
          On final failure routes a CRIT alert through the AlertEngine. Each
          member is isolated — a raised error is caught and recorded as a fail.
        - Inputs: zone (Zone), entity_id (str), mode (str), cfg
          (ScheduledAutoLock carrying max_attempts/settle/verify_boltstatus).
        - Outputs: outcome dict {timestamp, zone, member, mode, result,
          attempts, method, state}.
        """
        # OBSERVE posture: engine is running but NOT cleared to issue a real
        # lock (PROD OBSERVE with SLM_ENABLE_REAL_AUTOLOCK off). Record a
        # "would auto-lock" intent for parity and return WITHOUT issuing
        # lock.lock, sleeping, or reading boltStatus/verify — nothing was
        # locked, so there is nothing to verify and no failure to alert on.
        if not self._may_execute():
            return self._record_would_lock(zone, entity_id, mode, cfg)

        max_attempts = max(1, int(getattr(cfg, "max_attempts", 3)))
        settle = max(0, int(getattr(cfg, "settle_seconds", 5)))
        verify_bolt = bool(getattr(cfg, "verify_boltstatus", True))
        friendly = self._friendly_name(entity_id)

        attempts = 0
        success = False
        method = "heuristic"
        last_state = "unknown"
        try:
            for attempt in range(1, max_attempts + 1):
                attempts = attempt
                await self._issue_lock(entity_id)
                if settle:
                    await asyncio.sleep(settle)
                success, last_state, method = await self._verify_locked(
                    entity_id, verify_bolt
                )
                _LOGGER.info(
                    "AutoLockEngine: %s attempt %d/%d -> success=%s state=%s via=%s",
                    friendly,
                    attempt,
                    max_attempts,
                    success,
                    last_state,
                    method,
                )
                if success:
                    break
        except Exception as exc:  # noqa: BLE001 - per-member isolation
            _LOGGER.error("AutoLockEngine: error securing %s: %s", entity_id, exc)
            method = "exception"

        outcome = self._record_outcome(
            zone, entity_id, mode, success, attempts, method, last_state
        )
        if not success:
            self._raise_failure_alert(zone, entity_id, mode, attempts, last_state)
        return outcome

    async def _issue_lock(self, entity_id: str) -> None:
        """Issue the ``lock.lock`` service call for a member, if permitted.

        - Description: The ONLY place a real ``lock.lock`` is dispatched. Gated
          by :meth:`_may_execute` — in production with the real flag off this is
          a logged no-op, so the engine never touches hardware. In dev it drives
          the dummy template lock (input_boolean), proving the full path.
        - Inputs: entity_id (str member lock id).
        - Outputs: None.
        """
        if not self._may_execute():
            _LOGGER.info(
                "AutoLockEngine: GATED (no real exec) — would lock.lock %s", entity_id
            )
            return
        await self.hass.services.async_call(
            "lock", "lock", {"entity_id": entity_id}, blocking=True
        )

    async def _verify_locked(
        self, entity_id: str, verify_bolt: bool
    ) -> Tuple[bool, str, str]:
        """Determine whether a member is secured (boltStatus or heuristic).

        - Description: Faithful port of the pyscript ``_verify_locked``. A jam
          sensor reading ``on`` is always a hard failure. When ``verify_bolt``
          and a boltStatus is available, ``"unlocked"`` -> failure, anything
          else -> success. Otherwise the heuristic: ``locked`` -> success;
          ``unknown`` with jam off -> success (warning); else failure.
        - Inputs: entity_id (str), verify_bolt (bool).
        - Outputs: tuple(success bool, lock_state str, method str).
        """
        state = self.hass.states.get(entity_id)
        lock_state = (state.state if state is not None else "unknown") or "unknown"
        lock_state = str(lock_state).lower()

        if self._jam_sensor_on(entity_id):
            return (False, lock_state, "jammed-sensor")

        if verify_bolt:
            bolt = await self._read_bolt_status(entity_id, lock_state)
            if bolt is not None:
                if bolt == "unlocked":
                    return (False, lock_state, "boltStatus")
                return (True, lock_state, "boltStatus")

        # Heuristic fallback (boltStatus unavailable / disabled).
        if lock_state == "locked":
            return (True, lock_state, "heuristic")
        if lock_state == "unknown" and not self._jam_sensor_on(entity_id):
            _LOGGER.warning(
                "AutoLockEngine: %s state='unknown' jam=off — treating as locked "
                "(cosmetic quirk; bolt not positively confirmed)",
                entity_id,
            )
            return (True, lock_state, "heuristic")
        return (False, lock_state, "heuristic")

    async def _read_bolt_status(self, entity_id: str, lock_state: str) -> Optional[str]:
        """Read Door Lock CC (98) ``boltStatus`` for a lock.

        - Description: In dev there is no real Z-Wave, so boltStatus is served
          from :data:`MOCK_BOLT` (derived from the live state, or a forced
          override to exercise verify-failure). In production with the real
          autolock flag on, this reads via ``zwave_js.invoke_cc_api`` exactly
          like the pyscript; returns None (-> heuristic) if unavailable.
        - Inputs: entity_id (str), lock_state (str live HA lock state).
        - Outputs: str lowercased boltStatus, or None.
        """
        if is_dev_mock():
            return MOCK_BOLT.read(entity_id, lock_state)
        return await self._read_bolt_status_real(entity_id)

    async def _read_bolt_status_real(self, entity_id: str) -> Optional[str]:
        """Production boltStatus read via ``zwave_js.invoke_cc_api``.

        - Description: Only reached outside dev. Mirrors the pyscript dig for a
          ``boltStatus``-bearing dict across HA-version response shapes. Any
          error / unusable response yields None so the heuristic decides.
        - Inputs: entity_id (str).
        - Outputs: str lowercased boltStatus, or None.
        """
        try:
            resp = await self.hass.services.async_call(
                "zwave_js",
                "invoke_cc_api",
                {
                    "entity_id": entity_id,
                    "command_class": DOOR_LOCK_CC,
                    "method_name": "get",
                    "parameters": [],
                },
                blocking=True,
                return_response=True,
            )
        except Exception as exc:  # noqa: BLE001 - fall back to heuristic
            _LOGGER.debug(
                "AutoLockEngine: boltStatus read failed %s: %s", entity_id, exc
            )
            return None
        return _dig_bolt_status(resp)

    def _jam_sensor_on(self, entity_id: str) -> bool:
        """Return True if the companion jam binary_sensor reads ``on``.

        - Inputs: entity_id (str lock entity id).
        - Outputs: bool.
        """
        object_id = entity_id.split(".", 1)[-1]
        jam = self.hass.states.get(f"binary_sensor.{object_id}_jammed")
        return jam is not None and (jam.state or "").lower() == "on"

    # -- recording + alert routing ------------------------------------------

    def _friendly_name(self, entity_id: str) -> str:
        """Return the live HA friendly name for an entity, or its id.

        - Inputs: entity_id (str).
        - Outputs: str display name (never empty).
        """
        state = self.hass.states.get(entity_id)
        if state is not None:
            name = state.attributes.get("friendly_name")
            if name:
                return str(name)
        return entity_id

    def _record_outcome(
        self,
        zone: Zone,
        entity_id: str,
        mode: str,
        success: bool,
        attempts: int,
        method: str,
        state: str,
    ) -> Dict[str, Any]:
        """Append one auto-lock outcome record (most-recent last).

        - Inputs: zone (Zone), entity_id (str), mode (str), success (bool),
          attempts (int), method (str), state (str final lock state).
        - Outputs: the appended outcome dict.
        """
        record: Dict[str, Any] = {
            "timestamp": datetime.now().isoformat(),
            "zone_id": zone.zone_id,
            "zone_name": zone.name,
            "member_entity_id": entity_id,
            "door_name": self._friendly_name(entity_id),
            "mode": mode,
            "result": "success" if success else "failed",
            "attempts": attempts,
            "method": method,
            "state": state,
        }
        self.records.append(record)
        if len(self.records) > MAX_RECORDS:
            self.records = self.records[-MAX_RECORDS:]
        _LOGGER.info(
            "AutoLockEngine RESULT: %s %s on %s (zone '%s') attempts=%d via=%s",
            record["result"].upper(),
            mode,
            record["door_name"],
            zone.name,
            attempts,
            method,
        )
        return record

    def _record_would_lock(
        self, zone: Zone, entity_id: str, mode: str, cfg: Any
    ) -> Dict[str, Any]:
        """Record a PROD-OBSERVE "would auto-lock" intent (no real action).

        - Description: The OBSERVE-mode counterpart to :meth:`_secure_member`'s
          execute path. Appends an outcome record with ``result="would_lock"``
          carrying the member, mode, the scheduled time, and the live lock
          state at decision time, so the API/panel can show what WOULD have been
          locked. Issues NO ``lock.lock``, performs NO verify, and raises NO
          failure alert (nothing was attempted). Degrades gracefully if the
          member entity is missing (state -> "unknown").
        - Inputs: zone (Zone), entity_id (str), mode (str), cfg
          (ScheduledAutoLock carrying the configured ``time``).
        - Outputs: the appended outcome dict.
        """
        state = self.hass.states.get(entity_id)
        last_state = (state.state if state is not None else "unknown") or "unknown"
        scheduled_time = str(getattr(cfg, "time", "") or "")
        record: Dict[str, Any] = {
            "timestamp": datetime.now().isoformat(),
            "zone_id": zone.zone_id,
            "zone_name": zone.name,
            "member_entity_id": entity_id,
            "door_name": self._friendly_name(entity_id),
            "mode": mode,
            "result": "would_lock",
            "attempts": 0,
            "method": "observe",
            "state": str(last_state).lower(),
            "scheduled_time": scheduled_time,
        }
        self.records.append(record)
        if len(self.records) > MAX_RECORDS:
            self.records = self.records[-MAX_RECORDS:]
        _LOGGER.info(
            "AutoLockEngine OBSERVE: WOULD %s auto-lock %s (zone '%s') "
            "scheduled=%s state=%s — no lock.lock issued",
            mode,
            record["door_name"],
            zone.name,
            scheduled_time or "(idle)",
            record["state"],
        )
        return record

    def _raise_failure_alert(
        self, zone: Zone, entity_id: str, mode: str, attempts: int, state: str
    ) -> None:
        """Route a final auto-lock failure to the AlertEngine (CRIT).

        - Description: Surfaces the failure into the SAME alert log + DRY-RUN
          notify stream the detectors use, via ``AlertEngine.record_external``.
          A no-op (logged) if the alert engine is somehow absent.
        - Inputs: zone (Zone), entity_id (str), mode (str), attempts (int),
          state (str final lock state).
        - Outputs: None.
        """
        message = (
            f"{mode} auto-lock FAILED after {attempts} attempt(s) "
            f"(final state '{state}') — physical check needed"
        )
        engine = self.hass.data.get(ALERT_ENGINE_KEY)
        if engine is None:
            _LOGGER.error(
                "AutoLockEngine: no AlertEngine to route failure for %s: %s",
                entity_id,
                message,
            )
            return
        engine.record_external(entity_id, ALERT_AUTO_LOCK_FAILED, SEV_CRIT, message)


def _dig_bolt_status(resp: Any) -> Optional[str]:
    """Dig a ``boltStatus`` value out of an ``invoke_cc_api`` response.

    - Description: Port of the pyscript response-shape dig — the result may be a
      flat dict carrying ``boltStatus`` or a dict keyed by entity_id whose
      values carry it.
    - Inputs: resp (any invoke_cc_api response).
    - Outputs: str lowercased boltStatus, or None.
    """
    if not isinstance(resp, dict):
        return None
    candidates: List[Dict[str, Any]] = []
    if "boltStatus" in resp:
        candidates.append(resp)
    for value in resp.values():
        if isinstance(value, dict) and "boltStatus" in value:
            candidates.append(value)
    for candidate in candidates:
        bolt = candidate.get("boltStatus")
        if bolt is not None:
            return str(bolt).strip().lower()
    return None
