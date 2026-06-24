"""Pyscript-parity subject AND body builders for SLM alert notifications.

Split out of :mod:`.notifications` (which exceeded the 500-line limit) so the
notification dispatcher stays lean. This module owns the two data-driven
builder families that turn a recorded alert record into the exact email
SUBJECT and BODY text:

* **Subjects** build a concise, self-labeled subject line per alert type
  (keyed on the member entity id). The subject prefix is derived from the
  install's Home Assistant location name. The fleet wrapper + severity marker
  are applied separately by :func:`.notifications_channels._format_subject`.
* **Bodies** give each alert type a small, recovery-aware multi-line body so
  the dev/parity panel and any real send carry a human-readable description
  (door friendly name, state, timestamps, severity / percent / detail).

Both families are dispatch dicts keyed by ``alert_type`` (single source of
truth — add a row, not an if/else). SECURITY: bodies/subjects carry door
names, severities, human messages and timestamps only — never PIN codes.
"""

from __future__ import annotations

import re
from typing import Any, Callable, Dict, List

# Default subject prefix when no Home Assistant location name is available.
# The live prefix is normally derived from ``hass.config.location_name`` (see
# :func:`subject_prefix_for`) so each install's alert subjects are self-labeled.
_DEFAULT_SUBJECT_PREFIX = "Home Assistant -"

# Extracts the elapsed seconds out of a sustained-unlock message body
# ("Unlocked >15s without re-lock" / "...(dev-simulated)").
_SECONDS_RE = re.compile(r">(\d+)s")

# Extracts the battery percent out of a low-battery message body
# ("Battery low (8%)" / "Battery recovered (30%)").
_PERCENT_RE = re.compile(r"\((\d+)%\)")


def _entity_of(alert: Dict[str, Any]) -> str:
    """Return the member entity id the pyscripts key subjects on.

    - Inputs: alert (dict alert record).
    - Outputs: str entity_id (falls back to door name then ``lock``).
    """
    return str(alert.get("member_entity_id") or alert.get("door_name") or "lock")


def _name_of(alert: Dict[str, Any]) -> str:
    """Return the friendly door name (used by the COB auto-lock subject).

    - Inputs: alert (dict alert record).
    - Outputs: str door name (falls back to entity id).
    """
    return str(alert.get("door_name") or alert.get("member_entity_id") or "lock")


def _first_int(pattern: "re.Pattern[str]", text: str, default: int) -> int:
    """Return the first integer matched by ``pattern`` in ``text``.

    - Inputs: pattern (compiled regex with one int group), text (str),
      default (int returned when no match).
    - Outputs: int.
    """
    match = pattern.search(text or "")
    return int(match.group(1)) if match else default


def _friendly_of(alert: Dict[str, Any]) -> str:
    """Return the human-friendly door label for body text.

    - Inputs: alert (dict alert record).
    - Outputs: str friendly_name (falls back to the door name helper).
    """
    return alert.get("friendly_name") or _name_of(alert)


def _last_changed_of(alert: Dict[str, Any]) -> str:
    """Return the entity's last-changed timestamp as a string.

    - Inputs: alert (dict alert record).
    - Outputs: str ISO timestamp (``"unknown"`` when absent).
    """
    return str(alert.get("last_changed") or "unknown")


def _iso_of(alert: Dict[str, Any]) -> str:
    """Return the alert record's own ISO timestamp as a string.

    - Inputs: alert (dict alert record).
    - Outputs: str ISO timestamp (empty string when absent).
    """
    return str(alert.get("timestamp") or "")


# --- Subject builders -------------------------------------------------------


def subject_prefix_for(location_name: Any) -> str:
    """Return the alert subject prefix derived from the HA location name.

    - Description: Each install labels its alert subjects with its own Home
      Assistant location name (``hass.config.location_name``) so a fleet of
      installs stays distinguishable in a shared mailbox. Falls back to
      :data:`_DEFAULT_SUBJECT_PREFIX` when the name is missing/blank.
    - Inputs: location_name (str | None — usually ``hass.config.location_name``).
    - Outputs: str prefix ending in a plain hyphen, e.g. ``"My Home -"``.
    - Example: ``subject_prefix_for("My Home") == "My Home -"``.
    """
    name = str(location_name or "").strip()
    if not name:
        return _DEFAULT_SUBJECT_PREFIX
    return f"{name} -"


def _subj_sustained(alert: Dict[str, Any], prefix: str) -> str:
    """Sustained-unlock subject.

    Alert:    ``{prefix} {entity} unlocked >{n}s``
    Recovery: ``{prefix} {entity} locked again``
    """
    entity = _entity_of(alert)
    if alert.get("is_recovery"):
        return f"{prefix} {entity} locked again"
    seconds = _first_int(_SECONDS_RE, str(alert.get("message")), 15)
    return f"{prefix} {entity} unlocked >{seconds}s"


def _subj_outside_hours(alert: Dict[str, Any], prefix: str) -> str:
    """Outside-hours subject.

    Alert:    ``{prefix} door {entity} unlocked outside business hours``
    Recovery: ``{prefix} {entity} locked again``
    """
    entity = _entity_of(alert)
    if alert.get("is_recovery"):
        return f"{prefix} {entity} locked again"
    return f"{prefix} door {entity} unlocked outside business hours"


def _subj_auto_lock_failed(alert: Dict[str, Any], prefix: str) -> str:
    """COB auto-lock failure subject.

    Alert: ``{prefix} {name} FAILED to auto-lock at COB``
    """
    return f"{prefix} {_name_of(alert)} FAILED to auto-lock at COB"


def _subj_jam(alert: Dict[str, Any], prefix: str) -> str:
    """Jam subject.

    Alert:    ``{prefix} {entity} jammed``
    Recovery: ``{prefix} {entity} jam cleared``
    """
    entity = _entity_of(alert)
    state = "jam cleared" if alert.get("is_recovery") else "jammed"
    return f"{prefix} {entity} {state}"


def _subj_low_battery(alert: Dict[str, Any], prefix: str) -> str:
    """Low-battery subject.

    Alert:    ``{prefix} {entity} battery low ({pct}%)``
    Recovery: ``{prefix} {entity} battery recovered ({pct}%)``
    """
    entity = _entity_of(alert)
    pct = _first_int(_PERCENT_RE, str(alert.get("message")), 0)
    state = "battery recovered" if alert.get("is_recovery") else "battery low"
    return f"{prefix} {entity} {state} ({pct}%)"


def _subj_offline(alert: Dict[str, Any], prefix: str) -> str:
    """Offline subject.

    Alert:    ``{prefix} {entity} offline``
    Recovery: ``{prefix} {entity} back online``
    """
    entity = _entity_of(alert)
    state = "back online" if alert.get("is_recovery") else "offline"
    return f"{prefix} {entity} {state}"


# alert_type -> subject builder. Single source of truth so the subject wording
# stays data-driven (add a row, not an if/else). Keys mirror the ``ALERT_*``
# ids in :mod:`.alert_detectors` / :mod:`.auto_lock_verify`.
_SUBJECT_BUILDERS: Dict[str, Callable[[Dict[str, Any], str], str]] = {
    "sustained_unlock": _subj_sustained,
    "outside_hours": _subj_outside_hours,
    "auto_lock_failed": _subj_auto_lock_failed,
    "jam": _subj_jam,
    "low_battery": _subj_low_battery,
    "offline": _subj_offline,
}


def build_alert_subject(alert: Dict[str, Any], prefix: str | None = None) -> str:
    """Build the (pre-wrap) email subject for an alert record.

    - Description: Dispatches on ``alert_type`` to the matching subject builder
      (see :data:`_SUBJECT_BUILDERS`). Unknown types fall back to a consistent
      ``{prefix} {entity} {message}`` line so a new alert type can never produce
      an empty/garbled subject. The fleet wrapper + severity marker are added
      afterwards by :func:`.notifications_channels._format_subject`.
    - Inputs: alert (dict alert record from the engine), prefix (subject prefix,
      normally from :func:`subject_prefix_for`; defaults to the generic prefix).
    - Outputs: str pre-wrap subject body.
    """
    if prefix is None:
        prefix = _DEFAULT_SUBJECT_PREFIX
    builder = _SUBJECT_BUILDERS.get(str(alert.get("alert_type")))
    if builder is not None:
        return builder(alert, prefix)
    entity = _entity_of(alert)
    message = alert.get("message") or alert.get("alert_type") or "alert"
    return f"{prefix} {entity} {message}"


# --- Body builders ----------------------------------------------------------


def _body_outside_hours(alert: Dict[str, Any]) -> List[str]:
    """Outside-hours body — recovery-aware multi-line text.

    - Inputs: alert (dict alert record).
    - Outputs: list[str] body lines.
    """
    friendly = _friendly_of(alert)
    entity = _entity_of(alert)
    iso = _iso_of(alert)
    last_changed = _last_changed_of(alert)
    if alert.get("is_recovery"):
        lines = [
            f"{friendly} ({entity}) was previously alerted as unlocked.",
            "Now showing state: locked.",
            f"Last unlocked at: {last_changed}",
            f"Recovery timestamp: {iso}",
            "Alert closed.",
        ]
    else:
        lines = [
            f"Lock: {friendly} ({entity})",
            "State: unlocked",
            f"Timestamp: {iso}",
            f"Last changed: {last_changed}",
        ]
    return lines


def _body_sustained(alert: Dict[str, Any]) -> List[str]:
    """Sustained-unlock body — recovery-aware multi-line text.

    - Inputs: alert (dict alert record).
    - Outputs: list[str] body lines.
    """
    friendly = _friendly_of(alert)
    entity = _entity_of(alert)
    iso = _iso_of(alert)
    last_changed = _last_changed_of(alert)
    if alert.get("is_recovery"):
        lines = [
            f"{friendly} ({entity}) is now locked again.",
            "Previously alerted as sustained-unlocked.",
            f"Last unlocked at: {last_changed}",
            f"Recovery timestamp: {iso}",
            "Alert closed.",
        ]
    else:
        seconds = _first_int(_SECONDS_RE, str(alert.get("message")), 15)
        severity = str(alert.get("severity"))
        lines = [
            f"Lock: {friendly} ({entity})",
            "State: unlocked",
            f"Elapsed: {seconds}s without re-lock",
            f"Severity: {severity}",
        ]
    return lines


def _body_auto_lock_failed(alert: Dict[str, Any]) -> List[str]:
    """COB auto-lock failure body — alert only (no recovery variant).

    - Inputs: alert (dict alert record).
    - Outputs: list[str] body lines.
    """
    friendly = _friendly_of(alert)
    entity = _entity_of(alert)
    iso = _iso_of(alert)
    message = str(alert.get("message"))
    lines = [
        f"Lock: {friendly} ({entity})",
        "State: failed to auto-lock",
        f"Detail: {message}",
        f"Timestamp: {iso}",
        "",
        "Physical check needed — the bolt could not be confirmed thrown.",
    ]
    return lines


def _body_jam(alert: Dict[str, Any]) -> List[str]:
    """Jam body — recovery-aware multi-line text.

    - Inputs: alert (dict alert record).
    - Outputs: list[str] body lines.
    """
    friendly = _friendly_of(alert)
    entity = _entity_of(alert)
    iso = _iso_of(alert)
    last_changed = _last_changed_of(alert)
    if alert.get("is_recovery"):
        lines = [
            f"{friendly} ({entity}) was previously alerted as jammed.",
            "Now showing state: jam cleared.",
            f"Recovery timestamp: {iso}",
            "Alert closed.",
        ]
    else:
        message = str(alert.get("message"))
        lines = [
            f"Lock: {friendly} ({entity})",
            "State: jammed",
            f"Detail: {message}",
            f"Timestamp: {iso}",
            f"Last changed: {last_changed}",
        ]
    return lines


def _body_low_battery(alert: Dict[str, Any]) -> List[str]:
    """Low-battery body — recovery-aware multi-line text.

    - Inputs: alert (dict alert record).
    - Outputs: list[str] body lines.
    """
    friendly = _friendly_of(alert)
    entity = _entity_of(alert)
    iso = _iso_of(alert)
    pct = _first_int(_PERCENT_RE, str(alert.get("message")), 0)
    message = str(alert.get("message"))
    if alert.get("is_recovery"):
        lines = [
            f"{friendly} ({entity}) battery recovered ({pct}%).",
            f"Detail: {message}",
            f"Recovery timestamp: {iso}",
            "Alert closed.",
        ]
    else:
        lines = [
            f"Lock: {friendly} ({entity})",
            f"State: battery low ({pct}%)",
            f"Detail: {message}",
            f"Timestamp: {iso}",
        ]
    return lines


def _body_offline(alert: Dict[str, Any]) -> List[str]:
    """Offline body — recovery-aware multi-line text.

    - Inputs: alert (dict alert record).
    - Outputs: list[str] body lines.
    """
    friendly = _friendly_of(alert)
    entity = _entity_of(alert)
    iso = _iso_of(alert)
    last_changed = _last_changed_of(alert)
    if alert.get("is_recovery"):
        lines = [
            f"{friendly} ({entity}) is back online.",
            f"Recovery timestamp: {iso}",
            "Alert closed.",
        ]
    else:
        message = str(alert.get("message"))
        lines = [
            f"Lock: {friendly} ({entity})",
            "State: offline",
            f"Detail: {message}",
            f"Timestamp: {iso}",
            f"Last changed: {last_changed}",
        ]
    return lines


# alert_type -> body builder. Mirrors :data:`_SUBJECT_BUILDERS` key-for-key so
# both families stay consistent (single source of truth, data-driven).
_BODY_BUILDERS: Dict[str, Callable[[Dict[str, Any]], List[str]]] = {
    "sustained_unlock": _body_sustained,
    "outside_hours": _body_outside_hours,
    "auto_lock_failed": _body_auto_lock_failed,
    "jam": _body_jam,
    "low_battery": _body_low_battery,
    "offline": _body_offline,
}


def build_alert_body_lines(alert: Dict[str, Any]) -> List[str]:
    """Build the email body as a list of lines for an alert record (no PINs).

    - Description: Dispatches on ``alert_type`` to the matching recovery-aware
      body builder (see :data:`_BODY_BUILDERS`), returning the lines un-joined so
      the HTML card can render each as its own row. Unknown types fall back to a
      generic house-style body so a new alert type can never produce an empty
      body.
    - Inputs: alert (dict alert record from the engine).
    - Outputs: list[str] body lines.
    """
    builder = _BODY_BUILDERS.get(str(alert.get("alert_type")))
    if builder is not None:
        return builder(alert)
    friendly = _friendly_of(alert)
    entity = _entity_of(alert)
    iso = _iso_of(alert)
    message = str(alert.get("message"))
    lines = [
        f"Lock: {friendly} ({entity})",
        f"State: {message}",
        f"Timestamp: {iso}",
    ]
    return lines


def build_alert_body(alert: Dict[str, Any]) -> str:
    """Build the plain-text email body for an alert record (no PINs).

    - Description: Dispatches on ``alert_type`` to the matching recovery-aware
      body builder (see :data:`_BODY_BUILDERS`). Unknown types fall back to a
      generic house-style body so a new alert type can never produce an empty
      body.
    - Inputs: alert (dict alert record from the engine).
    - Outputs: str newline-joined multi-line body.
    """
    return "\n".join(build_alert_body_lines(alert))
