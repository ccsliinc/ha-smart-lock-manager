"""Tests for the file-based flag source in :mod:`gating` (HA-OS support).

Home Assistant OS does not let operators set process env vars, so the three
engine flags are ALSO sourced from a JSON file at the HA config dir, OR-combined
with their env vars. These tests prove, against a temp file pointed at via the
``SLM_FLAGS_PATH`` override:

* file ``enable_engines`` true -> :func:`engines_enabled` true (and
  :func:`current_engine_mode` -> ``observe`` with dev-mock off);
* a missing file -> all file flags false (env still honored);
* ``real_notify`` / ``real_autolock`` flow from the file;
* a malformed JSON file -> all-false, never raises;
* env-OR-file precedence per flag (either source enables);
* ``SLM_DEV_MOCK`` is NOT readable from the file (env-only).

Every test isolates env via ``patch.dict`` and points ``SLM_FLAGS_PATH`` at a
``tmp_path`` file so nothing touches the real ``/config`` location, and
re-primes the module's in-memory flags cache after each write.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, Optional
from unittest.mock import patch

import pytest

from custom_components.smart_lock_manager import gating

# Env keys cleared in every test so only the file (or the var under test) drives
# the result. SLM_FLAGS_PATH is set per-test to the temp file.
_FLAG_ENV_KEYS = (
    "SLM_DEV_MOCK",
    "SLM_ENABLE_ENGINES",
    "SLM_ENABLE_REAL_NOTIFY",
    "SLM_ENABLE_REAL_AUTOLOCK",
)


def _write_flags(path: Path, payload: Optional[Dict[str, Any]]) -> None:
    """Write a flags file (or raw-malformed content) and re-prime the cache.

    - Description: Helper that serializes ``payload`` as JSON to ``path`` (or,
      if ``payload`` is a str, writes it verbatim to simulate malformed JSON),
      clears the warn-dedup guard, then calls :func:`gating.prime_flags_cache`
      so the in-memory cache reflects the new file (mirrors the executor prime
      that ``__init__.py`` runs on every settings-change refresh).
    - Inputs: path (Path target), payload (dict to JSON-dump, str for raw bytes,
      or None to leave the file absent).
    - Outputs: None.
    """
    if payload is not None:
        if isinstance(payload, str):
            path.write_text(payload, encoding="utf-8")
        else:
            path.write_text(json.dumps(payload), encoding="utf-8")
    # Reset the warn guard so a fresh bad file logs once, then prime so the
    # no-I/O hot path observes the new file contents.
    gating._warned_key = None
    gating.prime_flags_cache()


@pytest.fixture
def flags_path(tmp_path: Path) -> Path:
    """Return a temp flags-file path and clear all flag env vars.

    - Description: Points the test at ``tmp_path/flags.json`` via the
      ``SLM_FLAGS_PATH`` override while clearing every flag env var, so each
      test starts from a clean env-OFF baseline.
    - Inputs: tmp_path (pytest fixture).
    - Outputs: Path to the (initially absent) temp flags file.
    """
    path = tmp_path / "flags.json"
    cleared = {key: "" for key in _FLAG_ENV_KEYS}
    cleared["SLM_FLAGS_PATH"] = str(path)
    with patch.dict(os.environ, cleared):
        gating._warned_key = None
        # Prime against the (initially absent) temp file so the cache starts
        # from a clean all-false baseline pointed at the test path.
        gating.prime_flags_cache()
        yield path


class TestFileFlagSource:
    """Cover the file source OR-combined with the env vars."""

    def test_missing_file_all_false_env_still_honored(self, flags_path: Path) -> None:
        """Absent file -> all file flags false; env still enables."""
        assert not flags_path.exists()
        assert gating.engines_enabled() is False
        assert gating.real_notify_enabled() is False
        assert gating.real_autolock_enabled() is False
        # Env still works with the file absent.
        with patch.dict(os.environ, {"SLM_ENABLE_ENGINES": "1"}):
            assert gating.engines_enabled() is True

    def test_file_enable_engines_drives_observe_mode(self, flags_path: Path) -> None:
        """File enable_engines true -> engines_enabled + observe (dev-mock off)."""
        _write_flags(flags_path, {"enable_engines": True})
        assert gating.engines_enabled() is True
        assert gating.engines_active() is True
        assert gating.current_engine_mode() == gating.MODE_OBSERVE

    def test_file_real_notify_and_autolock(self, flags_path: Path) -> None:
        """real_notify / real_autolock flow from the file independently."""
        _write_flags(
            flags_path,
            {"enable_engines": False, "real_notify": True, "real_autolock": False},
        )
        assert gating.real_notify_enabled() is True
        assert gating.real_autolock_enabled() is False
        _write_flags(flags_path, {"real_autolock": True})
        assert gating.real_autolock_enabled() is True
        assert gating.real_notify_enabled() is False

    def test_malformed_json_all_false_no_raise(self, flags_path: Path) -> None:
        """Malformed JSON -> all-false, never raises (warning suppressed)."""
        _write_flags(flags_path, "{not valid json")
        # Must not raise, all three flags false.
        assert gating.engines_enabled() is False
        assert gating.real_notify_enabled() is False
        assert gating.real_autolock_enabled() is False

    def test_non_object_root_all_false(self, flags_path: Path) -> None:
        """A JSON array / scalar root is treated as all-false, no raise."""
        _write_flags(flags_path, "[1, 2, 3]")
        assert gating.engines_enabled() is False

    def test_unknown_keys_ignored(self, flags_path: Path) -> None:
        """Unknown keys are ignored; known keys still coerced to bool."""
        _write_flags(
            flags_path,
            {"enable_engines": 1, "bogus": "wat", "real_notify": "yes"},
        )
        assert gating.engines_enabled() is True
        # Truthy non-bool string coerces to True via bool().
        assert gating.real_notify_enabled() is True

    def test_env_or_file_precedence(self, flags_path: Path) -> None:
        """Either source enables: env true OR file true -> enabled."""
        _write_flags(flags_path, {"enable_engines": False})
        # File off, env on -> enabled.
        with patch.dict(os.environ, {"SLM_ENABLE_ENGINES": "true"}):
            assert gating.engines_enabled() is True
        # Env off, file on -> enabled.
        _write_flags(flags_path, {"enable_engines": True})
        assert gating.engines_enabled() is True
        # Both off -> disabled.
        _write_flags(flags_path, {"enable_engines": False})
        assert gating.engines_enabled() is False

    def test_dev_mock_is_env_only(self, flags_path: Path) -> None:
        """SLM_DEV_MOCK is NOT readable from the file (env-only concept)."""
        # Even if someone puts dev_mock in the file, is_dev_mock ignores it.
        _write_flags(flags_path, {"dev_mock": True, "enable_engines": False})
        assert gating.is_dev_mock() is False
        assert "dev_mock" not in gating._FILE_KEYS
        # Env still drives dev-mock.
        with patch.dict(os.environ, {"SLM_DEV_MOCK": "1"}):
            assert gating.is_dev_mock() is True
            assert gating.current_engine_mode() == gating.MODE_DEV


class TestPrimeAndHotPath:
    """Cover the prime/cache split that keeps the hot path off the disk."""

    def test_prime_populates_cache_returns_flags(self, flags_path: Path) -> None:
        """Priming returns the parsed flags and fills the module cache."""
        flags_path.write_text(
            json.dumps({"enable_engines": True, "real_notify": True}),
            encoding="utf-8",
        )
        result = gating.prime_flags_cache()
        assert result == {
            "enable_engines": True,
            "real_notify": True,
            "real_autolock": False,
        }
        assert gating._cached_flags == result
        # Hot-path helper now reflects the primed cache.
        assert gating._file_flag("enable_engines") is True

    def test_hot_path_uses_cache_no_disk_read(self, flags_path: Path) -> None:
        """After priming, the hot path reads the cache without any open()."""
        _write_flags(flags_path, {"enable_engines": True})
        assert gating.engines_enabled() is True
        # Open must NOT be called on the read path: patch it to explode, then
        # exercise every loop-reachable gate. They must answer from the cache.
        with patch("custom_components.smart_lock_manager.gating.open") as mock_open:
            mock_open.side_effect = AssertionError("hot path opened the flags file")
            assert gating.engines_enabled() is True
            assert gating.engines_active() is True
            assert gating.real_notify_enabled() is False
            assert gating.real_autolock_enabled() is False
            assert gating.current_engine_mode() == gating.MODE_OBSERVE
            mock_open.assert_not_called()

    def test_file_edit_not_seen_until_reprime(self, flags_path: Path) -> None:
        """A flags-file edit is invisible to the hot path until the next prime."""
        _write_flags(flags_path, {"enable_engines": True})
        assert gating.engines_enabled() is True
        # Edit the file WITHOUT priming — the cache is stale by design.
        flags_path.write_text(json.dumps({"enable_engines": False}), encoding="utf-8")
        assert gating.engines_enabled() is True
        # The next prime (executor / settings-change refresh) picks it up.
        gating.prime_flags_cache()
        assert gating.engines_enabled() is False

    def test_prime_missing_file_all_false(self, flags_path: Path) -> None:
        """Priming an absent file yields an all-false cache, never raises."""
        assert not flags_path.exists()
        assert gating.prime_flags_cache() == dict(gating._EMPTY_FLAGS)
        assert gating._file_flag("real_autolock") is False

    def test_prime_malformed_file_all_false(self, flags_path: Path) -> None:
        """Priming a malformed file yields all-false, never raises."""
        flags_path.write_text("{not valid json", encoding="utf-8")
        gating._warned_key = None
        assert gating.prime_flags_cache() == dict(gating._EMPTY_FLAGS)

    def test_prime_honors_flags_path_override(self, tmp_path: Path) -> None:
        """prime_flags_cache reads the SLM_FLAGS_PATH override, not the default."""
        override = tmp_path / "override-flags.json"
        override.write_text(json.dumps({"real_autolock": True}), encoding="utf-8")
        cleared = {key: "" for key in _FLAG_ENV_KEYS}
        cleared["SLM_FLAGS_PATH"] = str(override)
        with patch.dict(os.environ, cleared):
            gating._warned_key = None
            assert gating.prime_flags_cache()["real_autolock"] is True
            assert gating.real_autolock_enabled() is True


class TestAutoLockMayExecuteFileAware:
    """Regression: AutoLockEngine._may_execute must honor the FILE flag.

    The bug: ``_may_execute`` called the env-ONLY ``auto_lock.real_autolock_enabled``
    so the office HA OS install (which has no settable env and uses the flags
    FILE) could never enable real auto-lock — while the notify path already
    worked from the file. ``_may_execute`` now delegates to the file-aware
    ``gating.real_autolock_enabled`` (env OR file), so the flags-file
    ``real_autolock`` key unblocks real execution.
    """

    def test_may_execute_honors_file_real_autolock(self, flags_path: Path) -> None:
        """File real_autolock true (dev-mock off) -> _may_execute True."""
        from custom_components.smart_lock_manager.auto_lock import AutoLockEngine

        # _may_execute only reads gating + dev-mock; it never touches hass, so a
        # trivial stub stands in for the HomeAssistant instance.
        engine = AutoLockEngine(object())  # type: ignore[arg-type]

        # File false / absent, no env, dev-mock off -> OBSERVE: must NOT execute.
        assert engine._may_execute() is False

        # Flip the FILE flag on and re-prime: real execution is now permitted
        # WITHOUT any env var (the HA-OS path the bug broke).
        _write_flags(flags_path, {"real_autolock": True})
        assert engine._may_execute() is True

    def test_may_execute_false_without_file_or_env(self, flags_path: Path) -> None:
        """No file flag, no env, dev-mock off -> _may_execute False."""
        from custom_components.smart_lock_manager.auto_lock import AutoLockEngine

        engine = AutoLockEngine(object())  # type: ignore[arg-type]
        _write_flags(flags_path, {"real_autolock": False})
        assert engine._may_execute() is False
