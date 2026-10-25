"""Security regression tests for the third HACS review round.

Two findings, both about trusting something that was never verified:

- Finding 1: :meth:`EmailNotifier._smtp_send` called ``smtp.starttls()`` with no
  SSL context, so the stdlib verified neither the certificate chain nor the
  hostname before the ``LOGIN`` that follows. Anyone on the path to the relay
  could have impersonated it and collected the SMTP credentials out of
  ``secrets.yaml``.
- Finding 2: the panel's ``#friendly_name`` input interpolated a lock-supplied
  display name into ``value="..."`` without escaping, inside a
  ``shadowRoot.innerHTML`` template, so a name containing a double quote broke
  out of the attribute and injected markup into the admin panel.

The frontend half is asserted against the SOURCE *and* the BUILT bundle on
purpose: the bundle is a committed build artifact, so a correct source fix with
a stale ``dist/`` would ship the vulnerable code to every HACS install while
looking fixed in review.
"""

from __future__ import annotations

import re
import ssl
from pathlib import Path
from unittest.mock import Mock, patch

from custom_components.smart_lock_manager.notifications_channels import (
    EmailNotifier,
    RenderedEmail,
)

COMPONENT_DIR = (
    Path(__file__).resolve().parent.parent / "custom_components" / "smart_lock_manager"
)
PANEL_SRC = COMPONENT_DIR / "frontend" / "src" / "main.js"
PANEL_BUNDLE = COMPONENT_DIR / "frontend" / "dist" / "smart-lock-manager-panel.js"


def _creds() -> dict:
    """Return a complete SMTP cred dict for the send path.

    - Outputs: dict with host/port/user/pass/from keys.
    """
    return {
        "host": "smtp.example.invalid",
        "port": 587,
        "user": "slm-user",
        "pass": "slm-pass",
        "from": "alerts@example.invalid",
    }


def _email() -> RenderedEmail:
    """Return a minimal rendered email payload.

    - Outputs: RenderedEmail with one recipient.
    """
    return RenderedEmail(
        severity="CRIT",
        kind="alert",
        subject="[fleet/internal/ha-office] CRIT - front door jammed",
        body="front door jammed",
        recipients=["ops@example.invalid"],
        clean_subject="front door jammed",
        body_lines=["front door jammed"],
    )


class TestSmtpStarttlsVerifies:
    """Finding 1: the STARTTLS handshake must verify the relay."""

    def test_starttls_receives_a_verifying_context(self) -> None:
        """starttls() gets a context that checks the chain AND the hostname.

        The context itself is built for real (``client_context`` is not
        patched), so this asserts the live security properties rather than
        merely that some keyword argument was passed.
        """
        notifier = EmailNotifier(Mock())
        with patch(
            "custom_components.smart_lock_manager.notifications_channels.smtplib.SMTP"
        ) as smtp_cls:
            assert notifier._smtp_send(_creds(), _email()) is True

        smtp = smtp_cls.return_value.__enter__.return_value
        assert smtp.starttls.call_count == 1
        context = smtp.starttls.call_args.kwargs.get("context")
        assert isinstance(context, ssl.SSLContext), "starttls() got no SSL context"
        assert context.verify_mode == ssl.CERT_REQUIRED, "certificate chain unverified"
        assert context.check_hostname is True, "hostname unverified"

    def test_credentials_are_sent_only_after_starttls(self) -> None:
        """LOGIN must never precede the handshake that protects it."""
        notifier = EmailNotifier(Mock())
        with patch(
            "custom_components.smart_lock_manager.notifications_channels.smtplib.SMTP"
        ) as smtp_cls:
            notifier._smtp_send(_creds(), _email())

        smtp = smtp_cls.return_value.__enter__.return_value
        order = [name for name, _args, _kwargs in smtp.method_calls]
        assert "starttls" in order and "login" in order
        assert order.index("starttls") < order.index("login")

    def test_no_bare_starttls_call_survives_anywhere(self) -> None:
        """No module may call starttls() without an explicit context."""
        offenders = [
            f"{path.relative_to(COMPONENT_DIR)}:{num}"
            for path in COMPONENT_DIR.rglob("*.py")
            for num, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
            if re.search(r"\.starttls\(\s*\)", line)
        ]
        assert not offenders, f"starttls() without a context at: {offenders}"


class TestPanelAttributeEscaping:
    """Finding 2: no unescaped interpolation into an HTML value attribute."""

    @staticmethod
    def _value_interpolations(source: str) -> list[str]:
        """Return every ``value="${...}"`` interpolation in HTML templates.

        - Description: Skips ``querySelector`` lines, where the same shape is a
          CSS attribute selector against the live DOM rather than HTML markup.
        - Inputs: source (str file contents).
        - Outputs: list[str] of the matched fragments.
        """
        return [
            match.group(0)
            for line in source.splitlines()
            if "querySelector" not in line
            for match in re.finditer(r'value="\$\{[^"]*', line)
        ]

    def test_source_escapes_every_value_attribute(self) -> None:
        """Every value attribute in the panel source goes through _esc()."""
        unescaped = [
            frag
            for frag in self._value_interpolations(
                PANEL_SRC.read_text(encoding="utf-8")
            )
            if "_esc(" not in frag and "escHtml(" not in frag
        ]
        assert not unescaped, f"unescaped value attributes: {unescaped}"

    def test_source_escapes_the_friendly_name_input(self) -> None:
        """The specific input from the review is escaped."""
        line = next(
            text
            for text in PANEL_SRC.read_text(encoding="utf-8").splitlines()
            if 'id="friendly_name"' in text
        )
        assert 'value="${this._esc(' in line

    def test_built_bundle_carries_the_escaped_form(self) -> None:
        """dist/ is rebuilt, so the shipped panel is not the vulnerable one."""
        bundle = PANEL_BUNDLE.read_text(encoding="utf-8")
        assert 'id="friendly_name"' in bundle, "friendly_name input missing from bundle"
        unescaped = [
            frag
            for frag in self._value_interpolations(bundle)
            if "_esc(" not in frag and "escHtml(" not in frag
        ]
        assert not unescaped, f"stale bundle, unescaped value attributes: {unescaped}"
