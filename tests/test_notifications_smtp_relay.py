"""The send path must use the CONFIGURED relay, and say so when it fails.

Context, because this is a regression test for an outage rather than a
hypothetical. The integration shipped a default SMTP host: a named vendor's
relay, applied whenever the install supplied credentials but no host. One
install did exactly that. When the fleet retired that vendor on 2026-07-30 the
credentials stopped being valid there, so every alert send failed ``535
Incorrect authentication data`` - 454 of them between 2026-08-24 and
2026-09-22, each one about a second after a recorded CRIT door alert. Nothing
looked broken: the hostname still resolved, the TCP connect still succeeded,
and the error named no host, so it read exactly like a wrong password rather
than a send to the wrong server.

Two defects produced that, and both are pinned here:

- The default itself, now removed (see
  :mod:`tests.test_notifications_smtp_creds`).
- A SECOND copy of the same literal in :meth:`EmailNotifier._smtp_send`, as an
  ``or "..."`` fallback. Removing only the first would have changed nothing,
  because this one would have supplied the retired relay all over again.

And one diagnostic defect: the error line discarded the host. A failure that
cannot distinguish "wrong credentials" from "right credentials, wrong server"
is the three-outcome rule collapsed into one, and it is why this went two
months without being read correctly.
"""

from __future__ import annotations

from unittest.mock import Mock, patch

from custom_components.smart_lock_manager.notifications_channels import (
    EmailNotifier,
    RenderedEmail,
)

CHANNELS = "custom_components.smart_lock_manager.notifications_channels"


def _creds(**over) -> dict:
    """Return a complete SMTP cred dict, overridable per test.

    - Inputs: over (keyword overrides merged over the defaults).
    - Outputs: dict with host/port/user/pass/from keys.
    """
    base = {
        "host": "relay.chosen-by-installer.invalid",
        "port": 2525,
        "user": "slm-user",
        "pass": "slm-pass",
        "from": "alerts@example.invalid",
    }
    base.update(over)
    return base


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


class TestSendUsesTheConfiguredRelay:
    """``_smtp_send`` connects to creds['host'], never to a built-in default."""

    def test_connects_to_the_configured_host_and_port(self) -> None:
        """The SMTP constructor receives exactly the configured endpoint."""
        notifier = EmailNotifier(Mock(config=Mock(path=lambda _n: "/tmp/secrets.yaml")))
        with patch(f"{CHANNELS}.smtplib.SMTP") as smtp_cls:
            assert notifier._smtp_send(_creds(), _email()) is True
        host, port = smtp_cls.call_args[0][0], smtp_cls.call_args[0][1]
        assert host == "relay.chosen-by-installer.invalid"
        assert port == 2525

    def test_blank_host_raises_rather_than_falling_back(self) -> None:
        """An empty host must NOT silently resolve to some default relay.

        The config reader already refuses to return creds without a host, so
        reaching here with a blank one is a programming error. Failing loudly
        is correct; quietly substituting a hostname is what caused the outage.
        """
        notifier = EmailNotifier(Mock(config=Mock(path=lambda _n: "/tmp/secrets.yaml")))
        with patch(f"{CHANNELS}.smtplib.SMTP") as smtp_cls:
            try:
                notifier._smtp_send(
                    {k: v for k, v in _creds().items() if k != "host"}, _email()
                )
            except KeyError:
                pass
            else:  # pragma: no cover - only reached if a fallback returns
                raise AssertionError("a missing host was tolerated")
        smtp_cls.assert_not_called()


class TestFailureNamesTheEndpoint:
    """A failed send must identify the relay it was talking to."""

    def test_error_log_includes_host_and_port(self, caplog) -> None:
        """535 alone is ambiguous; the host is what makes it actionable."""
        import smtplib

        notifier = EmailNotifier(Mock(config=Mock(path=lambda _n: "/tmp/secrets.yaml")))
        with patch(f"{CHANNELS}.smtplib.SMTP") as smtp_cls:
            smtp_cls.return_value.__enter__.return_value.login.side_effect = (
                smtplib.SMTPAuthenticationError(535, b"Incorrect authentication data")
            )
            assert notifier._smtp_send(_creds(), _email()) is False
        assert "relay.chosen-by-installer.invalid" in caplog.text
        assert "2525" in caplog.text
