"""Shared HTML alert-card renderer for the SLM notification layer.

Ports the fleet ``lib_email`` HTML card into the SLM integration so emails sent
by :class:`~.notifications_channels.EmailNotifier` carry a styled, severity-bar
HTML part alongside the legacy plain-text body. The severity markers come from
:data:`~.notifications_channels._MARKERS` (single source of truth) so the HTML
heading stays byte-compatible with the subject markers.

SECURITY: the card carries door names, severities, human messages, timestamps
and an optional actor label only — never PIN codes.
"""

from __future__ import annotations

from typing import Any, Optional

from .notifications_channels import _MARKERS

# Severity -> bar color. Mirrors the fleet lib_email card palette. The recovery
# key is literally "HEALTHY-RECOVERY"; unknown severities fall back to blue.
_BAR_COLORS = {
    "CRIT": "#c0392b",
    "ERROR": "#c0392b",
    "WARN": "#e67e22",
    "HEALTHY-RECOVERY": "#27ae60",
    "INFO": "#2980b9",
}


def _esc(text: Any) -> str:
    """Minimal HTML-escape for card text (ampersand + angle brackets).

    - Description: Escapes ``&``, ``<`` and ``>`` so alert text/door names render
      literally and never inject markup. Ampersand is escaped first.
    - Inputs: text (Any — coerced to str).
    - Outputs: str HTML-safe text.
    """
    return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def render_alert_html(
    severity: str,
    subject: str,
    body_lines: "list[str] | str",
    host_tag: Optional[str] = None,
    actor: Optional[str] = None,
    timestamp: Optional[str] = None,
) -> str:
    """Render the styled HTML alert card for an email's text/html part.

    - Description: Byte-faithful port of the fleet ``lib_email`` alert card. The
      severity drives the top bar color and the heading marker; the body lines
      become escaped rows; an optional ``actor`` adds a "Triggered by" line and
      the footer carries an optional host tag plus a timestamp (now() when None).
    - Inputs: severity (str), subject (str), body_lines (list[str] or str — a
      str is split on newlines), host_tag (Optional[str] footer host label),
      actor (Optional[str] triggered-by label), timestamp (Optional[str] ISO;
      defaults to now() to the second).
    - Outputs: str — a complete ``<html>`` document for the text/html MIME part.
    """
    import datetime as _dt

    sev = (severity or "").upper().strip()
    color = _BAR_COLORS.get(sev, "#2980b9")
    marker = _MARKERS.get(sev, "")
    if isinstance(body_lines, str):
        lines = [ln for ln in body_lines.split("\n")]
    else:
        lines = list(body_lines or [])
    while lines and not str(lines[-1]).strip():
        lines.pop()
    if timestamp is None:
        timestamp = _dt.datetime.now().replace(microsecond=0).isoformat()
    rows = "".join(
        f'<div style="padding:2px 0;color:#222;font-size:14px;'
        f'line-height:1.5;">{_esc(ln)}</div>'
        for ln in lines
    )
    actor_html = ""
    if actor:
        actor_html = (
            f'<div style="margin-top:8px;font-size:13px;color:#444;">'
            f"<strong>Triggered by:</strong> {_esc(actor)}</div>"
        )
    footer_bits = []
    if host_tag:
        footer_bits.append(_esc(host_tag))
    footer_bits.append(_esc(timestamp))
    footer = " &middot; ".join(footer_bits)
    heading = f"{marker} {_esc(subject)}" if marker else _esc(subject)
    return (
        '<html><body style="margin:0;padding:0;background:#f4f5f7;">'
        '<div style="max-width:560px;margin:16px auto;background:#ffffff;'
        "border-radius:6px;overflow:hidden;font-family:-apple-system,"
        "Segoe UI,Roboto,Helvetica,Arial,sans-serif;"
        'box-shadow:0 1px 3px rgba(0,0,0,0.12);">'
        f'<div style="height:6px;background:{color};"></div>'
        '<div style="padding:18px 22px;">'
        f'<div style="font-size:17px;font-weight:600;color:#1a1a1a;'
        f'margin-bottom:10px;">{heading}</div>'
        f"{rows}"
        f"{actor_html}"
        "</div>"
        f'<div style="padding:10px 22px;background:#fafafa;border-top:'
        f'1px solid #eee;font-size:12px;color:#888;">{footer}</div>'
        "</div></body></html>"
    )
