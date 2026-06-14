"""DRY-RUN notification layer for the Smart Lock Manager alert engine (Phase 4b).

This module turns a recorded alert (from the OBSERVE-ONLY :mod:`alert_engine`)
into notification *intents* and, in a future production build, into real sends.
It is deliberately constrained for this phase:

* **DRY-RUN is the DEFAULT and is forced ON under ``SLM_DEV_MOCK``.** In dry-run
  the layer NEVER hits SMTP2GO and NEVER calls a ``notify`` /
  ``persistent_notification`` service. It only LOGS the fully-rendered payload
  and RETURNS a structured "would-notify" intent so the dev API/panel can show
  exactly what *would* have been sent. This guarantees it can run alongside the
  user's live pyscripts without ever double-notifying.
* **One explicit real-send flag.** Real sending is gated by the single env var
  :data:`REAL_NOTIFY_ENV` (``SLM_ENABLE_REAL_NOTIFY``), which defaults OFF.
  Real sending additionally requires that dry-run is NOT forced (i.e. NOT under
  ``SLM_DEV_MOCK``). So in dev nothing ever sends, and in production nothing
  sends until that flag is explicitly turned on. This phase ships it OFF.

* **Byte-compatible email format.** The SMTP2GO request shape and subject line
  exactly mirror the user's ``lib_email`` pyscript module: subject
  ``[fleet/internal/<kind>] <marker> <subject>`` with severity markers, kind
  routing (alerts use ``kind="alert"`` -> base ``smtp2go_to`` plus
  ``smtp2go_alert_to``), STARTTLS to ``mail.smtp2go.com:587``. This makes parity
  comparison against the pyscripts trivial.

SECURITY: notification payloads carry door names, severities, human messages and
recipient addresses (non-secret user config) only — never PIN codes.
"""

from __future__ import annotations

import logging
import os
import smtplib
from dataclasses import dataclass
from email.mime.text import MIMEText
from email.utils import formatdate, make_msgid
from typing import Any, Dict, List, Optional

import yaml  # type: ignore[import-untyped]
from homeassistant.core import HomeAssistant

from .models.zone_settings import EmailNotify, MobileNotify, ZoneNotify

_LOGGER = logging.getLogger(__name__)

# --- Real-send gating -------------------------------------------------------
# The ONE explicit flag that would ever enable real sending. Default OFF. Even
# when set, real sending is suppressed whenever dry-run is forced (dev mock).
REAL_NOTIFY_ENV = "SLM_ENABLE_REAL_NOTIFY"

# --- lib_email parity constants (replicated EXACTLY) ------------------------
SECRETS_FILENAME = "secrets.yaml"
SMTP_HOST = "mail.smtp2go.com"
SMTP_PORT = 587
SMTP_TIMEOUT = 15  # seconds

# Severity -> visual marker (empty string means "no marker"). Mirrors
# lib_email._MARKERS exactly so subjects are byte-compatible.
_MARKERS = {
    "CRIT": "🔴",
    "ERROR": "🔴",
    "WARN": "🟡",
    "HEALTHY-RECOVERY": "🟢",
    "INFO": "ℹ️",
}

# Channel identifiers used in intent records.
CHANNEL_EMAIL = "email"
CHANNEL_MOBILE = "mobile"


def real_send_enabled() -> bool:
    """Return whether the explicit real-send flag is truthy.

    - Description: Reads :data:`REAL_NOTIFY_ENV`. This is the ONLY switch that
      could enable real sending later; it defaults OFF. Callers still suppress
      real sending whenever dry-run is forced (see :class:`NotificationDispatcher`).
    - Inputs: none (reads process environment).
    - Outputs: bool — True only when the flag is explicitly truthy.
    """
    raw = os.environ.get(REAL_NOTIFY_ENV, "")
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _format_subject(severity: str, subject: str, kind: str) -> str:
    """Wrap a subject as ``[fleet/internal/<kind>] <marker?> <subject>``.

    - Description: Byte-for-byte port of ``lib_email._format_subject``. The
      ``daily`` kind never gets a marker; every other kind gets the severity
      marker when one is defined.
    - Inputs: severity (str), subject (str), kind (str).
    - Outputs: str subject line.
    """
    prefix = f"[fleet/internal/{kind}]"
    marker = "" if kind == "daily" else _MARKERS.get(severity.upper(), "")
    if marker:
        return f"{prefix} {marker} {subject}"
    return f"{prefix} {subject}"


def _load_secrets_sync(secrets_path: str) -> Optional[Dict[str, Any]]:
    """Read SMTP2GO creds from ``secrets.yaml`` (BLOCKING file IO).

    - Description: Mirrors ``lib_email._load_secrets`` — base creds plus the
      per-kind extra-recipient lists (here only ``alert`` is needed). MUST be
      run in the executor, never the event loop. Returns None if a required
      base credential is missing.
    - Inputs: secrets_path (str absolute path to secrets.yaml).
    - Outputs: dict {user, pass, from, to, kind_to: {alert: [...]}} or None.
    """
    try:
        with open(secrets_path, "r", encoding="utf-8") as handle:
            secrets = yaml.safe_load(handle) or {}
    except (OSError, yaml.YAMLError) as exc:
        _LOGGER.error("notifications: failed to read %s: %s", secrets_path, exc)
        return None

    creds: Dict[str, Any] = {
        "user": secrets.get("smtp2go_user"),
        "pass": secrets.get("smtp2go_pass"),
        "from": secrets.get("smtp2go_from"),
        "to": secrets.get("smtp2go_to"),
    }
    missing = [k for k, v in creds.items() if not v]
    if missing:
        _LOGGER.error("notifications: missing SMTP2GO secrets: %s", missing)
        return None

    kind_to: Dict[str, List[str]] = {}
    for kind in ("alert", "daily", "info", "test"):
        raw = secrets.get(f"smtp2go_{kind}_to") or ""
        kind_to[kind] = [a.strip() for a in str(raw).split(",") if a.strip()]
    creds["kind_to"] = kind_to
    return creds


def _dedup_preserve(addresses: List[str]) -> List[str]:
    """Return ``addresses`` de-duplicated case-insensitively, order preserved.

    - Inputs: addresses (list[str]).
    - Outputs: list[str] with original casing, first occurrence wins.
    """
    seen: set = set()
    out: List[str] = []
    for addr in addresses:
        if not addr:
            continue
        key = addr.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(addr)
    return out


@dataclass
class RenderedEmail:
    """A fully-rendered email payload ready to send or record as an intent.

    Attributes:
        severity: WARN / CRIT / HEALTHY-RECOVERY etc.
        kind: subject-prefix segment (always ``alert`` for SLM alerts).
        subject: the wrapped ``[fleet/internal/...]`` subject line.
        body: plain-text body.
        recipients: final envelope recipient list (base + alert + override).
    """

    severity: str
    kind: str
    subject: str
    body: str
    recipients: List[str]


class EmailNotifier:
    """Builds (and, only when real-send is enabled, dispatches) SMTP2GO emails.

    Replicates the user's ``lib_email`` send EXACTLY: same SMTP host/port,
    STARTTLS, subject/marker format and ``kind="alert"`` recipient routing. In
    DRY-RUN it renders + logs the payload and returns an intent without touching
    the network.
    """

    def __init__(self, hass: HomeAssistant) -> None:
        """Initialize the notifier and cache the secrets path.

        - Inputs: hass (HomeAssistant).
        - Outputs: None.
        """
        self.hass = hass
        self._secrets_path = hass.config.path(SECRETS_FILENAME)
        self._creds_cache: Optional[Dict[str, Any]] = None

    async def _creds(self) -> Optional[Dict[str, Any]]:
        """Return the cached SMTP2GO creds, loading them via the executor once.

        - Inputs: none.
        - Outputs: creds dict or None (when secrets are missing/unreadable).
        """
        if self._creds_cache is None:
            self._creds_cache = await self.hass.async_add_executor_job(
                _load_secrets_sync, self._secrets_path
            )
        return self._creds_cache

    def _resolve_recipients(
        self, creds: Dict[str, Any], kind: str, override: List[str]
    ) -> List[str]:
        """Build the final To: list (base + kind extras + zone override).

        - Description: Mirrors ``lib_email._resolve_recipients`` (base
          ``smtp2go_to`` first, then the kind-specific list) and additionally
          appends the zone's ``recipients_override`` so per-zone targeting is
          honoured. De-duplicated, order-preserving.
        - Inputs: creds (dict), kind (str), override (list[str] zone override).
        - Outputs: list[str] of recipient addresses.
        """
        base = [creds["to"]] if creds.get("to") else []
        extras = (creds.get("kind_to") or {}).get(kind, []) or []
        return _dedup_preserve(base + extras + list(override or []))

    async def render(
        self,
        severity: str,
        subject: str,
        body: str,
        recipients_override: List[str],
        kind: str = "alert",
    ) -> Optional[RenderedEmail]:
        """Render a full email payload from secrets + the lib_email format.

        - Inputs: severity (str), subject (str), body (str),
          recipients_override (list[str] from the zone), kind (str).
        - Outputs: RenderedEmail, or None when creds/recipients are unavailable.
        """
        creds = await self._creds()
        if not creds:
            return None
        recipients = self._resolve_recipients(creds, kind, recipients_override)
        if not recipients:
            _LOGGER.error(
                "notifications: no email recipients resolved for kind=%s", kind
            )
            return None
        sev = (severity or "").upper().strip()
        return RenderedEmail(
            severity=sev,
            kind=kind,
            subject=_format_subject(sev, subject, kind),
            body=body,
            recipients=recipients,
        )

    def _build_mime(self, creds: Dict[str, Any], email: RenderedEmail) -> MIMEText:
        """Build the MIMEText message (parity with ``lib_email._build_message``).

        - Inputs: creds (dict), email (RenderedEmail).
        - Outputs: MIMEText ready for ``sendmail``.
        """
        msg = MIMEText(email.body, "plain", "utf-8")
        msg["From"] = creds["from"]
        msg["To"] = ", ".join(email.recipients)
        msg["Subject"] = email.subject
        msg["Date"] = formatdate(localtime=True)
        msg["Message-ID"] = make_msgid(domain="ha.local")
        msg["X-HA-Severity"] = email.severity
        msg["X-HA-Kind"] = email.kind
        return msg

    def _smtp_send(self, creds: Dict[str, Any], email: RenderedEmail) -> bool:
        """Connect to SMTP2GO, STARTTLS, login and send (parity with lib_email).

        - Description: Only ever reached when real-send is explicitly enabled
          AND dry-run is not forced. Never called in dev/dry-run.
        - Inputs: creds (dict), email (RenderedEmail).
        - Outputs: bool — True on success.
        """
        msg = self._build_mime(creds, email)
        try:
            with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=SMTP_TIMEOUT) as smtp:
                smtp.ehlo()
                smtp.starttls()
                smtp.ehlo()
                smtp.login(creds["user"], creds["pass"])
                smtp.sendmail(creds["from"], email.recipients, msg.as_string())
            return True
        except (smtplib.SMTPException, OSError) as exc:
            _LOGGER.error("notifications: SMTP send failed: %s", exc)
            return False

    async def send_real(self, email: RenderedEmail) -> bool:
        """Actually send a rendered email via SMTP2GO (executor).

        - Description: The ONLY path that touches the network. Callers must have
          already confirmed real-send is enabled and dry-run is not forced.
        - Inputs: email (RenderedEmail).
        - Outputs: bool — True on success, False otherwise.
        """
        creds = await self._creds()
        if not creds:
            return False
        result = await self.hass.async_add_executor_job(self._smtp_send, creds, email)
        return bool(result)


class MobileNotifier:
    """Builds (and, only when real-send is enabled, dispatches) mobile pushes.

    In DRY-RUN it renders + logs the intent and returns it without calling any
    ``notify`` / ``persistent_notification`` service.
    """

    def __init__(self, hass: HomeAssistant) -> None:
        """Initialize the mobile notifier.

        - Inputs: hass (HomeAssistant).
        - Outputs: None.
        """
        self.hass = hass

    @staticmethod
    def resolve_targets(targets: List[str]) -> List[str]:
        """Return the configured ``notify.*`` targets (fallback to persistent).

        - Description: Empty config falls back to ``persistent_notification`` so
          an enabled-but-untargeted zone still produces a visible intent.
        - Inputs: targets (list[str] notify service names, e.g. ``mobile_app_x``).
        - Outputs: list[str] of resolved targets.
        """
        resolved = _dedup_preserve(list(targets or []))
        return resolved or ["persistent_notification"]

    async def send_real(self, target: str, title: str, message: str) -> bool:
        """Call the real HA notify service for one target (never in dry-run).

        - Description: Calls ``notify.<target>`` for mobile-app targets, or
          ``persistent_notification.create`` for the persistent fallback. Only
          reached when real-send is explicitly enabled and dry-run is not forced.
        - Inputs: target (str), title (str), message (str).
        - Outputs: bool — True if the service call was dispatched.
        """
        try:
            if target == "persistent_notification":
                await self.hass.services.async_call(
                    "persistent_notification",
                    "create",
                    {"title": title, "message": message},
                    blocking=False,
                )
            else:
                await self.hass.services.async_call(
                    "notify",
                    target,
                    {"title": title, "message": message},
                    blocking=False,
                )
            return True
        except Exception as exc:  # noqa: BLE001 - service errors must never crash
            _LOGGER.error("notifications: mobile send failed for %s: %s", target, exc)
            return False


class NotificationDispatcher:
    """Routes a recorded alert to email and/or mobile per the zone's config.

    DRY-RUN is the default and is FORCED whenever ``dry_run`` is True (the engine
    passes ``is_dev_mock()``). In dry-run the dispatcher renders every enabled
    channel, LOGS the payload, and returns the intents — it never sends. Real
    sending requires BOTH the explicit :data:`REAL_NOTIFY_ENV` flag AND
    ``dry_run`` being False.
    """

    def __init__(self, hass: HomeAssistant, dry_run: bool) -> None:
        """Initialize the dispatcher.

        - Inputs: hass (HomeAssistant), dry_run (bool — force dry-run when True).
        - Outputs: None.
        """
        self.hass = hass
        self.dry_run = dry_run
        self.email = EmailNotifier(hass)
        self.mobile = MobileNotifier(hass)

    def _should_really_send(self) -> bool:
        """Return True only when real sending is permitted.

        - Description: Requires the explicit real-send flag AND that dry-run is
          not forced. In dev (``dry_run`` True) this is always False.
        - Inputs: none.
        - Outputs: bool.
        """
        return (not self.dry_run) and real_send_enabled()

    @staticmethod
    def _email_subject(alert: Dict[str, Any]) -> str:
        """Build the human (pre-wrap) email subject from an alert record.

        - Inputs: alert (dict alert record from the engine).
        - Outputs: str subject (e.g. ``SLM Front North: Lock jammed``).
        """
        door = alert.get("door_name") or alert.get("member_entity_id") or "lock"
        message = alert.get("message") or alert.get("alert_type") or "alert"
        return f"SLM {door}: {message}"

    @staticmethod
    def _body(alert: Dict[str, Any]) -> str:
        """Build a plain-text notification body from an alert record (no PINs).

        - Inputs: alert (dict alert record).
        - Outputs: str multi-line body.
        """
        lines = [
            f"Zone:      {alert.get('zone_name') or '(unhomed)'}",
            f"Door:      {alert.get('door_name') or alert.get('member_entity_id')}",
            f"Type:      {alert.get('alert_type')}",
            f"Severity:  {alert.get('severity')}",
            f"Recovery:  {bool(alert.get('is_recovery'))}",
            f"Message:   {alert.get('message')}",
            f"Time:      {alert.get('timestamp')}",
        ]
        return "\n".join(lines)

    @staticmethod
    def _severity_for(alert: Dict[str, Any]) -> str:
        """Return the lib_email severity token for an alert (recovery-aware).

        - Description: Recovery records always use ``HEALTHY-RECOVERY`` (green
          marker); otherwise the alert's own severity (WARN/CRIT) is used.
        - Inputs: alert (dict alert record).
        - Outputs: str severity token.
        """
        if alert.get("is_recovery"):
            return "HEALTHY-RECOVERY"
        return str(alert.get("severity") or "INFO").upper()

    async def dispatch(
        self, alert: Dict[str, Any], notify: ZoneNotify
    ) -> List[Dict[str, Any]]:
        """Route one alert to enabled channels; return the intent list.

        - Description: For each enabled channel, renders the payload, logs it,
          records a structured intent, and (only when real-send is permitted)
          dispatches it. In dry-run NOTHING is sent. The returned intents are
          attached to the alert record so the API/panel can show them.
        - Inputs: alert (dict alert record), notify (ZoneNotify zone config).
        - Outputs: list of intent dicts ``{channel, recipients|targets, subject,
          dry_run}``.
        """
        intents: List[Dict[str, Any]] = []
        severity = self._severity_for(alert)
        subject = self._email_subject(alert)
        body = self._body(alert)

        if notify.email.enabled:
            intent = await self._dispatch_email(
                alert, notify.email, severity, subject, body
            )
            if intent is not None:
                intents.append(intent)

        if notify.mobile.enabled:
            intents.append(
                await self._dispatch_mobile(alert, notify.mobile, subject, body)
            )

        return intents

    async def _dispatch_email(
        self,
        alert: Dict[str, Any],
        email_cfg: EmailNotify,
        severity: str,
        subject: str,
        body: str,
    ) -> Optional[Dict[str, Any]]:
        """Render + (dry-run) record / (real) send the email channel.

        - Inputs: alert (dict), email_cfg (EmailNotify), severity (str),
          subject (str), body (str).
        - Outputs: intent dict, or None when the payload could not be rendered.
        """
        rendered = await self.email.render(
            severity, subject, body, email_cfg.recipients_override, kind="alert"
        )
        if rendered is None:
            return None

        sent = False
        if self._should_really_send():
            sent = await self.email.send_real(rendered)
        else:
            _LOGGER.info(
                "notifications DRY-RUN email (no send): subject=%r -> %s",
                rendered.subject,
                ", ".join(rendered.recipients),
            )
        return {
            "channel": CHANNEL_EMAIL,
            "recipients": rendered.recipients,
            "subject": rendered.subject,
            "severity": rendered.severity,
            "dry_run": not self._should_really_send(),
            "sent": sent,
        }

    async def _dispatch_mobile(
        self,
        alert: Dict[str, Any],
        mobile_cfg: MobileNotify,
        subject: str,
        body: str,
    ) -> Dict[str, Any]:
        """Render + (dry-run) record / (real) send the mobile channel.

        - Inputs: alert (dict), mobile_cfg (MobileNotify), subject (str),
          body (str).
        - Outputs: intent dict.
        """
        targets = MobileNotifier.resolve_targets(mobile_cfg.targets)
        sent = False
        if self._should_really_send():
            results = [
                await self.mobile.send_real(target, subject, body) for target in targets
            ]
            sent = any(results)
        else:
            _LOGGER.info(
                "notifications DRY-RUN mobile (no send): title=%r -> %s",
                subject,
                ", ".join(targets),
            )
        return {
            "channel": CHANNEL_MOBILE,
            "targets": targets,
            "subject": subject,
            "dry_run": not self._should_really_send(),
            "sent": sent,
        }
