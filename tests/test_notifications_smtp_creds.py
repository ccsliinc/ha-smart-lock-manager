"""Tests for portable SMTP credential resolution (D3 + office regression).

:func:`..notifications_config.load_smtp_creds_sync` is *generic-first*: it reads
the portable ``slm_smtp_*`` keys first and falls back to the office
``smtp2go_*`` keys per field, so a fresh HACS install can point at any SMTP
relay while the existing office install (``smtp2go_*`` only) keeps resolving to
``mail.smtp2go.com:587`` byte-for-byte. These tests pin every branch of that
resolution: generic-first, office fallback, host/port override, field-level
mixing, the missing-required => None contract, and per-kind recipient routing.
"""

from __future__ import annotations

from pathlib import Path

from custom_components.smart_lock_manager.notifications_config import (
    DEFAULT_SMTP_HOST,
    DEFAULT_SMTP_PORT,
    load_smtp_creds_sync,
)


def _write(tmp_path: Path, body: str) -> str:
    """Write ``body`` to a temp secrets.yaml and return its path.

    - Inputs: tmp_path (pytest fixture dir), body (str YAML contents).
    - Outputs: str absolute path to the written secrets file.
    """
    path = tmp_path / "secrets.yaml"
    path.write_text(body, encoding="utf-8")
    return str(path)


class TestLoadSmtpCredsResolution:
    """Generic-first resolution, office fallback, host/port + None contract."""

    def test_smtp2go_only_resolves_default_host_port(self, tmp_path: Path) -> None:
        """smtp2go_* only => default mail.smtp2go.com:587 + smtp2go values."""
        path = _write(
            tmp_path,
            "smtp2go_user: ouser\n"
            "smtp2go_pass: opass\n"
            "smtp2go_from: from@office\n"
            "smtp2go_to: to@office\n",
        )
        creds = load_smtp_creds_sync(path)
        assert creds is not None
        assert creds["host"] == DEFAULT_SMTP_HOST == "mail.smtp2go.com"
        assert creds["port"] == DEFAULT_SMTP_PORT == 587
        assert creds["user"] == "ouser"
        assert creds["pass"] == "opass"
        assert creds["from"] == "from@office"
        assert creds["to"] == "to@office"

    def test_generic_slm_keys_win_with_host_port(self, tmp_path: Path) -> None:
        """slm_smtp_* present => those win, incl. custom host/port."""
        path = _write(
            tmp_path,
            "slm_smtp_user: guser\n"
            "slm_smtp_pass: gpass\n"
            "slm_smtp_from: from@generic\n"
            "slm_smtp_to: to@generic\n"
            "slm_smtp_host: smtp.example.com\n"
            "slm_smtp_port: 2525\n"
            # office keys present too, but must be ignored.
            "smtp2go_user: ouser\n" "smtp2go_host: mail.smtp2go.com\n",
        )
        creds = load_smtp_creds_sync(path)
        assert creds is not None
        assert creds["host"] == "smtp.example.com"
        assert creds["port"] == 2525
        assert creds["user"] == "guser"
        assert creds["from"] == "from@generic"

    def test_mixed_keys_resolve_per_field(self, tmp_path: Path) -> None:
        """A mixed file resolves host from slm, user from smtp2go fallback."""
        path = _write(
            tmp_path,
            # host comes from the generic key...
            "slm_smtp_host: smtp.example.com\n"
            # ...but user/pass/from only exist under the office prefix.
            "smtp2go_user: ouser\n"
            "smtp2go_pass: opass\n"
            "smtp2go_from: from@office\n",
        )
        creds = load_smtp_creds_sync(path)
        assert creds is not None
        assert creds["host"] == "smtp.example.com"
        assert creds["user"] == "ouser"
        assert creds["from"] == "from@office"
        # No port key anywhere => default.
        assert creds["port"] == DEFAULT_SMTP_PORT

    def test_missing_required_returns_none(self, tmp_path: Path) -> None:
        """No user under EITHER prefix => required missing => None."""
        path = _write(
            tmp_path,
            "smtp2go_pass: opass\n"
            "smtp2go_from: from@office\n"
            "smtp2go_to: to@office\n",
        )
        assert load_smtp_creds_sync(path) is None


class TestPerKindRecipients:
    """Per-kind extra-recipient routing (slm_smtp_<k>_to / smtp2go_<k>_to)."""

    def test_generic_alert_to_lands_in_kind_to(self, tmp_path: Path) -> None:
        """slm_smtp_alert_to populates kind_to['alert']."""
        path = _write(
            tmp_path,
            "slm_smtp_user: guser\n"
            "slm_smtp_pass: gpass\n"
            "slm_smtp_from: from@generic\n"
            "slm_smtp_alert_to: alert1@x, alert2@x\n",
        )
        creds = load_smtp_creds_sync(path)
        assert creds is not None
        assert creds["kind_to"]["alert"] == ["alert1@x", "alert2@x"]


class TestOfficeRegression:
    """Office smtp2go-only install MUST keep resolving exactly as today."""

    def test_office_smtp2go_only_full_resolution(self, tmp_path: Path) -> None:
        """Office-style keys => mail.smtp2go.com:587 + smtp2go_alert_to routing."""
        path = _write(
            tmp_path,
            "smtp2go_user: ouser\n"
            "smtp2go_pass: opass\n"
            "smtp2go_from: from@office\n"
            "smtp2go_to: to@office\n"
            "smtp2go_alert_to: oncall@office\n",
        )
        creds = load_smtp_creds_sync(path)
        assert creds is not None
        assert creds["host"] == "mail.smtp2go.com"
        assert creds["port"] == 587
        assert creds["user"] == "ouser"
        assert creds["to"] == "to@office"
        assert creds["kind_to"]["alert"] == ["oncall@office"]
