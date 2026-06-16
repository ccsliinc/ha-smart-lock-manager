"""Persistent storage for Smart Lock Manager global engine settings.

Some engine behaviour is process-wide rather than per-zone — most notably the
two periodic alert sweeps (the outside-hours boundary sweep and the persistent
health sweep for jam / low_battery / offline). There is exactly ONE timer per
sweep for the whole engine, so the cadence is a GLOBAL setting, not a per-zone
one. This module owns that small settings blob, persisted under a dedicated key
separate from the per-lock / per-zone / alert-log keys:

  - ``smart_lock_manager_global_settings`` —
    ``{"outside_hours_sweep_minutes": int, "health_sweep_minutes": int}``

The blob is always returned fully shaped (defaults filled) so callers never
have to special-case an absent file. SECURITY: no PIN material is involved —
these are operational cadence integers only.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, cast

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

_LOGGER = logging.getLogger(__name__)

STORAGE_VERSION = 1

# Dedicated key for the global engine settings blob.
GLOBAL_SETTINGS_STORAGE_KEY = "smart_lock_manager_global_settings"

# Setting field names (single source of truth, re-used by the service schema).
ATTR_OUTSIDE_HOURS_SWEEP_MINUTES = "outside_hours_sweep_minutes"
ATTR_HEALTH_SWEEP_MINUTES = "health_sweep_minutes"

# Defaults: outside-hours sweep is FAST (catches doors at close); the health
# sweep is SLOWER because jam / battery / offline change slowly.
DEFAULT_OUTSIDE_HOURS_SWEEP_MINUTES = 15
DEFAULT_HEALTH_SWEEP_MINUTES = 60

# Sane bounds for either cadence (minutes). 1..1440 (one minute .. one day).
MIN_SWEEP_MINUTES = 1
MAX_SWEEP_MINUTES = 1440

# In-memory cache so the engine can read intervals synchronously inside a
# callback (``_subscribe``) without awaiting storage. Primed on load/save.
_CACHE: Dict[str, int] = {}


def _store(hass: HomeAssistant) -> Store:
    """Return the HA Store backing the global settings blob.

    - Inputs: hass (HomeAssistant).
    - Outputs: Store keyed by :data:`GLOBAL_SETTINGS_STORAGE_KEY`.
    """
    return Store(hass, STORAGE_VERSION, GLOBAL_SETTINGS_STORAGE_KEY)


def _shape(data: Any) -> Dict[str, int]:
    """Return a fully-shaped settings dict (defaults filled, ints coerced).

    - Inputs: data (the raw loaded value, possibly None / partial).
    - Outputs: dict with both cadence keys present as ints.
    """
    result = {
        ATTR_OUTSIDE_HOURS_SWEEP_MINUTES: DEFAULT_OUTSIDE_HOURS_SWEEP_MINUTES,
        ATTR_HEALTH_SWEEP_MINUTES: DEFAULT_HEALTH_SWEEP_MINUTES,
    }
    if isinstance(data, dict):
        for key in (ATTR_OUTSIDE_HOURS_SWEEP_MINUTES, ATTR_HEALTH_SWEEP_MINUTES):
            try:
                value = int(data[key])
            except (KeyError, TypeError, ValueError):
                continue
            if MIN_SWEEP_MINUTES <= value <= MAX_SWEEP_MINUTES:
                result[key] = value
    return result


async def load_global_settings(hass: HomeAssistant) -> Dict[str, int]:
    """Load the persisted global settings blob (always fully shaped).

    - Description: Reads the blob, fills defaults / clamps to bounds, and primes
      the synchronous cache the engine reads inside its subscribe callback.
    - Inputs: hass (HomeAssistant).
    - Outputs: dict with both cadence keys (ints).
    """
    try:
        data = await _store(hass).async_load()
    except Exception as exc:  # pragma: no cover - storage I/O guard
        _LOGGER.error("Failed to load global settings: %s", exc)
        data = None
    shaped = _shape(data)
    _CACHE.update(shaped)
    return shaped


async def save_global_settings(hass: HomeAssistant, blob: Dict[str, int]) -> None:
    """Persist the global settings blob and re-prime the cache.

    - Inputs: hass (HomeAssistant), blob (dict, partial or full; shaped here).
    - Outputs: None.
    """
    shaped = _shape({**_CACHE, **blob})
    _CACHE.update(shaped)
    try:
        await _store(hass).async_save(shaped)
    except Exception as exc:  # pragma: no cover - storage I/O guard
        _LOGGER.error("Failed to save global settings: %s", exc)


def get_cached_global_settings() -> Dict[str, int]:
    """Return the cached global settings synchronously (defaults if unprimed).

    - Description: The alert engine's ``_subscribe`` is a sync callback and
      cannot await storage, so it reads the cache primed by
      :func:`load_global_settings` / :func:`save_global_settings`. Falls back to
      the shaped defaults before the first load.
    - Inputs: none.
    - Outputs: dict with both cadence keys (ints).
    """
    if not _CACHE:
        return _shape(None)
    return cast(Dict[str, int], dict(_CACHE))
