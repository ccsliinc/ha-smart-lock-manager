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
``tmp_path`` file so nothing touches the real ``/config`` location, and resets
the module's mtime cache between writes.
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
    """Write a flags file (or raw-malformed content) and reset the cache.

    - Description: Helper that serializes ``payload`` as JSON to ``path`` (or,
      if ``payload`` is a str, writes it verbatim to simulate malformed JSON),
      then clears the module mtime cache + warn-dedup so the next read re-parses.
    - Inputs: path (Path target), payload (dict to JSON-dump, str for raw bytes,
      or None to leave the file absent).
    - Outputs: None.
    """
    if payload is not None:
        if isinstance(payload, str):
            path.write_text(payload, encoding="utf-8")
        else:
            path.write_text(json.dumps(payload), encoding="utf-8")
    # Reset the module-level mtime cache + warn guard between writes so each
    # assertion observes a fresh parse (temp files can share an mtime tick).
    gating._flags_cache["key"] = None
    gating._flags_cache["value"] = dict(gating._EMPTY_FLAGS)
    gating._warned_key = None


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
        gating._flags_cache["key"] = None
        gating._warned_key = None
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
