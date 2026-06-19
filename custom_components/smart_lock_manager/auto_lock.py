"""AUTO-LOCK engine for Smart Lock Manager (Phase 4c, dev-gated).

This folds the legacy office/home auto-lock pyscripts onto the zone model. It
runs two modes per zone, driven entirely by the zone's ``settings``:

* **Scheduled COB** (port of ``office/.../automations/lock_doors.py``) — at a
  configured ``time`` on configured ``days`` it locks every member with a
  verify + retry loop (up to ``max_attempts``, ``settle_seconds`` apart),
  verifying via Door Lock CC (98) ``boltStatus`` when ``verify_boltstatus`` is
  set, else a heuristic on lock state + jam sensor. Per-member failure
  isolation; a final failure is routed through the existing alert/notify path
  (CRIT) so it shows in Dev Alerts + dry-run notify intents.
* **Idle** (port of ``home/.../home_autolock_front_door.yaml``) — N minutes
  after a member unlocks it auto-locks (same verify/retry). When ``sun_aware``
  it uses ``night_minutes`` after dusk / ``day_minutes`` in daytime, computed
  from ``sun.sun`` ``next_dusk`` / ``next_dawn``. The timer is cancelled/reset
  on re-lock or re-unlock.

Two ORTHOGONAL layers of gating make this safe to run beside the live pyscripts:

* **MODE-GATED construction** (Phase 4d). The engine is instantiated when
  ``is_dev_mock() OR engines_enabled()`` (see :func:`.gating.engines_active` and
  ``async_setup_entry`` in ``__init__.py``), exactly like
  :class:`~.alert_engine.AlertEngine`. With both flags off (production default)
  the class is never built, so production behaviour is 100% unchanged. Under
  ``SLM_ENABLE_ENGINES`` (dev-mock off) it runs in PROD OBSERVE against the real
  office zones.
* **Execution gating** (INDEPENDENT of construction). Issuing an ACTUAL
  ``lock.lock`` is permitted only when :func:`is_dev_mock` is true (the lock
  entities are dev template locks backed by ``input_boolean`` — never hardware)
  OR the single explicit env flag :data:`REAL_AUTOLOCK_ENV`
  (``SLM_ENABLE_REAL_AUTOLOCK``, default OFF) is set. When NEITHER holds the
  engine is in OBSERVE posture: it RECORDS a "would auto-lock" intent (members +
  scheduled time/mode) for parity and issues NO ``lock.lock``, and it does NOT
  read boltStatus / verify (nothing was locked). This phase ships the real flag
  OFF, so in PROD OBSERVE the engine records intents only and cannot double-lock
  a real door alongside the live pyscripts.

The engine only acts on zones whose corresponding mode is ``enabled`` in
settings — it respects the opt-in and never auto-locks a zone that did not.

SECURITY: auto-lock records carry entity ids, door names, severities and
human-readable messages only — never PIN codes.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime
from typing import Any, Callable, Dict, List, Tuple

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.event import (
    async_call_later,
    async_track_state_change_event,
    async_track_time_change,
)
from homeassistant.util import dt as dt_util

from .auto_lock_verify import (  # noqa: F401 - re-exported for backward compat
    ALERT_AUTO_LOCK_FAILED,
    DOOR_LOCK_CC,
    MAX_RECORDS,
    AutoLockVerifyMixin,
    _dig_bolt_status,
)
from .const import DOMAIN
from .dev_mock import MOCK_BOLT, is_dev_mock
from .gating import current_engine_mode
from .models.zone import Zone
from .zone_runtime import get_zone_for_lock, get_zone_registry

_LOGGER = logging.getLogger(__name__)

# hass.data key holding the single per-process AutoLockEngine instance.
AUTO_LOCK_ENGINE_KEY = f"{DOMAIN}_auto_lock_engine"

# --- Execution gating -------------------------------------------------------
# The ONE explicit env flag that could let this engine issue real lock commands
# in PRODUCTION. Default OFF. In dev (SLM_DEV_MOCK) the engine drives the dummy
# template locks regardless, so the full verify/retry path is provable without
# this flag — but no real hardware is ever touched there either.
REAL_AUTOLOCK_ENV = "SLM_ENABLE_REAL_AUTOLOCK"

# Mode identifiers used by the dev trigger service + records.
MODE_SCHEDULED = "scheduled"
MODE_IDLE = "idle"


def real_autolock_enabled() -> bool:
    """Return whether the explicit real-autolock flag is truthy.

    - Description: Reads :data:`REAL_AUTOLOCK_ENV`. This is the ONLY switch that
      could enable issuing real ``lock.lock`` commands in production; it
      defaults OFF.
    - Inputs: none (reads process environment).
    - Outputs: bool — True only when the flag is explicitly truthy.
    """
    raw = os.environ.get(REAL_AUTOLOCK_ENV, "")
    return raw.strip().lower() in ("1", "true", "yes", "on")


class AutoLockEngine(AutoLockVerifyMixin):
    """Per-process AUTO-LOCK driver for all zone member locks (dev-gated).

    Created ONCE under ``SLM_DEV_MOCK`` and stored at
    ``hass.data[AUTO_LOCK_ENGINE_KEY]``. Owns the daily COB time triggers, the
    idle unlock listener, and the per-member idle timers across every zone. The
    shared lock + verify + retry + recording path lives on
    :class:`~.auto_lock_verify.AutoLockVerifyMixin`.

    Attributes:
        hass: the HomeAssistant instance.
        records: rolling list of auto-lock outcome dicts (most-recent last).
    """

    def __init__(self, hass: HomeAssistant) -> None:
        """Initialize an empty, unstarted engine.

        - Inputs: hass (HomeAssistant).
        - Outputs: None.
        """
        self.hass = hass
        self.records: List[Dict[str, Any]] = []
        # Active subscriptions / timers, released on async_stop().
        self._unsubs: List[Callable[[], None]] = []
        self._idle_timers: Dict[str, Callable[[], None]] = {}
        self._started = False

    # -- lifecycle ----------------------------------------------------------

    async def async_start(self) -> None:
        """Schedule COB triggers and subscribe to member unlocks.

        - Description: For every zone with ``scheduled_auto_lock.enabled`` arm a
          daily ``async_track_time_change`` at the zone's configured time. Then
          subscribe one state-change listener over every member of every zone
          with ``idle_auto_lock.enabled`` to start the idle timers. Idempotent.
        - Inputs: none (reads the zone registry).
        - Outputs: None.
        """
        if self._started:
            return
        self._schedule_all_cob()
        self._subscribe_idle()
        self._started = True
        # File-aware reader (lazy import, mirrors _may_execute) so the logged
        # real_flag matches the value that actually gates execution — env OR
        # the flags-file ``real_autolock`` key, not the env var alone.
        from .gating import real_autolock_enabled as file_aware_real_autolock

        _LOGGER.info(
            "AutoLockEngine started: mode=%s real_exec=%s (dev_mock=%s, real_flag=%s)",
            current_engine_mode(),
            self._may_execute(),
            is_dev_mock(),
            file_aware_real_autolock(),
        )

    @callback
    def async_stop(self) -> None:
        """Release all time triggers, listeners and pending idle timers.

        - Inputs: none.
        - Outputs: None.
        """
        self._teardown_subscriptions()
        self._started = False
        _LOGGER.info("AutoLockEngine stopped")

    @callback
    def _teardown_subscriptions(self) -> None:
        """Cancel every COB trigger, idle listener and pending idle timer.

        - Description: Releases all ``async_track_time_change`` /
          ``async_track_state_change_event`` handles and every pending idle
          ``async_call_later`` timer. Leaves ``_started`` and the recorded
          ``records`` untouched so the engine can be re-subscribed without
          losing history. Safe to call repeatedly — handle collections are
          cleared as they are released.
        - Inputs: none.
        - Outputs: None.
        """
        for unsub in self._unsubs:
            unsub()
        self._unsubs.clear()
        for cancel in list(self._idle_timers.values()):
            cancel()
        self._idle_timers.clear()

    @callback
    def async_refresh(self) -> None:
        """Rebuild COB schedules + idle subscriptions from current settings.

        - Description: Called when a zone's settings change so enabling
          ``idle_auto_lock`` or ``scheduled_auto_lock`` (or editing the COB
          time / member set) takes effect WITHOUT an HA restart. Tears down the
          existing time triggers, idle listener and pending idle timers, then
          re-arms COB triggers and re-subscribes the idle listener over the
          current enabled members. Idempotent: old handles are cancelled first
          so no duplicate timers/listeners accumulate across repeated edits.
          A no-op if the engine never started.
        - Inputs: none (reads the zone registry).
        - Outputs: None.
        """
        if not self._started:
            return
        self._teardown_subscriptions()
        self._schedule_all_cob()
        self._subscribe_idle()
        _LOGGER.info("AutoLockEngine refreshed: COB + idle subscriptions rebuilt")

    # -- execution gating ---------------------------------------------------

    def _may_execute(self) -> bool:
        """Return whether issuing a real ``lock.lock`` is permitted.

        - Description: Permitted only in dev (the dummy template locks are not
          hardware) OR when the explicit :data:`REAL_AUTOLOCK_ENV` flag is set.
          With both off the engine records the intended action but issues NO
          ``lock.lock`` service call — the production-safe default.
        - Inputs: none.
        - Outputs: bool.
        """
        # File-aware reader imported LAZILY here (mirrors gating's own lazy
        # import of this module's env reader) to avoid a circular import at
        # module load. This OR-combines the SLM_ENABLE_REAL_AUTOLOCK env var
        # with the flags-file ``real_autolock`` key, so HA OS (no settable env)
        # can enable real auto-lock via the file — same as the notify path.
        from .gating import real_autolock_enabled as file_aware_real_autolock

        return is_dev_mock() or file_aware_real_autolock()

    # -- scheduled COB ------------------------------------------------------

    def _schedule_all_cob(self) -> None:
        """Arm a daily time trigger for every COB-enabled zone.

        - Inputs: none (reads the zone registry).
        - Outputs: None.
        """
        for zone in get_zone_registry(self.hass).values():
            cfg = zone.settings.scheduled_auto_lock
            if not cfg.enabled:
                continue
            hour, minute = _parse_hhmm(cfg.time)
            zone_id = zone.zone_id

            @callback
            def _fire(_now: datetime, _zone_id: str = zone_id) -> None:
                self.hass.async_create_task(self._run_cob(_zone_id))

            self._unsubs.append(
                async_track_time_change(
                    self.hass, _fire, hour=hour, minute=minute, second=0
                )
            )
            _LOGGER.info(
                "AutoLockEngine: COB scheduled for zone '%s' at %02d:%02d days=%s",
                zone.name,
                hour,
                minute,
                cfg.days,
            )

    async def _run_cob(self, zone_id: str) -> None:
        """Run the COB lockdown for one zone, honouring its day gate.

        - Description: For each member, attempt the verify+retry lock with
          per-member failure isolation. Skips entirely if today is not in the
          zone's configured ``days``.
        - Inputs: zone_id (str).
        - Outputs: None.
        """
        zone = get_zone_registry(self.hass).get(zone_id)
        if zone is None:
            return
        cfg = zone.settings.scheduled_auto_lock
        if not cfg.enabled:
            return
        if datetime.now().weekday() not in (cfg.days or []):
            _LOGGER.info(
                "AutoLockEngine: COB skipped for zone '%s' (not a scheduled day)",
                zone.name,
            )
            return
        _LOGGER.info("AutoLockEngine: COB lockdown for zone '%s'", zone.name)
        for entity_id in zone.member_lock_entity_ids:
            await self._secure_member(zone, entity_id, MODE_SCHEDULED, cfg)

    # -- idle ---------------------------------------------------------------

    def _subscribe_idle(self) -> None:
        """Subscribe a single state listener over all idle-enabled members.

        - Inputs: none (reads the zone registry).
        - Outputs: None.
        """
        entities: List[str] = []
        seen: set = set()
        for zone in get_zone_registry(self.hass).values():
            if not zone.settings.idle_auto_lock.enabled:
                continue
            for entity_id in zone.member_lock_entity_ids:
                if entity_id not in seen:
                    seen.add(entity_id)
                    entities.append(entity_id)
        if entities:
            self._unsubs.append(
                async_track_state_change_event(
                    self.hass, entities, self._handle_idle_event
                )
            )
            _LOGGER.info(
                "AutoLockEngine: idle auto-lock watching %d member(s)", len(entities)
            )

    @callback
    def _handle_idle_event(self, event: Any) -> None:
        """Start/cancel a member's idle timer on its lock state changes.

        - Description: On ``unlocked`` (re)start the per-member idle timer; on
          any non-unlocked state cancel a pending timer (re-lock or going
          unavailable). Mirrors the home YAML start/cancel automations.
        - Inputs: event (HA state_changed Event).
        - Outputs: None.
        """
        entity_id = event.data.get("entity_id", "")
        new_state = event.data.get("new_state")
        if new_state is None:
            return
        value = (new_state.state or "unknown").lower()
        zone = get_zone_for_lock(self.hass, entity_id)
        if zone is None or not zone.settings.idle_auto_lock.enabled:
            return

        if value == "unlocked":
            self._start_idle_timer(zone, entity_id)
        else:
            self._cancel_idle_timer(entity_id)

    def _idle_delay_seconds(self, zone: Zone) -> int:
        """Resolve the idle delay in seconds for a zone (sun-aware capable).

        - Description: When ``sun_aware`` is False, returns ``minutes`` * 60.
          When True, mirrors the home YAML: if ``next_dusk`` is sooner than
          ``next_dawn`` it is night -> ``night_minutes``, else day ->
          ``day_minutes``. Falls back to ``minutes`` when ``sun.sun`` is absent.
        - Inputs: zone (Zone).
        - Outputs: int seconds.
        """
        cfg = zone.settings.idle_auto_lock
        if not cfg.sun_aware:
            return max(0, cfg.minutes) * 60
        minutes = self._sun_aware_minutes(
            cfg.night_minutes, cfg.day_minutes, cfg.minutes
        )
        return max(0, minutes) * 60

    def _sun_aware_minutes(
        self, night_minutes: int, day_minutes: int, fallback_minutes: int
    ) -> int:
        """Pick night vs day minutes from ``sun.sun`` next_dusk/next_dawn.

        - Description: Port of the home YAML duration template. Night when the
          next dusk arrives before the next dawn. Falls back to
          ``fallback_minutes`` if the sun entity or its attributes are missing.
        - Inputs: night_minutes (int), day_minutes (int), fallback_minutes (int).
        - Outputs: int minutes.
        """
        sun = self.hass.states.get("sun.sun")
        if sun is None:
            return fallback_minutes
        dusk = sun.attributes.get("next_dusk")
        dawn = sun.attributes.get("next_dawn")
        dusk_dt = dt_util.parse_datetime(str(dusk)) if dusk else None
        dawn_dt = dt_util.parse_datetime(str(dawn)) if dawn else None
        if dusk_dt is None or dawn_dt is None:
            return fallback_minutes
        # Night when dusk comes first (mirrors: dusk - dawn < 0).
        return night_minutes if dusk_dt < dawn_dt else day_minutes

    def _start_idle_timer(self, zone: Zone, entity_id: str) -> None:
        """Arm (or re-arm) the idle auto-lock timer for one member.

        - Inputs: zone (Zone owning the member), entity_id (str).
        - Outputs: None.
        """
        self._cancel_idle_timer(entity_id)
        delay = self._idle_delay_seconds(zone)
        zone_id = zone.zone_id

        @callback
        def _expire(_now: datetime) -> None:
            self._idle_timers.pop(entity_id, None)
            self.hass.async_create_task(self._on_idle_expired(zone_id, entity_id))

        self._idle_timers[entity_id] = async_call_later(self.hass, delay, _expire)
        _LOGGER.info("AutoLockEngine: idle timer armed for %s (%ds)", entity_id, delay)

    async def _on_idle_expired(self, zone_id: str, entity_id: str) -> None:
        """Auto-lock a member whose idle timer expired, if still unlocked.

        - Inputs: zone_id (str), entity_id (str).
        - Outputs: None.
        """
        zone = get_zone_registry(self.hass).get(zone_id)
        if zone is None or not zone.settings.idle_auto_lock.enabled:
            return
        state = self.hass.states.get(entity_id)
        if state is None or (state.state or "").lower() != "unlocked":
            return  # re-locked or changed in the meantime; nothing to do.
        # Reuse the COB verify/retry settings for the lock attempt.
        cfg = zone.settings.scheduled_auto_lock
        await self._secure_member(zone, entity_id, MODE_IDLE, cfg)

    def _cancel_idle_timer(self, entity_id: str) -> None:
        """Cancel a pending idle timer for a member, if any.

        - Inputs: entity_id (str).
        - Outputs: None.
        """
        cancel = self._idle_timers.pop(entity_id, None)
        if cancel:
            cancel()

    # -- read API -----------------------------------------------------------

    def serialize(self) -> List[Dict[str, Any]]:
        """Return the auto-lock outcome records most-recent-first.

        - Inputs: none.
        - Outputs: list of outcome dicts.
        """
        return list(reversed(self.records))

    # -- dev triggers -------------------------------------------------------

    async def dev_trigger(
        self, zone_id: str, mode: str, fail_verify: bool = False
    ) -> None:
        """Force a COB run or an idle-expiry NOW for a zone (dev-only).

        - Description: DEV entrypoint for the ``dev_trigger_autolock`` service.
          ``scheduled`` runs the COB lockdown immediately (bypassing the time +
          day gate). ``idle`` fires the idle-expiry path for every member.
          ``fail_verify`` forces every member's boltStatus to ``"unlocked"`` so
          verify fails and the retry + CRIT-alert path is exercised; the
          override is cleared afterward.
        - Inputs: zone_id (str), mode (str scheduled|idle), fail_verify (bool).
        - Outputs: None.
        """
        zone = get_zone_registry(self.hass).get(zone_id)
        if zone is None:
            _LOGGER.warning("dev_trigger_autolock: unknown zone_id %s", zone_id)
            return

        forced: List[str] = []
        if fail_verify:
            for entity_id in zone.member_lock_entity_ids:
                MOCK_BOLT.set_override(entity_id, "unlocked")
                forced.append(entity_id)

        try:
            if mode == MODE_SCHEDULED:
                cfg = zone.settings.scheduled_auto_lock
                for entity_id in zone.member_lock_entity_ids:
                    await self._secure_member(zone, entity_id, MODE_SCHEDULED, cfg)
            elif mode == MODE_IDLE:
                for entity_id in zone.member_lock_entity_ids:
                    await self._on_idle_expired(zone_id, entity_id)
            else:
                _LOGGER.warning("dev_trigger_autolock: unknown mode %s", mode)
        finally:
            for entity_id in forced:
                MOCK_BOLT.set_override(entity_id, None)


# --- module helpers ---------------------------------------------------------


def _parse_hhmm(value: str) -> Tuple[int, int]:
    """Parse an ``HH:MM`` string into ``(hour, minute)``, defaulting 17:30.

    - Inputs: value (str like "17:30").
    - Outputs: tuple(int hour, int minute).
    """
    try:
        hour, minute = (int(part) for part in str(value).split(":", 1))
        return hour, minute
    except (TypeError, ValueError, AttributeError):
        return 17, 30
