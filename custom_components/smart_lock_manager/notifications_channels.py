"""SMTP2GO email + mobile-push channel notifiers for the SLM alert engine.

Split out of :mod:`.notifications` (which exceeded the 500-line limit) so the
dispatcher module stays lean. This module owns the byte-compatible ``lib_email``
parity machinery: the ``secrets.yaml`` reader, the fleet subject wrapper, the
:class:`RenderedEmail` payload, and the two channel notifiers
(:class:`EmailNotifier`, :class:`MobileNotifier`). All of these only ever touch
the network when real-send is explicitly enabled — the dispatcher gates that.

SECURITY: payloads carry door names, severities, human messages and recipient
addresses (non-secret user config) only — never PIN codes.
"""

from __future__ import annotations

import logging
import smtplib
from dataclasses import dataclass, field
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formatdate, make_msgid
from typing import Any, Dict, List, Optional

from homeassistant.core import HomeAssistant

from .notifications_config import load_smtp_creds_sync

_LOGGER = logging.getLogger(__name__)

# --- lib_email parity constants (replicated EXACTLY) ------------------------
SECRETS_FILENAME = "secrets.yaml"
HOST_TAG_FILENAME = ".ha_host_tag"
SMTP_TIMEOUT = 15  # seconds

# Back-compat alias: the cred loader moved to :mod:`.notifications_config`
# (generic-first slm_smtp_* with smtp2go_* fallback). Re-exported here under the
# historical name so existing tests/mocks that patch
# ``notifications_channels._load_secrets_sync`` keep working, and so
# :meth:`EmailNotifier._creds` resolves a module-level name tests can patch.
_load_secrets_sync = load_smtp_creds_sync

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


def _format_subject(
    severity: str, subject: str, kind: str, prefix: Optional[str] = None
) -> str:
    """Format a neutral subject line, with an optional caller-supplied prefix.

    - Description: The default output is ``f"{marker} {subject}"`` — just the
      severity marker (when one is defined) and the subject; the ``daily`` kind
      never carries a marker. The legacy ``[fleet/internal/<kind>]`` wrapper is
      GONE so a stranger's install reads cleanly. An optional ``prefix`` is
      prepended when truthy (e.g. an office bracketed tag), yielding
      ``f"{prefix} {marker} {subject}"`` (or ``f"{prefix} {subject}"`` when no
      marker applies).
    - Inputs: severity (str), subject (str), kind (str), prefix (Optional[str]
      — a caller-supplied lead, e.g. a bracketed routing tag; None => none).
    - Outputs: str subject line.
    - Example: ``_format_subject("WARN", "lock unlocked", "alert")`` ->
      ``"🟡 lock unlocked"``.
    """
    marker = "" if kind == "daily" else _MARKERS.get(severity.upper(), "")
    if prefix:
        return f"{prefix} {marker} {subject}" if marker else f"{prefix} {subject}"
    return f"{marker} {subject}" if marker else subject


def _read_host_tag_sync(host_tag_path: str) -> Optional[str]:
    """Read the host tag from ``.ha_host_tag`` (BLOCKING file IO).

    - Description: Mirrors lib_email._hostname_tag's file read. MUST run in the
      executor, never the event loop. Returns None if missing/empty so the
      footer can gracefully omit the host.
    - Inputs: host_tag_path (str absolute path to .ha_host_tag).
    - Outputs: str host tag (e.g. 'ha-office'), or None.
    """
    try:
        with open(host_tag_path, "r", encoding="utf-8") as handle:
            tag = handle.read().strip()
    except OSError:
        return None
    return tag or None


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
        subject: the wrapped ``[fleet/internal/...]`` subject line (the email
            Subject header).
        clean_subject: the human subject BEFORE fleet-wrapping and BEFORE any
            marker — fed to the HTML card so its heading carries exactly one
            marker (the card prepends it) and no ``[fleet/internal/...]`` prefix.
        body: plain-text body.
        recipients: final envelope recipient list (the non-empty zone override
            verbatim, else base ``smtp2go_to`` + the kind-specific extras).
        body_lines: the un-joined body lines fed to the HTML card renderer (so
            each becomes its own row); derived from ``body`` when not supplied.
        host_tag: the footer host label (e.g. ``ha-office``), or None when the
            ``.ha_host_tag`` file is absent so the footer omits the host.
        actor: the "Triggered by" label (e.g. ``Theresa (keypad, slot 2)``), or
            None when attribution found no in-window access-log entry — the HTML
            card omits the line cleanly when falsy.
    """

    severity: str
    kind: str
    subject: str
    body: str
    recipients: List[str]
    clean_subject: str = ""
    body_lines: List[str] = field(default_factory=list)
    host_tag: Optional[str] = None
    actor: Optional[str] = None


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
        self._host_tag_cache: Optional[str] = None
        self._host_tag_loaded = False

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

    async def _host_tag(self) -> Optional[str]:
        """Return the cached host tag, loading it via the executor once.

        - Description: Lazily reads ``.ha_host_tag`` once (executor), caching the
          result — including a None (missing file) — so the footer host label is
          resolved at most one file read per process. Mirrors :meth:`_creds`.
        - Inputs: none.
        - Outputs: str host tag, or None when the file is missing/empty.
        """
        if not self._host_tag_loaded:
            self._host_tag_cache = await self.hass.async_add_executor_job(
                _read_host_tag_sync, self.hass.config.path(HOST_TAG_FILENAME)
            )
            self._host_tag_loaded = True
        return self._host_tag_cache

    def _resolve_recipients(
        self, creds: Dict[str, Any], kind: str, override: List[str]
    ) -> List[str]:
        """Build the final To: list, honouring a non-empty zone override.

        - Description: A NON-EMPTY ``recipients_override`` REPLACES the base
          routing entirely — the email goes to EXACTLY those addresses (base
          ``smtp2go_to`` and the kind-specific extras are NOT appended), so a
          zone can be retargeted to its own recipients. When the override is
          empty / None it falls back to ``lib_email._resolve_recipients``
          parity: base ``smtp2go_to`` first, then the kind-specific list.
          Addresses are trimmed; blanks are dropped; the result is
          de-duplicated, order-preserving.
        - Inputs: creds (dict), kind (str), override (list[str] zone override).
        - Outputs: list[str] of recipient addresses.
        """
        cleaned_override = [a.strip() for a in (override or []) if a and a.strip()]
        if cleaned_override:
            return _dedup_preserve(cleaned_override)
        base = [creds["to"]] if creds.get("to") else []
        extras = (creds.get("kind_to") or {}).get(kind, []) or []
        return _dedup_preserve(base + extras)

    async def render(
        self,
        severity: str,
        subject: str,
        body: str,
        recipients_override: List[str],
        kind: str = "alert",
        body_lines: Optional[List[str]] = None,
        actor: Optional[str] = None,
    ) -> Optional[RenderedEmail]:
        """Render a full email payload from secrets + the lib_email format.

        - Inputs: severity (str), subject (str), body (str),
          recipients_override (list[str] from the zone), kind (str), body_lines
          (optional pre-split lines for the HTML card; derived from ``body`` by
          splitting on newlines when None), actor (optional "Triggered by" label
          for the HTML card).
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
        host_tag = await self._host_tag()
        lines = body_lines if body_lines is not None else body.split("\n")
        sev = (severity or "").upper().strip()
        return RenderedEmail(
            severity=sev,
            kind=kind,
            subject=_format_subject(sev, subject, kind),
            body=body,
            recipients=recipients,
            clean_subject=subject,
            body_lines=lines,
            host_tag=host_tag,
            actor=actor,
        )

    def _build_mime(self, creds: Dict[str, Any], email: RenderedEmail) -> MIMEMultipart:
        """Build a multipart/alternative message (plain + styled HTML card).

        - Description: Parity with lib_email but upgraded to multipart: a text/plain
          part (the legacy body, unchanged) AND a text/html part rendering the
          shared alert card via render_alert_html. The plain part stays first so
          non-HTML clients fall back to it.
        - Inputs: creds (dict), email (RenderedEmail).
        - Outputs: MIMEMultipart('alternative') ready for sendmail.
        """
        # Imported LAZILY to avoid a circular import at module load:
        # notifications_html imports _MARKERS from this module.
        from .notifications_html import render_alert_html

        msg = MIMEMultipart("alternative")
        msg["From"] = creds["from"]
        msg["To"] = ", ".join(email.recipients)
        msg["Subject"] = email.subject
        msg["Date"] = formatdate(localtime=True)
        msg["Message-ID"] = make_msgid(domain="ha.local")
        msg["X-HA-Severity"] = email.severity
        msg["X-HA-Kind"] = email.kind
        # email.actor carries the "Triggered by" label resolved by access-log
        # attribution (None when no in-window event matched); the renderer omits
        # the line cleanly when actor is falsy.
        # Pass the CLEAN human subject (pre-fleet-wrap, pre-marker) so the card
        # heading reads "<marker> <subject>" — the renderer prepends the single
        # marker. email.subject (the wrapped header) would double-stamp the
        # marker and leak the [fleet/internal/...] prefix into the heading.
        html = render_alert_html(
            severity=email.severity,
            subject=email.clean_subject,
            body_lines=email.body_lines,
            host_tag=email.host_tag,
            actor=email.actor,
            timestamp=None,
        )
        msg.attach(MIMEText(email.body, "plain", "utf-8"))
        msg.attach(MIMEText(html, "html", "utf-8"))
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
            host = creds.get("host") or "mail.smtp2go.com"
            port = int(creds.get("port") or 587)
            with smtplib.SMTP(host, port, timeout=SMTP_TIMEOUT) as smtp:
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
