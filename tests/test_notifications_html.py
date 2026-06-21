"""Tests for the HTML alert-card renderer + the multipart email upgrade.

Covers :func:`render_alert_html` (bar color, marker heading, escaping, actor
line, footer) and the :meth:`EmailNotifier._build_mime` multipart/alternative
upgrade (plain part unchanged, HTML part carries the styled card), plus the
``build_alert_body`` / ``build_alert_body_lines`` parity.
"""

from __future__ import annotations

from custom_components.smart_lock_manager.notifications_bodies import (
    build_alert_body,
    build_alert_body_lines,
)
from custom_components.smart_lock_manager.notifications_channels import (
    EmailNotifier,
    RenderedEmail,
)
from custom_components.smart_lock_manager.notifications_html import render_alert_html


def test_render_alert_html_basic() -> None:
    """WARN card carries the bar color, subject, body rows and footer."""
    html = render_alert_html(
        severity="WARN",
        subject="lock alert",
        body_lines=["line a", "line b"],
        host_tag="ha-office",
        timestamp="2026-06-20T12:00:00",
        actor=None,
    )
    assert "#e67e22" in html
    assert "lock alert" in html
    assert "line a" in html
    assert "line b" in html
    assert "ha-office &middot; 2026-06-20T12:00:00" in html


def test_render_alert_html_actor_line() -> None:
    """An actor adds a 'Triggered by' line; absence omits it cleanly."""
    with_actor = render_alert_html(
        severity="WARN",
        subject="lock alert",
        body_lines=["line a"],
        actor="keypad-3",
    )
    assert "Triggered by:" in with_actor
    assert "keypad-3" in with_actor

    without_actor = render_alert_html(
        severity="WARN",
        subject="lock alert",
        body_lines=["line a"],
        actor=None,
    )
    assert "Triggered by:" not in without_actor


def test_render_alert_html_escapes() -> None:
    """Body and subject text is HTML-escaped (no raw markup injected)."""
    html = render_alert_html(
        severity="WARN",
        subject="door <script>",
        body_lines=["value < 5 & rising"],
    )
    assert "&lt;" in html
    assert "&amp;" in html
    assert "<script" not in html


def test_build_mime_multipart(hass) -> None:
    """_build_mime yields multipart/alternative: plain (verbatim) + HTML card."""
    email = RenderedEmail(
        severity="WARN",
        kind="alert",
        subject="[fleet/internal/alert] 🟡 test",
        body="line one\nline two",
        recipients=["a@x"],
        clean_subject="test",
        body_lines=["line one", "line two"],
        host_tag="ha-office",
    )
    notifier = EmailNotifier(hass)
    msg = notifier._build_mime({"from": "from@x"}, email)

    assert msg.get_content_type() == "multipart/alternative"
    parts = msg.get_payload()
    assert len(parts) == 2

    assert parts[0].get_content_type() == "text/plain"
    assert parts[0].get_payload(decode=True).decode("utf-8") == email.body

    assert parts[1].get_content_type() == "text/html"
    html = parts[1].get_payload(decode=True).decode("utf-8")
    assert "#e67e22" in html
    assert "test" in html
    assert "line one" in html
    assert "line two" in html
    assert "ha-office" in html
    assert "&middot;" in html


async def test_card_heading_single_marker_no_fleet_prefix(hass) -> None:
    """End-to-end render(): HTML card heading uses the clean subject.

    The heading carries exactly one marker and no fleet prefix, while the email
    Subject header stays fully wrapped.
    """
    clean_subject = "office HA - lock.rear battery low (87%)"
    notifier = EmailNotifier(hass)

    async def _fake_creds():
        return {"from": "from@x", "to": "a@x", "kind_to": {"alert": []}}

    async def _fake_host_tag():
        return "ha-office"

    notifier._creds = _fake_creds  # type: ignore[method-assign]
    notifier._host_tag = _fake_host_tag  # type: ignore[method-assign]

    rendered = await notifier.render(
        severity="WARN",
        subject=clean_subject,
        body="line one",
        recipients_override=[],
        kind="alert",
        body_lines=["line one"],
    )
    assert rendered is not None

    # Subject HEADER stays fully wrapped (prefix + single marker + clean subject).
    assert rendered.subject == f"[fleet/internal/alert] 🟡 {clean_subject}"
    assert rendered.clean_subject == clean_subject

    msg = notifier._build_mime({"from": "from@x"}, rendered)
    assert msg["Subject"] == f"[fleet/internal/alert] 🟡 {clean_subject}"

    parts = msg.get_payload()
    html = parts[1].get_payload(decode=True).decode("utf-8")

    # Card heading == "<marker> <clean subject>": exactly ONE marker glyph and
    # NO fleet prefix leaking into the heading.
    heading = f"🟡 {clean_subject}"
    assert heading in html
    assert html.count("🟡") == 1
    assert "[fleet/internal" not in html

    # text/plain part stays the verbatim body.
    assert parts[0].get_content_type() == "text/plain"
    assert parts[0].get_payload(decode=True).decode("utf-8") == rendered.body


def test_build_alert_body_parity() -> None:
    """build_alert_body is the newline-join of build_alert_body_lines."""
    alert = {
        "alert_type": "sustained_unlock",
        "member_entity_id": "lock.front",
        "friendly_name": "Front Door",
        "message": "Unlocked >30s without re-lock",
        "severity": "WARN",
        "timestamp": "2026-06-20T10:00:00",
        "last_changed": "2026-06-20T09:59:00",
        "is_recovery": False,
    }
    assert build_alert_body(alert) == "\n".join(build_alert_body_lines(alert))
    lines = build_alert_body_lines(alert)
    assert isinstance(lines, list)
    assert len(lines) >= 1
