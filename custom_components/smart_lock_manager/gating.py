"""Centralized engine-mode gating for the Smart Lock Manager engines (Phase 4d).

This module is the SINGLE auditable source of truth for the env flags that
decide WHETHER the alert / auto-lock / notification engines are constructed and
HOW they behave. It carries no Home Assistant imports and no side effects, so
every gate can be unit-exercised under env permutations and reasoned about in
isolation.

Three independent env flags, all default OFF:

* ``SLM_DEV_MOCK`` (read via :func:`..dev_mock.is_dev_mock`) — the existing dev
  harness. Mock Z-Wave; engines drive dummy template locks; notify forced
  dry-run. Unchanged by this phase.
* ``SLM_ENABLE_ENGINES`` (:data:`ENABLE_ENGINES_ENV`) — the NEW Phase-4d flag.
  When set against the REAL office HA (dev-mock OFF) the engines run in a SAFE
  OBSERVE / DRY-RUN posture: alert detects + records against real entities,
  notify renders dry-run intents only, auto-lock records "would auto-lock"
  intents but issues NO real ``lock.lock``.
* ``SLM_ENABLE_REAL_NOTIFY`` / ``SLM_ENABLE_REAL_AUTOLOCK`` — the two
  INDEPENDENT "real action" flags. Even with engines enabled, nothing real
  happens (no SMTP / notify send, no ``lock.lock``) until these are explicitly
  turned on. Their canonical readers live next to the code they unlock
  (:func:`..notifications.real_send_enabled`,
  :func:`..auto_lock.real_autolock_enabled`); this module re-exposes thin
  wrappers so the whole truth table can be evaluated from ONE place.

Engine-construction condition (implemented in ``__init__.py`` setup and the
engines): engines are CONSTRUCTED when ``is_dev_mock() OR engines_enabled()``
(see :func:`engines_active`). The dev-only ``dev_simulate_alert`` /
``dev_trigger_autolock`` services remain gated on ``is_dev_mock()`` ALONE.

Gating truth table (DEV_MOCK / ENABLE_ENGINES -> mode):

    ===========  ==============  ==============================================
    SLM_DEV_MOCK ENABLE_ENGINES  behavior
    ===========  ==============  ==============================================
    1            (any)           "dev"     — mock locks; auto-lock drives mocks;
                                            notify dry-run. Existing behavior.
    0            1               "observe" — real entities; alert detect+record;
                                            notify dry-run intents; auto-lock
                                            records would-lock intents; NO real
                                            lock.lock (unless REAL_AUTOLOCK), NO
                                            real send (unless REAL_NOTIFY).
    0            0               "off"     — engines NOT constructed; inert
                                            (production default).
    ===========  ==============  ==============================================

REAL_NOTIFY / REAL_AUTOLOCK are orthogonal to the mode: they only ever flip a
real side-effect ON, never construct an engine and never suppress observe/dev.

File-based flag source (HA OS)
------------------------------
Home Assistant OS does not let operators set process env vars, so the office
install cannot use the env flags above. To cover that, each of the three engine
flags is ALSO sourced from a JSON file at the HA config dir, OR-combined with
its env var (``env truthy OR file truthy`` -> enabled):

    /config/smart_lock_manager_flags.json
    { "enable_engines": true, "real_notify": false, "real_autolock": false }

* ``enable_engines``  -> OR'd into :func:`engines_enabled`.
* ``real_notify``     -> OR'd into :func:`real_notify_enabled`.
* ``real_autolock``   -> OR'd into :func:`real_autolock_enabled`.

``SLM_DEV_MOCK`` stays ENV-ONLY — it is a dev-only concept and is deliberately
NOT readable from the file.

Robustness: a missing file yields all-false file values (env still honored); a
malformed/unreadable file is treated as all-false and logs a SINGLE warning,
never raising. Unknown keys are ignored; values are coerced to bool. The path
defaults to :data:`_DEFAULT_FLAGS_PATH` but can be overridden via the
``SLM_FLAGS_PATH`` env var (for unit tests / dev temp files). The read is cheap
and mtime-cached, but enabling engines via the file STILL requires an
integration reload / HA restart because the engines are CONSTRUCTED once at
setup (see :func:`engines_active`); the file is re-read live for the real-action
decision points (notify / auto-lock) on every check.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Dict

from .dev_mock import is_dev_mock

_LOGGER = logging.getLogger(__name__)

# The NEW Phase-4d flag: run the engines in a SAFE observe/dry-run posture on
# the REAL office HA (dev-mock OFF) in parallel with the live pyscripts. Default
# OFF -> in production the engines are not even constructed.
ENABLE_ENGINES_ENV = "SLM_ENABLE_ENGINES"

# Engine-mode identifiers returned by :func:`current_engine_mode`.
MODE_DEV = "dev"
MODE_OBSERVE = "observe"
MODE_OFF = "off"

# Truthy spellings accepted for every flag (case-insensitive). One definition so
# all gates agree byte-for-byte.
_TRUTHY = ("1", "true", "yes", "on")


def _env_truthy(name: str) -> bool:
    """Return whether an env var is set to a truthy value.

    - Description: Case-insensitive read of ``name``; truthy spellings are
      ``1`` / ``true`` / ``yes`` / ``on``. Anything else (including unset) is
      False. This is the ONE place flag spellings are interpreted.
    - Inputs: name (str env var name).
    - Outputs: bool.
    - Example: with ``SLM_ENABLE_ENGINES=on`` -> ``_env_truthy(ENABLE_ENGINES_ENV)``
      is True.
    """
    return os.environ.get(name, "").strip().lower() in _TRUTHY


# --- File-based flag source (HA OS, where env vars aren't settable) ---------

# Default location of the JSON flags file: the HA config dir. Overridable via
# the SLM_FLAGS_PATH env var for unit tests / dev temp files.
_DEFAULT_FLAGS_PATH = "/config/smart_lock_manager_flags.json"
_FLAGS_PATH_ENV = "SLM_FLAGS_PATH"

# The three file keys, mapped to their normalized internal names. Anything not
# listed here is ignored. SLM_DEV_MOCK is deliberately absent (env-only).
_FILE_KEYS = ("enable_engines", "real_notify", "real_autolock")

# All-false result reused for missing / malformed files.
_EMPTY_FLAGS: Dict[str, bool] = {key: False for key in _FILE_KEYS}

# mtime-based cache: (path, mtime) -> parsed flags. Keeps the per-decision read
# cheap without sacrificing correctness (a changed file changes its mtime).
_flags_cache: Dict[str, object] = {"key": None, "value": dict(_EMPTY_FLAGS)}

# Guard so the malformed-file warning is logged at most once per bad (path,
# mtime) — avoids log spam on the per-decision read path.
_warned_key: object = None


def _flags_path() -> str:
    """Return the active flags-file path (``SLM_FLAGS_PATH`` env or default).

    - Description: Resolves the JSON flags file location. The env override lets
      unit tests and dev point at a temp file; production uses the HA config
      default.
    - Inputs: none (reads process environment).
    - Outputs: str filesystem path.
    """
    override = os.environ.get(_FLAGS_PATH_ENV, "").strip()
    return override or _DEFAULT_FLAGS_PATH


def _read_flags_file() -> Dict[str, bool]:
    """Read the JSON flags file, returning a {key: bool} map (never raises).

    - Description: Loads the file at :func:`_flags_path` and coerces the known
      keys (:data:`_FILE_KEYS`) to bool. A missing file -> all-false. A
      malformed / unreadable file or a non-object JSON root -> all-false plus a
      SINGLE logged warning (deduped by path+mtime). Unknown keys are ignored.
      Results are mtime-cached so repeated per-decision reads stay cheap.
    - Inputs: none (reads the filesystem + process environment).
    - Outputs: Dict[str, bool] with exactly the :data:`_FILE_KEYS` keys.
    """
    global _warned_key

    path = _flags_path()
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        # Missing / unreadable file: not an error condition — env still honored.
        return dict(_EMPTY_FLAGS)

    cache_key = (path, mtime)
    if _flags_cache["key"] == cache_key:
        cached = _flags_cache["value"]
        if isinstance(cached, dict):
            return {key: bool(cached.get(key, False)) for key in _FILE_KEYS}
        return dict(_EMPTY_FLAGS)

    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        if not isinstance(data, dict):
            raise ValueError("flags file root is not a JSON object")
        result = {key: bool(data.get(key, False)) for key in _FILE_KEYS}
    except (OSError, ValueError) as err:
        if _warned_key != cache_key:
            _LOGGER.warning(
                "Smart Lock Manager flags file %s is malformed or unreadable "
                "(%s); treating all file flags as off",
                path,
                err,
            )
            _warned_key = cache_key
        result = dict(_EMPTY_FLAGS)

    _flags_cache["key"] = cache_key
    _flags_cache["value"] = dict(result)
    return dict(result)


def _file_flag(key: str) -> bool:
    """Return a single file flag's bool value (all-false on any read failure).

    - Description: Convenience wrapper over :func:`_read_flags_file` for the one
      key a caller cares about. Robustness is fully handled by the reader.
    - Inputs: key (str) — one of :data:`_FILE_KEYS`.
    - Outputs: bool.
    """
    return _read_flags_file().get(key, False)


def engines_enabled() -> bool:
    """Return whether the Phase-4d ``SLM_ENABLE_ENGINES`` flag is truthy.

    - Description: Reads :data:`ENABLE_ENGINES_ENV` OR-combined with the flags
      file's ``enable_engines`` (env truthy OR file truthy -> enabled). Default
      OFF. This is the switch that lets the engines run in OBSERVE mode against
      the real office HA; it does NOT by itself enable any real side-effect.
    - Inputs: none (reads process environment + flags file).
    - Outputs: bool.
    """
    return _env_truthy(ENABLE_ENGINES_ENV) or _file_flag("enable_engines")


def engines_active() -> bool:
    """Return whether the engines should be CONSTRUCTED at all.

    - Description: The combined construction guard used in ``__init__.py`` and
      the engines: engines run under dev-mock OR when explicitly enabled. With
      both off (production default) the engines are never built and are inert.
    - Inputs: none.
    - Outputs: bool — ``is_dev_mock() or engines_enabled()``.
    """
    return is_dev_mock() or engines_enabled()


def real_notify_enabled() -> bool:
    """Return whether the explicit real-NOTIFY flag is truthy (independent).

    - Description: The env reader (:func:`..notifications.real_send_enabled`)
      OR-combined with the flags file's ``real_notify`` (env truthy OR file
      truthy -> enabled), so the whole truth table is evaluable from this
      module AND the HA-OS file source is honored. Default OFF; even when set,
      the dispatcher still suppresses real sends whenever dry-run is forced
      (dev-mock).
    - Inputs: none (reads process environment + flags file).
    - Outputs: bool.
    """
    # Imported lazily to avoid a circular import (notifications imports nothing
    # from gating, but keeping this local keeps the dependency one-directional).
    from .notifications import real_send_enabled

    return real_send_enabled() or _file_flag("real_notify")


def real_autolock_enabled() -> bool:
    """Return whether the explicit real-AUTOLOCK flag is truthy (independent).

    - Description: The env reader (:func:`..auto_lock.real_autolock_enabled`)
      OR-combined with the flags file's ``real_autolock`` (env truthy OR file
      truthy -> enabled), so the whole truth table is evaluable from this
      module AND the HA-OS file source is honored. Default OFF; it is the ONLY
      switch that lets the auto-lock engine issue a real ``lock.lock`` in
      production.
    - Inputs: none (reads process environment + flags file).
    - Outputs: bool.
    """
    from .auto_lock import real_autolock_enabled as _real_autolock

    return _real_autolock() or _file_flag("real_autolock")


def current_engine_mode() -> str:
    """Return the active engine mode: ``dev`` | ``observe`` | ``off``.

    - Description: The single derivation of the mode the API surfaces and the
      engines log. Dev-mock wins (existing dev behavior); else engines-enabled
      means OBSERVE on real entities; else OFF (not constructed).
    - Inputs: none.
    - Outputs: str — :data:`MODE_DEV`, :data:`MODE_OBSERVE`, or :data:`MODE_OFF`.
    """
    if is_dev_mock():
        return MODE_DEV
    if engines_enabled():
        return MODE_OBSERVE
    return MODE_OFF
