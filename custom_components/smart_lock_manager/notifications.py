"""DRY-RUN notification layer for the Smart Lock Manager alert engine (Phase 4b).

Turns a recorded alert (from the OBSERVE-ONLY :mod:`alert_engine`) into
notification *intents* and, in a future production build, into real sends.

* **DRY-RUN is the DEFAULT and is forced ON under ``SLM_DEV_MOCK``.** In dry-run
  the layer NEVER hits SMTP2GO and NEVER calls a ``notify`` /
  ``persistent_notification`` service; it only LOGS the fully-rendered payload
  and RETURNS a structured "would-notify" intent for the API/panel, so it can
  run in OBSERVE without ever sending a real notification.
* **One explicit real-send flag.** Real sending is gated by the single env var
  :data:`REAL_NOTIFY_ENV` (``SLM_ENABLE_REAL_NOTIFY``, default OFF) AND requires
  that dry-run is NOT forced. So dev never sends, and prod stays silent until
  that flag is explicitly turned on. This phase ships it OFF.
* **Byte-compatible email format.** SMTP2GO request shape and subject line
  mirror the user's ``lib_email`` pyscript: subject
  ``[fleet/internal/<kind>] <marker> <subject>``, ``kind="alert"`` routing
  (base ``smtp2go_to`` + ``smtp2go_alert_to``), STARTTLS to
  ``mail.smtp2go.com:587`` — making parity comparison trivial.

SECURITY: payloads carry door names, severities, human messages and recipient
addresses (non-secret user config) only — never PIN codes.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional

from homeassistant.core import HomeAssistant

from .const import DOMAIN
from .models.zone_settings import EmailNotify, MobileNotify, ZoneNotify
from .notifications_bodies import (
    build_alert_body,
    build_alert_body_lines,
    build_alert_subject,
    subject_prefix_for,
)
from .notifications_channels import (
    CHANNEL_EMAIL,
    CHANNEL_MOBILE,
    EmailNotifier,
    MobileNotifier,
)

__all__ = ["build_alert_subject", "build_alert_body"]

_LOGGER = logging.getLogger(__name__)

# --- Real-send gating -------------------------------------------------------
# The ONE explicit flag that would ever enable real sending. Default OFF. Even
# when set, real sending is suppressed whenever dry-run is forced (dev mock).
REAL_NOTIFY_ENV = "SLM_ENABLE_REAL_NOTIFY"

# --- Subject/body builders --------------------------------------------------
# Alert subjects are keyed on the MEMBER ENTITY ID (e.g. ``lock.front_door``,
# NOT the zone display name) and prefixed with the install's Home Assistant
# location name so a fleet of installs stays distinguishable in a shared
# mailbox. The actual subject AND body builders live in
# :mod:`.notifications_bodies`, and the SMTP2GO / mobile channel notifiers +
# the fleet subject wrapper live in :mod:`.notifications_channels` (both split
# out to keep this module under the 500-line limit).
# ``build_alert_subject`` / ``build_alert_body`` are re-exported above so
# ``from .notifications import build_alert_subject`` (and ``...body``) both
# keep working.


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

        - Description: Requires the FILE-AWARE real-send flag AND that dry-run is
          not forced. In dev (``dry_run`` True) this is always False. The reader
          is :func:`..gating.real_notify_enabled` (env OR the flags-file
          ``real_notify`` key) — NOT the env-only :func:`real_send_enabled` — so
          HA OS (no settable env) can enable real sends via the file, mirroring
          :meth:`..auto_lock.AutoLockEngine._may_execute`.
        - Inputs: none.
        - Outputs: bool.
        """
        # Imported LAZILY here to avoid a circular import at module load (gating
        # imports this module's ``real_send_enabled``), mirroring auto_lock.
        from .gating import real_notify_enabled

        return (not self.dry_run) and real_notify_enabled()

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

    def _options_for(self, member_entity_id: str) -> Dict[str, Any]:
        """Return the SLM config-entry options governing one member lock.

        - Description: Looks up SLM config entries. If exactly one entry exists
          it is used; otherwise the entry whose ``data["lock_entity_id"]``
          matches ``member_entity_id`` is selected. Returns that entry's options
          (notify_service / smtp_enabled / smtp_recipients) as a plain dict, or
          ``{}`` when no entry matches or config_entries is unavailable (e.g. a
          Mock in tests).
        - Inputs: member_entity_id (str — the alert's lock entity id).
        - Outputs: dict of entry options (possibly empty).
        """
        try:
            entries = list(self.hass.config_entries.async_entries(DOMAIN))
        except Exception:  # noqa: BLE001 - Mock hass / no entries -> no options
            return {}
        if not entries:
            return {}
        if len(entries) == 1:
            entry = entries[0]
        else:
            entry = next(
                (
                    e
                    for e in entries
                    if e.data.get("lock_entity_id") == member_entity_id
                ),
                None,
            )
            if entry is None:
                return {}
        return dict(entry.options or {})

    async def dispatch(
        self, alert: Dict[str, Any], notify: ZoneNotify
    ) -> List[Dict[str, Any]]:
        """Route one alert to enabled channels; return the intent list.

        - Description: For each enabled channel, renders the payload, logs it,
          records a structured intent, and (only when real-send is permitted)
          dispatches it. Reads the lock's config-entry options once to resolve a
          portable ``notify_service`` and the ``smtp_enabled`` /
          ``smtp_recipients`` SMTP overrides. In dry-run NOTHING is sent. When
          notifications are "on" yet nothing is deliverable (real-send only),
          fires a fail-loud ``persistent_notification``. The returned intents are
          attached to the alert record so the API/panel can show them.
        - Inputs: alert (dict alert record), notify (ZoneNotify zone config).
        - Outputs: list of intent dicts ``{channel, recipients|targets, subject,
          dry_run}``.
        """
        intents: List[Dict[str, Any]] = []
        severity = self._severity_for(alert)
        prefix = subject_prefix_for(self.hass.config.location_name)
        subject = build_alert_subject(alert, prefix)
        body = build_alert_body(alert)
        body_lines = build_alert_body_lines(alert)

        member = str(alert.get("member_entity_id") or "")
        options = self._options_for(member)
        notify_service = (options.get("notify_service") or "").strip()
        # SMTP path runs for the per-zone email flag OR the global smtp_enabled
        # option (lets a HACS user opt into direct SMTP without a per-zone flag).
        email_on = notify.email.enabled or bool(options.get("smtp_enabled"))
        # Global recipients override (used only when the zone override is empty).
        options_recipients = [
            a.strip()
            for a in str(options.get("smtp_recipients") or "").split(",")
            if a.strip()
        ]

        email_intent: Optional[Dict[str, Any]] = None
        if email_on:
            email_intent = await self._dispatch_email(
                alert,
                notify.email,
                severity,
                subject,
                body,
                body_lines,
                options_recipients,
            )
            if email_intent is not None:
                intents.append(email_intent)

        if notify.mobile.enabled:
            intents.append(
                await self._dispatch_mobile(alert, notify.mobile, subject, body)
            )

        if notify_service and (email_on or notify.mobile.enabled):
            intents.append(
                await self._dispatch_notify_service(notify_service, subject, body)
            )

        self._maybe_fail_loud(
            member=member,
            email_on=email_on,
            email_intent=email_intent,
            notify_service=notify_service,
            mobile_enabled=notify.mobile.enabled,
        )

        return intents

    def _maybe_fail_loud(
        self,
        member: str,
        email_on: bool,
        email_intent: Optional[Dict[str, Any]],
        notify_service: str,
        mobile_enabled: bool,
    ) -> None:
        """Fire a persistent_notification when alerts fire but nothing delivers.

        - Description: D6 fail-loud. Only when real-send is permitted: if the
          email path was the ONLY thing enabled, its render produced nothing
          (creds missing => ``email_intent is None``), there is no
          ``notify_service``, and mobile is not enabled, raise a stable
          ``persistent_notification`` telling the user to configure a delivery
          channel. Never nags in dry-run. Best-effort: schedules the service
          call and swallows errors so a misconfig can't crash dispatch.
        - Inputs: member (str lock id), email_on (bool), email_intent (dict or
          None), notify_service (str), mobile_enabled (bool).
        - Outputs: None (side effect: schedules a persistent_notification).
        """
        if not self._should_really_send():
            return
        unconfigured = (
            email_on
            and email_intent is None
            and not notify_service
            and not mobile_enabled
        )
        if not unconfigured:
            return
        _LOGGER.warning(
            "notifications: alert fired for %s but no delivery channel is "
            "configured (SMTP creds missing, no notify_service, mobile off)",
            member,
        )
        try:
            self.hass.async_create_task(
                self.hass.services.async_call(
                    "persistent_notification",
                    "create",
                    {
                        "notification_id": f"slm_notify_unconfigured_{member}",
                        "title": ("Smart Lock Manager: notifications not configured"),
                        "message": (
                            "An alert fired but no delivery channel is "
                            "configured. Set a notify_service in the integration "
                            "options, or add SMTP credentials (slm_smtp_* or "
                            "smtp2go_*) to secrets.yaml."
                        ),
                    },
                    blocking=False,
                )
            )
        except Exception as exc:  # noqa: BLE001 - never crash on a nag failure
            _LOGGER.warning("notifications: failed to raise unconfigured nag: %s", exc)

    async def _dispatch_notify_service(
        self, service: str, subject: str, body: str
    ) -> Dict[str, Any]:
        """Send the alert through a portable ``notify.<service>`` service.

        - Description: The RECOMMENDED portable path (D2). Reuses
          :meth:`..notifications_channels.MobileNotifier.send_real`, which calls
          ``notify.<service>`` with a plain-text title/message. Gated by
          :meth:`_should_really_send` like the other channels; dry-run logs only.
        - Inputs: service (str notify.* service name), subject (str title),
          body (str plain-text message).
        - Outputs: intent dict ``{channel, service, subject, dry_run, sent}``.
        """
        sent = False
        if self._should_really_send():
            sent = await self.mobile.send_real(service, subject, body)
        else:
            _LOGGER.info(
                "notifications DRY-RUN notify_service (no send): "
                "service=%r title=%r",
                service,
                subject,
            )
        return {
            "channel": "notify_service",
            "service": service,
            "subject": subject,
            "dry_run": not self._should_really_send(),
            "sent": sent,
        }

    async def _dispatch_email(
        self,
        alert: Dict[str, Any],
        email_cfg: EmailNotify,
        severity: str,
        subject: str,
        body: str,
        body_lines: List[str],
        options_recipients: Optional[List[str]] = None,
    ) -> Optional[Dict[str, Any]]:
        """Render + (dry-run) record / (real) send the email channel.

        - Inputs: alert (dict), email_cfg (EmailNotify), severity (str),
          subject (str), body (str), body_lines (list[str] un-joined body lines
          for the HTML card), options_recipients (Optional[list[str]] — the
          global ``smtp_recipients`` override, used ONLY when the zone's
          ``recipients_override`` is empty).
        - Outputs: intent dict, or None when the payload could not be rendered.
        """
        actor = alert.get("actor")
        # Plain-text parity: append a "Triggered by:" line to the PLAIN body only
        # (the HTML card renders its own dedicated actor block from ``actor``, so
        # adding it to body_lines too would double-render it in HTML).
        if actor:
            body = f"{body}\nTriggered by: {actor}"
        # Zone override wins; fall back to the global smtp_recipients option.
        recipients_override = email_cfg.recipients_override or (
            options_recipients or []
        )
        rendered = await self.email.render(
            severity,
            subject,
            body,
            recipients_override,
            kind="alert",
            body_lines=body_lines,
            actor=actor,
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
            "body": rendered.body,
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
