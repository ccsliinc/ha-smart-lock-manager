"""Per-zone operational settings for Smart Lock Manager (Phase 4a, backend).

This module defines the structured, JSON-round-trippable config blocks that fold
the legacy office/home pyscript behaviour (business hours, scheduled COB
auto-lock, idle auto-lock, alerting, notification) onto the :class:`~.zone.Zone`
data model. The blocks live here (not in ``zone.py``) so the Zone module stays
under the 500-line limit and the settings schema has one clear home.

Design contract:

* **Every block defaults to a sensible, OFF-by-default value.** A zone persisted
  BEFORE these fields existed hydrates cleanly: :func:`settings_from_dict`
  tolerates a wholly-absent ``settings`` key and any missing sub-key, filling in
  defaults. ``to_dict`` always emits the full nested shape so the round-trip is
  stable.
* **Defaults MIRROR the legacy pyscript thresholds** (08:30-17:30 Mon-Fri
  business window, 17:30 COB lock, 3 attempts / 5s settle, 15/30/45s sustained
  tiers, 20% low-battery) so that flipping a zone's ``enabled`` flag reproduces
  the historical behaviour exactly — no magic numbers scattered elsewhere.
* **``enabled`` defaults to ``False`` everywhere.** Nothing changes for a zone
  until the user opts in via the (future) settings UI. The dev observe-only
  :mod:`alert_engine` reconciles this by treating "absent / unconfigured" as
  "use these mirror defaults and keep observing" — see ``alert_engine`` docs.

No raw PIN material is ever stored in these blocks; they are operational config
only and safe to return over the read API.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List

# --- Mirror-the-pyscript default constants ---------------------------------
# Single source of truth for the legacy thresholds. The alert_engine imports
# these so the observe-only engine and the zone defaults can never drift.

DEFAULT_OPEN_TIME = "08:30"
DEFAULT_CLOSE_TIME = "17:30"
DEFAULT_WEEKDAYS: List[int] = [0, 1, 2, 3, 4]  # Mon-Fri (0=Mon .. 6=Sun)
DEFAULT_WORKDAY_ENTITY = "binary_sensor.workday_sensor"

DEFAULT_AUTOLOCK_TIME = "17:30"
DEFAULT_MAX_ATTEMPTS = 3
DEFAULT_SETTLE_SECONDS = 5

DEFAULT_IDLE_MINUTES = 5
DEFAULT_IDLE_NIGHT_MINUTES = 15
DEFAULT_IDLE_DAY_MINUTES = 5

DEFAULT_SUSTAINED_TIERS: List[int] = [15, 30, 45]
DEFAULT_LOW_BATTERY_THRESHOLD = 20
DEFAULT_SEVERITY_CRIT = "CRIT"


def _weekdays() -> List[int]:
    """Return a fresh Mon-Fri day list (avoid shared mutable default)."""
    return list(DEFAULT_WEEKDAYS)


def _sustained_tiers() -> List[int]:
    """Return a fresh sustained-tier list (avoid shared mutable default)."""
    return list(DEFAULT_SUSTAINED_TIERS)


@dataclass
class BusinessHours:
    """Business-hours window driving outside-hours evaluation.

    Mirrors the legacy ``workday_sensor`` + 08:30-17:30 gate. When
    ``use_workday_sensor`` is True the named ``binary_sensor`` (state ``on``)
    AND the open/close window decide "open"; otherwise day-of-week membership in
    ``days`` AND the window decide it.
    """

    enabled: bool = False
    open_time: str = DEFAULT_OPEN_TIME
    close_time: str = DEFAULT_CLOSE_TIME
    days: List[int] = field(default_factory=_weekdays)
    use_workday_sensor: bool = False
    workday_entity: str = DEFAULT_WORKDAY_ENTITY


@dataclass
class ScheduledAutoLock:
    """COB-style scheduled lockdown config (port of ``lock_doors.py``)."""

    enabled: bool = False
    time: str = DEFAULT_AUTOLOCK_TIME
    days: List[int] = field(default_factory=_weekdays)
    max_attempts: int = DEFAULT_MAX_ATTEMPTS
    settle_seconds: int = DEFAULT_SETTLE_SECONDS
    verify_boltstatus: bool = True


@dataclass
class IdleAutoLock:
    """Idle-timeout auto-lock config (port of the home auto-lock YAML)."""

    enabled: bool = False
    minutes: int = DEFAULT_IDLE_MINUTES
    sun_aware: bool = False
    night_minutes: int = DEFAULT_IDLE_NIGHT_MINUTES
    day_minutes: int = DEFAULT_IDLE_DAY_MINUTES


@dataclass
class OutsideHoursAlert:
    """Outside-business-hours unlock alert config."""

    enabled: bool = False
    severity: str = DEFAULT_SEVERITY_CRIT


@dataclass
class SustainedUnlockAlert:
    """Sustained-unlock escalation alert config (tiers in seconds)."""

    enabled: bool = False
    tiers: List[int] = field(default_factory=_sustained_tiers)


@dataclass
class JamAlert:
    """Jam / lock-failure alert config."""

    enabled: bool = False
    severity: str = DEFAULT_SEVERITY_CRIT


@dataclass
class LowBatteryAlert:
    """Low-battery alert config (percent threshold)."""

    enabled: bool = False
    threshold: int = DEFAULT_LOW_BATTERY_THRESHOLD


@dataclass
class OfflineAlert:
    """Offline / unavailable member alert config."""

    enabled: bool = False


@dataclass
class ZoneAlerts:
    """All per-zone alert toggles + thresholds."""

    outside_hours: OutsideHoursAlert = field(default_factory=OutsideHoursAlert)
    sustained_unlock: SustainedUnlockAlert = field(default_factory=SustainedUnlockAlert)
    jam: JamAlert = field(default_factory=JamAlert)
    low_battery: LowBatteryAlert = field(default_factory=LowBatteryAlert)
    offline: OfflineAlert = field(default_factory=OfflineAlert)


@dataclass
class EmailNotify:
    """Email (SMTP2GO) notification channel config.

    ``recipients_override`` is a list of plain email addresses the user typed;
    empty means "fall back to the default routing". These are non-secret
    operational targets and are safe to expose over the read API.
    """

    enabled: bool = False
    recipients_override: List[str] = field(default_factory=list)


@dataclass
class MobileNotify:
    """HA mobile-app / persistent_notification channel config.

    ``targets`` is a list of ``notify.*`` service targets; empty means "fall
    back to the default targets". Non-secret operational config.
    """

    enabled: bool = False
    targets: List[str] = field(default_factory=list)


@dataclass
class ZoneNotify:
    """Per-zone notification channel config (email + mobile)."""

    email: EmailNotify = field(default_factory=EmailNotify)
    mobile: MobileNotify = field(default_factory=MobileNotify)


@dataclass
class MemberMeta:
    """Per-member companion-entity overrides for the health detectors.

    Real-world Z-Wave locks expose jam and battery companion entities whose ids
    do NOT follow the auto-discovery convention
    (``binary_sensor.<object_id>_jammed`` / ``sensor.<object_id>_battery``).
    These explicit overrides — keyed by member ``entity_id`` in
    :attr:`ZoneSettings.member_meta` — are the SOURCE OF TRUTH the jam and
    low-battery detectors resolve FIRST, falling back to auto-discovery only
    when a field is empty. Both default to empty (auto-discovery preserved).

    Non-secret operational config (entity ids only) — safe over the read API.
    """

    jam_sensor: str = ""
    battery_entity: str = ""


@dataclass
class ZoneSettings:
    """The full per-zone operational settings bundle.

    Aggregates the five config blocks the Phase-4a settings editor manages.
    Constructed with all-default (inert) blocks; :func:`settings_from_dict`
    rebuilds it tolerantly from persisted JSON (any missing block/key falls back
    to its default), and :meth:`to_dict` emits the complete nested shape.
    """

    business_hours: BusinessHours = field(default_factory=BusinessHours)
    scheduled_auto_lock: ScheduledAutoLock = field(default_factory=ScheduledAutoLock)
    idle_auto_lock: IdleAutoLock = field(default_factory=IdleAutoLock)
    alerts: ZoneAlerts = field(default_factory=ZoneAlerts)
    notify: ZoneNotify = field(default_factory=ZoneNotify)
    # Per-member companion-entity overrides, keyed by member entity_id. The
    # health detectors (jam / low_battery) resolve these FIRST and fall back to
    # auto-discovery when a field is empty. Defaults to no overrides.
    member_meta: Dict[str, MemberMeta] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """Serialize the full settings bundle to a JSON-safe nested dict.

        - Outputs: dict with every block present (stable round-trip shape).
        """
        return {
            "business_hours": asdict(self.business_hours),
            "scheduled_auto_lock": asdict(self.scheduled_auto_lock),
            "idle_auto_lock": asdict(self.idle_auto_lock),
            "alerts": asdict(self.alerts),
            "notify": asdict(self.notify),
            "member_meta": {
                entity_id: asdict(meta) for entity_id, meta in self.member_meta.items()
            },
        }


# --- tolerant rebuild helpers ----------------------------------------------
# Each helper accepts whatever (possibly partial / None) sub-dict was persisted
# and returns a fully-populated dataclass, defaulting any absent key. This is
# what makes pre-existing zones (no ``settings`` key at all) hydrate cleanly.


def _as_dict(value: Any) -> Dict[str, Any]:
    """Return ``value`` if it is a dict, else an empty dict.

    - Inputs: value (any) — a persisted sub-block that may be missing/None.
    - Outputs: dict (never None) so ``.get`` calls below are always safe.
    """
    return value if isinstance(value, dict) else {}


def _bool(value: Any, default: bool) -> bool:
    """Coerce a persisted value to bool, falling back to ``default``."""
    return bool(value) if value is not None else default


def _int(value: Any, default: int) -> int:
    """Coerce a persisted value to int, falling back to ``default`` on error."""
    if value is None:
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _str(value: Any, default: str) -> str:
    """Coerce a persisted value to str, falling back to ``default``."""
    return str(value) if value not in (None, "") else default


def _int_list(value: Any, default: List[int]) -> List[int]:
    """Coerce a persisted value to a list[int], falling back to ``default``.

    - Inputs: value (any), default (list[int]) used when value is missing/bad.
    - Outputs: a fresh list of ints.
    """
    if not isinstance(value, list):
        return list(default)
    out: List[int] = []
    for item in value:
        try:
            out.append(int(item))
        except (TypeError, ValueError):
            continue
    return out


def _str_list(value: Any) -> List[str]:
    """Coerce a persisted value to a list[str] (empty when missing/bad)."""
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if item not in (None, "")]


def _business_hours_from(data: Any) -> BusinessHours:
    """Rebuild :class:`BusinessHours` tolerantly from a persisted dict."""
    d = _as_dict(data)
    return BusinessHours(
        enabled=_bool(d.get("enabled"), False),
        open_time=_str(d.get("open_time"), DEFAULT_OPEN_TIME),
        close_time=_str(d.get("close_time"), DEFAULT_CLOSE_TIME),
        days=_int_list(d.get("days"), DEFAULT_WEEKDAYS),
        use_workday_sensor=_bool(d.get("use_workday_sensor"), False),
        workday_entity=_str(d.get("workday_entity"), DEFAULT_WORKDAY_ENTITY),
    )


def _scheduled_from(data: Any) -> ScheduledAutoLock:
    """Rebuild :class:`ScheduledAutoLock` tolerantly from a persisted dict."""
    d = _as_dict(data)
    return ScheduledAutoLock(
        enabled=_bool(d.get("enabled"), False),
        time=_str(d.get("time"), DEFAULT_AUTOLOCK_TIME),
        days=_int_list(d.get("days"), DEFAULT_WEEKDAYS),
        max_attempts=_int(d.get("max_attempts"), DEFAULT_MAX_ATTEMPTS),
        settle_seconds=_int(d.get("settle_seconds"), DEFAULT_SETTLE_SECONDS),
        verify_boltstatus=_bool(d.get("verify_boltstatus"), True),
    )


def _idle_from(data: Any) -> IdleAutoLock:
    """Rebuild :class:`IdleAutoLock` tolerantly from a persisted dict."""
    d = _as_dict(data)
    return IdleAutoLock(
        enabled=_bool(d.get("enabled"), False),
        minutes=_int(d.get("minutes"), DEFAULT_IDLE_MINUTES),
        sun_aware=_bool(d.get("sun_aware"), False),
        night_minutes=_int(d.get("night_minutes"), DEFAULT_IDLE_NIGHT_MINUTES),
        day_minutes=_int(d.get("day_minutes"), DEFAULT_IDLE_DAY_MINUTES),
    )


def _alerts_from(data: Any) -> ZoneAlerts:
    """Rebuild :class:`ZoneAlerts` tolerantly from a persisted dict."""
    d = _as_dict(data)
    oh = _as_dict(d.get("outside_hours"))
    su = _as_dict(d.get("sustained_unlock"))
    jam = _as_dict(d.get("jam"))
    lb = _as_dict(d.get("low_battery"))
    off = _as_dict(d.get("offline"))
    return ZoneAlerts(
        outside_hours=OutsideHoursAlert(
            enabled=_bool(oh.get("enabled"), False),
            severity=_str(oh.get("severity"), DEFAULT_SEVERITY_CRIT),
        ),
        sustained_unlock=SustainedUnlockAlert(
            enabled=_bool(su.get("enabled"), False),
            tiers=_int_list(su.get("tiers"), DEFAULT_SUSTAINED_TIERS),
        ),
        jam=JamAlert(
            enabled=_bool(jam.get("enabled"), False),
            severity=_str(jam.get("severity"), DEFAULT_SEVERITY_CRIT),
        ),
        low_battery=LowBatteryAlert(
            enabled=_bool(lb.get("enabled"), False),
            threshold=_int(lb.get("threshold"), DEFAULT_LOW_BATTERY_THRESHOLD),
        ),
        offline=OfflineAlert(enabled=_bool(off.get("enabled"), False)),
    )


def _notify_from(data: Any) -> ZoneNotify:
    """Rebuild :class:`ZoneNotify` tolerantly from a persisted dict."""
    d = _as_dict(data)
    email = _as_dict(d.get("email"))
    mobile = _as_dict(d.get("mobile"))
    return ZoneNotify(
        email=EmailNotify(
            enabled=_bool(email.get("enabled"), False),
            recipients_override=_str_list(email.get("recipients_override")),
        ),
        mobile=MobileNotify(
            enabled=_bool(mobile.get("enabled"), False),
            targets=_str_list(mobile.get("targets")),
        ),
    )


def _member_meta_from(data: Any) -> Dict[str, MemberMeta]:
    """Rebuild the per-member companion-entity overrides from persisted JSON.

    - Description: Accepts the persisted ``member_meta`` mapping (entity_id ->
      sub-dict) and returns a dict of :class:`MemberMeta`, defaulting each
      field to an empty string. Tolerant of a wholly-absent / malformed map (a
      zone persisted before this field existed yields an empty dict — i.e.
      auto-discovery for every member).
    - Inputs: data (any) — the persisted ``member_meta`` blob or None.
    - Outputs: dict[str, MemberMeta].
    """
    d = _as_dict(data)
    out: Dict[str, MemberMeta] = {}
    for entity_id, meta in d.items():
        m = _as_dict(meta)
        out[str(entity_id)] = MemberMeta(
            jam_sensor=_str(m.get("jam_sensor"), ""),
            battery_entity=_str(m.get("battery_entity"), ""),
        )
    return out


def settings_from_dict(data: Any) -> ZoneSettings:
    """Rebuild a :class:`ZoneSettings` from persisted JSON, tolerantly.

    - Description: Accepts the ``settings`` value stored in a zone dict — which
      may be entirely absent/None (a zone persisted before Phase 4a) or a
      partial dict — and returns a fully-populated :class:`ZoneSettings` with
      every missing field defaulted. This is the backward-compat contract.
    - Inputs: data (any) — the persisted ``settings`` blob or None.
    - Outputs: ZoneSettings.
    """
    d = _as_dict(data)
    return ZoneSettings(
        business_hours=_business_hours_from(d.get("business_hours")),
        scheduled_auto_lock=_scheduled_from(d.get("scheduled_auto_lock")),
        idle_auto_lock=_idle_from(d.get("idle_auto_lock")),
        alerts=_alerts_from(d.get("alerts")),
        notify=_notify_from(d.get("notify")),
        member_meta=_member_meta_from(d.get("member_meta")),
    )


# Top-level block names accepted by the update_zone_settings service. Each maps
# to a ``ZoneSettings`` attribute; the merge in the service is per-block so an
# unspecified block is never clobbered. ``member_meta`` is a keyed map (entity
# id -> override sub-dict) rather than a flat block, but the same per-block deep
# merge applies — a partial update touches only the named members' sub-dicts.
SETTINGS_BLOCKS = (
    "business_hours",
    "scheduled_auto_lock",
    "idle_auto_lock",
    "alerts",
    "notify",
    "member_meta",
)


def merge_settings(current: ZoneSettings, updates: Dict[str, Any]) -> ZoneSettings:
    """Return a new ZoneSettings with ``updates`` block(s) merged into current.

    - Description: Partial-update merge at BLOCK granularity. Any block named in
      ``updates`` is fully rebuilt from the merge of its current serialized form
      and the supplied sub-dict (so partial sub-keys within a named block are
      also honoured); blocks NOT named in ``updates`` are preserved verbatim.
    - Inputs: current (ZoneSettings), updates (dict of block_name -> sub-dict).
    - Outputs: a fresh ZoneSettings (does not mutate ``current``).
    """
    base = current.to_dict()
    for block in SETTINGS_BLOCKS:
        if block in updates and isinstance(updates[block], dict):
            merged_block = _deep_merge(base.get(block, {}), updates[block])
            base[block] = merged_block
    return settings_from_dict(base)


def _deep_merge(base: Dict[str, Any], overlay: Dict[str, Any]) -> Dict[str, Any]:
    """Recursively merge ``overlay`` onto a copy of ``base``.

    - Description: Nested dicts merge key-by-key; any non-dict value in
      ``overlay`` replaces the base value. Used so a partial alert update (e.g.
      only ``sustained_unlock.tiers``) does not wipe sibling alert toggles.
    - Inputs: base (dict), overlay (dict).
    - Outputs: a new merged dict (inputs untouched).
    """
    out = dict(base)
    for key, value in overlay.items():
        if key in out and isinstance(out[key], dict) and isinstance(value, dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out
