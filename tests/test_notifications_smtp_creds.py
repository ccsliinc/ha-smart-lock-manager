"""Tests for portable SMTP credential resolution (D3 + office regression).

:func:`..notifications_config.load_smtp_creds_sync` is *generic-first*: it reads
the portable ``slm_smtp_*`` keys first and falls back to the legacy
``smtp2go_*`` keys per field, so an install can point at any SMTP relay while an
older install keeps resolving from the keys it already has. These tests pin
every branch: generic-first, legacy fallback, port override, field-level mixing,
the missing-required => None contract, and per-kind recipient routing.

HISTORY, and the reason two tests in here were rewritten on 2026-10-26. The host
used to default to a named vendor's relay, and this file asserted that default
as an "office regression" guarantee. That turned a latent misconfiguration into
a pinned requirement: when the relay was retired, the integration kept
authenticating against it, the logins failed 535, two months of door alerts were
lost, and the test suite stayed green the whole time because it was testing that
the wrong thing happened reliably. The host now has no default and is required.
A test that pins a default endpoint pins a security decision, so the tests below
assert the ABSENCE of one.
"""

from __future__ import annotations

import re
from pathlib import Path

from custom_components.smart_lock_manager.notifications_config import (
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

    def test_legacy_keys_resolve_with_their_own_host(self, tmp_path: Path) -> None:
        """smtp2go_* only, host included => those values, port defaulted."""
        path = _write(
            tmp_path,
            "smtp2go_user: ouser\n"
            "smtp2go_pass: opass\n"
            "smtp2go_from: from@office\n"
            "smtp2go_to: to@office\n"
            "smtp2go_host: relay.office.invalid\n",
        )
        creds = load_smtp_creds_sync(path)
        assert creds is not None
        assert creds["host"] == "relay.office.invalid"
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
            # legacy keys present too, but must be ignored.
            "smtp2go_user: ouser\n" "smtp2go_host: legacy.example.invalid\n",
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
            "smtp2go_to: to@office\n"
            "smtp2go_host: relay.office.invalid\n",
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
            "slm_smtp_host: smtp.example.com\n"
            "slm_smtp_alert_to: alert1@x, alert2@x\n",
        )
        creds = load_smtp_creds_sync(path)
        assert creds is not None
        assert creds["kind_to"]["alert"] == ["alert1@x", "alert2@x"]


class TestOfficeRegression:
    """A legacy smtp2go_*-keyed install MUST keep resolving from those keys."""

    def test_legacy_only_full_resolution(self, tmp_path: Path) -> None:
        """Legacy keys incl. host => full resolution + alert routing."""
        path = _write(
            tmp_path,
            "smtp2go_user: ouser\n"
            "smtp2go_pass: opass\n"
            "smtp2go_from: from@office\n"
            "smtp2go_to: to@office\n"
            "smtp2go_host: relay.office.invalid\n"
            "smtp2go_alert_to: oncall@office\n",
        )
        creds = load_smtp_creds_sync(path)
        assert creds is not None
        assert creds["host"] == "relay.office.invalid"
        assert creds["port"] == 587
        assert creds["user"] == "ouser"
        assert creds["to"] == "to@office"
        assert creds["kind_to"]["alert"] == ["oncall@office"]


class TestHostIsRequiredAndNeverDefaulted:
    """The host must come from config. No module may supply one on its own."""

    def test_no_host_under_either_prefix_returns_none(self, tmp_path: Path) -> None:
        """Every other required key present, no host => None, not a guess."""
        path = _write(
            tmp_path,
            "smtp2go_user: ouser\n"
            "smtp2go_pass: opass\n"
            "smtp2go_from: from@office\n"
            "smtp2go_to: to@office\n",
        )
        assert load_smtp_creds_sync(path) is None

    def test_the_missing_host_error_names_the_key_to_set(
        self, tmp_path: Path, caplog
    ) -> None:
        """A bare failure is unactionable: the log must name slm_smtp_host."""
        path = _write(
            tmp_path,
            "smtp2go_user: ouser\n"
            "smtp2go_pass: opass\n"
            "smtp2go_from: from@office\n",
        )
        assert load_smtp_creds_sync(path) is None
        assert "slm_smtp_host" in caplog.text

    def test_no_module_defaults_the_host_to_a_literal(self) -> None:
        """No .py in the package may hold a default SMTP hostname.

        The two-month alert outage came from a hostname literal standing in for
        a configuration value, in TWO places: the config reader's default and a
        second ``or "..."`` fallback in the send path, which would have
        re-introduced the bug on its own. This fails if either shape returns.
        """
        pkg = Path(__file__).resolve().parent.parent / "custom_components"
        offenders = []
        for py in pkg.rglob("*.py"):
            for num, line in enumerate(
                py.read_text(encoding="utf-8").splitlines(), start=1
            ):
                if line.lstrip().startswith("#"):
                    continue
                if re.search(r"""smtp2go\.com|["']smtp\.[a-z0-9.-]+["']""", line):
                    offenders.append(f"{py.name}:{num}: {line.strip()}")
        assert not offenders, "default SMTP hostname literal(s): " + "; ".join(
            offenders
        )
