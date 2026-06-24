"""Persistent storage for Smart Lock Manager alert SNOOZE state.

A snooze temporarily SUPPRESSES alert notifications (alerts are still recorded)
either globally or for one zone, and AUTO-EXPIRES at a stored epoch deadline.
There is one process-wide snooze blob, persisted under a dedicated key separate
from the per-lock / per-zone / alert-log / global-settings keys:

  - ``smart_lock_manager_snooze`` —
    ``{"global_until": float|None, "zones": {zone_id: float}}``

``global_until`` / each zone value is a UNIX epoch deadline (seconds). The sync
accessors compare against the current time so an expired snooze simply stops
matching — no sweep/cleanup needed. SECURITY: no PIN material is involved —
these are operational epoch deadlines only.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime
from typing import Any, Dict, Optional

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

_LOGGER = logging.getLogger(__name__)

STORAGE_VERSION = 1

# Dedicated key for the snooze blob.
SNOOZE_STORAGE_KEY = "smart_lock_manager_snooze"

# In-memory cache so the engine can read snooze state synchronously inside its
# notify path (``_notify``) without awaiting storage. Primed on load/save.
# Shape: ``{"global_until": float|None, "zones": {zone_id: float}}``.
_CACHE: Dict[str, Any] = {}


def _store(hass: HomeAssistant) -> Store:
    """Return the HA Store backing the snooze blob.

    - Inputs: hass (HomeAssistant).
    - Outputs: Store keyed by :data:`SNOOZE_STORAGE_KEY`.
    """
    return Store(hass, STORAGE_VERSION, SNOOZE_STORAGE_KEY)


def _shape(data: Any) -> Dict[str, Any]:
    """Return a fully-shaped snooze dict (validated, defaults filled).

    - Description: ``global_until`` must be a number or None; each zone value
      must be a number. Bad / partial input collapses to the empty shape.
    - Inputs: data (the raw loaded value, possibly None / partial).
    - Outputs: dict ``{"global_until": float|None, "zones": {str: float}}``.
    """
    empty: Dict[str, Any] = {"global_until": None, "zones": {}}
    if not isinstance(data, dict):
        return empty

    global_until: Optional[float] = None
    raw_global = data.get("global_until")
    if raw_global is not None:
        if isinstance(raw_global, (int, float)) and not isinstance(raw_global, bool):
            global_until = float(raw_global)
        else:
            return empty

    zones: Dict[str, float] = {}
    raw_zones = data.get("zones")
    if isinstance(raw_zones, dict):
        for zone_id, value in raw_zones.items():
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                zones[str(zone_id)] = float(value)
            else:
                return empty
    return {"global_until": global_until, "zones": zones}


def _ensure_cache() -> None:
    """Ensure the cache has its baseline keys before a sync mutation.

    - Inputs: none.
    - Outputs: None (mutates :data:`_CACHE` in place).
    """
    if "global_until" not in _CACHE:
        _CACHE["global_until"] = None
    if "zones" not in _CACHE or not isinstance(_CACHE.get("zones"), dict):
        _CACHE["zones"] = {}


async def load_snooze(hass: HomeAssistant) -> Dict[str, Any]:
    """Load the persisted snooze blob (always fully shaped) and prime the cache.

    - Inputs: hass (HomeAssistant).
    - Outputs: dict ``{"global_until": float|None, "zones": {str: float}}``.
    """
    try:
        data = await _store(hass).async_load()
    except Exception as exc:  # pragma: no cover - storage I/O guard
        _LOGGER.error("Failed to load snooze state: %s", exc)
        return _shape(None)
    shaped = _shape(data)
    _CACHE.clear()
    _CACHE.update(shaped)
    return shaped


async def save_snooze(hass: HomeAssistant, blob: Dict[str, Any]) -> None:
    """Merge a snooze blob onto the cache, persist it, and re-prime the cache.

    - Description: ``global_until`` is taken from ``blob`` when present, else the
      cache; zones are merged (cache first, blob wins). The result is shaped,
      cached, and written.
    - Inputs: hass (HomeAssistant), blob (dict, partial or full).
    - Outputs: None.
    """
    _ensure_cache()
    cache_zones = dict(_CACHE.get("zones") or {})
    blob_zones = dict((blob or {}).get("zones") or {})
    if blob and "global_until" in blob:
        global_until = blob.get("global_until")
    else:
        global_until = _CACHE.get("global_until")
    shaped = _shape(
        {"global_until": global_until, "zones": {**cache_zones, **blob_zones}}
    )
    _CACHE.clear()
    _CACHE.update(shaped)
    try:
        await _store(hass).async_save(shaped)
    except Exception as exc:  # pragma: no cover - storage I/O guard
        _LOGGER.error("Failed to save snooze state: %s", exc)


def get_cached_snooze() -> Dict[str, Any]:
    """Return a copy of the cached snooze state synchronously.

    - Inputs: none.
    - Outputs: dict ``{"global_until": float|None, "zones": {str: float}}``.
    """
    return {
        "global_until": _CACHE.get("global_until"),
        "zones": dict(_CACHE.get("zones") or {}),
    }


def global_snooze_active(now_epoch: Optional[float] = None) -> bool:
    """Return whether a global snooze is currently active (auto-expiring).

    - Inputs: now_epoch (float epoch, optional; defaults to ``time.time()``).
    - Outputs: bool — True when a global deadline exists and is still future.
    """
    now = now_epoch if now_epoch is not None else time.time()
    gu = _CACHE.get("global_until")
    return gu is not None and now < gu


def zone_snooze_active(zone_id: str, now_epoch: Optional[float] = None) -> bool:
    """Return whether a per-zone snooze is currently active (auto-expiring).

    - Inputs: zone_id (str), now_epoch (float epoch, optional; defaults now).
    - Outputs: bool — True when that zone's deadline exists and is still future.
    """
    now = now_epoch if now_epoch is not None else time.time()
    deadline = (_CACHE.get("zones") or {}).get(zone_id)
    return deadline is not None and now < deadline


def snooze_active(zone_id: Optional[str], now_epoch: Optional[float] = None) -> bool:
    """Return whether alerts are snoozed for a zone (global OR per-zone).

    - Inputs: zone_id (str|None member's zone), now_epoch (float epoch, opt).
    - Outputs: bool — True when the global snooze OR that zone's snooze is live.
    """
    if global_snooze_active(now_epoch):
        return True
    return zone_id is not None and zone_snooze_active(zone_id, now_epoch)


def set_global_snooze(until_epoch: float) -> None:
    """Set the global snooze deadline in memory.

    - Inputs: until_epoch (float UNIX epoch deadline).
    - Outputs: None (mutates :data:`_CACHE`; persist via :func:`save_snooze`).
    """
    _ensure_cache()
    _CACHE["global_until"] = float(until_epoch)


def set_zone_snooze(zone_id: str, until_epoch: float) -> None:
    """Set a per-zone snooze deadline in memory.

    - Inputs: zone_id (str), until_epoch (float UNIX epoch deadline).
    - Outputs: None (mutates :data:`_CACHE`; persist via :func:`save_snooze`).
    """
    _ensure_cache()
    _CACHE.setdefault("zones", {})[zone_id] = float(until_epoch)


def clear_global_snooze() -> None:
    """Clear the global snooze deadline in memory.

    - Inputs: none.
    - Outputs: None (mutates :data:`_CACHE`; persist via :func:`save_snooze`).
    """
    _ensure_cache()
    _CACHE["global_until"] = None


def clear_zone_snooze(zone_id: str) -> None:
    """Clear a per-zone snooze deadline in memory.

    - Inputs: zone_id (str).
    - Outputs: None (mutates :data:`_CACHE`; persist via :func:`save_snooze`).
    """
    _ensure_cache()
    _CACHE.get("zones", {}).pop(zone_id, None)


def snooze_state_for_api() -> dict:
    """Return the snooze state as ISO strings for the panel DATA API.

    - Description: Converts each epoch deadline to an ISO-8601 string via
      ``datetime.fromtimestamp``. ``global_until`` of None stays None. Includes
      every zone currently present in the cache.
    - Inputs: none.
    - Outputs: dict ``{"global_until": str|None, "zones": {zone_id: str}}``.
    """
    gu = _CACHE.get("global_until")
    global_iso = datetime.fromtimestamp(gu).isoformat() if gu is not None else None
    zones_iso = {
        zone_id: datetime.fromtimestamp(deadline).isoformat()
        for zone_id, deadline in (_CACHE.get("zones") or {}).items()
    }
    return {"global_until": global_iso, "zones": zones_iso}
