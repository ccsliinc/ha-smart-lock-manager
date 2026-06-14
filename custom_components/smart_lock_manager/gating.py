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
"""

from __future__ import annotations

import os

from .dev_mock import is_dev_mock

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


def engines_enabled() -> bool:
    """Return whether the Phase-4d ``SLM_ENABLE_ENGINES`` flag is truthy.

    - Description: Reads :data:`ENABLE_ENGINES_ENV`. Default OFF. This is the
      switch that lets the engines run in OBSERVE mode against the real office
      HA; it does NOT by itself enable any real side-effect.
    - Inputs: none (reads process environment).
    - Outputs: bool.
    """
    return _env_truthy(ENABLE_ENGINES_ENV)


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

    - Description: Thin re-export of :func:`..notifications.real_send_enabled`
      so the whole truth table is evaluable from this module. Default OFF; even
      when set, the dispatcher still suppresses real sends whenever dry-run is
      forced (dev-mock).
    - Inputs: none.
    - Outputs: bool.
    """
    # Imported lazily to avoid a circular import (notifications imports nothing
    # from gating, but keeping this local keeps the dependency one-directional).
    from .notifications import real_send_enabled

    return real_send_enabled()


def real_autolock_enabled() -> bool:
    """Return whether the explicit real-AUTOLOCK flag is truthy (independent).

    - Description: Thin re-export of :func:`..auto_lock.real_autolock_enabled`
      so the whole truth table is evaluable from this module. Default OFF; it is
      the ONLY switch that lets the auto-lock engine issue a real ``lock.lock``
      in production.
    - Inputs: none.
    - Outputs: bool.
    """
    from .auto_lock import real_autolock_enabled as _real_autolock

    return _real_autolock()


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
