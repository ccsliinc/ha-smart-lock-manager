"""Regression tests for the notification real-send gate + recipient override.

Two bugs are covered here:

* **BUG 1 — send gate read the env, not the flags file.**
  ``NotificationDispatcher._should_really_send`` used to call the env-ONLY
  ``notifications.real_send_enabled``. The AlertEngine builds the dispatcher
  with ``dry_run`` derived from the FILE-AWARE ``gating.real_notify_enabled``,
  so on the office HA OS install (flags file ``real_notify: true``, no settable
  env) ``dry_run`` was False but the extra env-only check forced dry-run anyway
  — real email never sent. ``_should_really_send`` now delegates to the
  file-aware ``gating.real_notify_enabled`` (env OR file), mirroring
  ``auto_lock.AutoLockEngine._may_execute``.

* **BUG 2 — a non-empty ``recipients_override`` was ignored.**
  ``EmailNotifier._resolve_recipients`` appended the override onto base +
  kind-specific recipients, so a zone override never REPLACED the base routing.
  It now uses a non-empty override verbatim (trimmed, blanks dropped) and falls
  back to base + kind extras only when the override is empty / None.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict
from unittest.mock import patch

import pytest
from homeassistant.core import HomeAssistant

from custom_components.smart_lock_manager import gating
from custom_components.smart_lock_manager.notifications import NotificationDispatcher
from custom_components.smart_lock_manager.notifications_channels import EmailNotifier

# Flag env keys cleared in every gate test so ONLY the flags file (or the var
# under test) drives the result. SLM_FLAGS_PATH is set per-test to the temp file.
_FLAG_ENV_KEYS = (
    "SLM_DEV_MOCK",
    "SLM_ENABLE_ENGINES",
    "SLM_ENABLE_REAL_NOTIFY",
    "SLM_ENABLE_REAL_AUTOLOCK",
)


def _write_real_notify(path: Path, value: bool) -> None:
    """Write a flags file with ``real_notify`` set and re-prime the cache.

    - Description: Serializes ``{"real_notify": value}`` to ``path`` and runs
      :func:`gating.prime_flags_cache` so the no-I/O hot path observes it
      (mirrors the executor prime ``__init__.py`` runs on a settings refresh).
    - Inputs: path (Path target), value (bool real_notify flag).
    - Outputs: None.
    """
    path.write_text(json.dumps({"real_notify": value}), encoding="utf-8")
    gating._warned_key = None
    gating.prime_flags_cache()


@pytest.fixture
def flags_path(tmp_path: Path):
    """Return a temp flags-file path and clear all flag env vars.

    - Description: Points the test at ``tmp_path/flags.json`` via the
      ``SLM_FLAGS_PATH`` override while clearing every flag env var, then primes
      against the (initially absent) file so the cache starts all-false.
    - Inputs: tmp_path (pytest fixture).
    - Outputs: Path to the (initially absent) temp flags file.
    """
    path = tmp_path / "flags.json"
    cleared = {key: "" for key in _FLAG_ENV_KEYS}
    cleared["SLM_FLAGS_PATH"] = str(path)
    with patch.dict(os.environ, cleared):
        gating._warned_key = None
        gating.prime_flags_cache()
        yield path


class TestShouldReallySendFileAware:
    """BUG 1: the send gate must honor the FILE flag, not just the env var."""

    def test_should_send_true_when_file_real_notify_and_not_dry_run(
        self, hass: HomeAssistant, flags_path: Path
    ) -> None:
        """File real_notify true + dry_run False -> _should_really_send True.

        This is the office HA-OS path the bug broke: no env var set, the flag
        comes from the file, and the engine built the dispatcher dry_run=False.
        """
        _write_real_notify(flags_path, True)
        dispatcher = NotificationDispatcher(hass, dry_run=False)
        assert dispatcher._should_really_send() is True

    def test_should_send_false_when_file_false_and_no_env(
        self, hass: HomeAssistant, flags_path: Path
    ) -> None:
        """File real_notify false + no env var -> _should_really_send False."""
        _write_real_notify(flags_path, False)
        dispatcher = NotificationDispatcher(hass, dry_run=False)
        assert dispatcher._should_really_send() is False

    def test_should_send_false_when_flags_file_absent_and_no_env(
        self, hass: HomeAssistant, flags_path: Path
    ) -> None:
        """Absent flags file + no env var -> _should_really_send False."""
        assert not flags_path.exists()
        dispatcher = NotificationDispatcher(hass, dry_run=False)
        assert dispatcher._should_really_send() is False

    def test_dry_run_forced_overrides_file_flag(
        self, hass: HomeAssistant, flags_path: Path
    ) -> None:
        """Even with file real_notify true, dry_run=True suppresses real send."""
        _write_real_notify(flags_path, True)
        dispatcher = NotificationDispatcher(hass, dry_run=True)
        assert dispatcher._should_really_send() is False


class TestRecipientsOverride:
    """BUG 2: a non-empty recipients_override REPLACES base + kind extras."""

    @staticmethod
    def _creds() -> Dict[str, Any]:
        """Return parity creds with a base ``to`` and a kind-specific alert list.

        - Inputs: none.
        - Outputs: dict mirroring ``_load_secrets_sync`` output.
        """
        return {
            "user": "u",
            "pass": "p",
            "from": "from@x",
            "to": "base@x",
            "kind_to": {"alert": ["alert@x"]},
        }

    def test_non_empty_override_replaces_base_and_alert(
        self, hass: HomeAssistant
    ) -> None:
        """A non-empty override is used verbatim (no base / alert appended)."""
        notifier = EmailNotifier(hass)
        recipients = notifier._resolve_recipients(
            self._creds(), "alert", ["jsugamele@gmail.com"]
        )
        assert recipients == ["jsugamele@gmail.com"]

    def test_empty_override_falls_back_to_base_plus_alert(
        self, hass: HomeAssistant
    ) -> None:
        """Empty override -> base smtp2go_to + kind-specific alert recipients."""
        notifier = EmailNotifier(hass)
        recipients = notifier._resolve_recipients(self._creds(), "alert", [])
        assert recipients == ["base@x", "alert@x"]

    def test_none_override_falls_back_to_base_plus_alert(
        self, hass: HomeAssistant
    ) -> None:
        """None override -> base smtp2go_to + kind-specific alert recipients."""
        notifier = EmailNotifier(hass)
        recipients = notifier._resolve_recipients(
            self._creds(), "alert", None  # type: ignore[arg-type]
        )
        assert recipients == ["base@x", "alert@x"]

    def test_override_trims_and_drops_blanks(self, hass: HomeAssistant) -> None:
        """Override addresses are trimmed and blank entries are ignored."""
        notifier = EmailNotifier(hass)
        recipients = notifier._resolve_recipients(
            self._creds(), "alert", ["  a@x  ", "", "   ", "b@x"]
        )
        assert recipients == ["a@x", "b@x"]

    def test_override_all_blank_falls_back_to_base(self, hass: HomeAssistant) -> None:
        """An override of only blanks is treated as empty -> base fallback."""
        notifier = EmailNotifier(hass)
        recipients = notifier._resolve_recipients(self._creds(), "alert", ["", "   "])
        assert recipients == ["base@x", "alert@x"]
