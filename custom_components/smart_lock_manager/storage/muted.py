"""Persistent storage for Smart Lock Manager per-member alert MUTES.

A mute PERMANENTLY suppresses alert notifications for one lock member, either
for EVERY alert type (``"all"``) or for one specific alert type, until it is
manually cleared. Unlike a snooze (:mod:`.snooze`), a mute is NOT time-based and
never auto-expires — it is sticky until an explicit unmute. There is one
process-wide muted blob, persisted under a dedicated key separate from the
per-lock / per-zone / alert-log / global-settings / snooze keys:

  - ``smart_lock_manager_muted`` —
    ``{"muted": {member_entity_id: ["all"] | [alert_type, ...]}}``

The persisted per-member value is a sorted list (JSON-friendly). In memory the
cache holds each member's muted types as a ``set`` for cheap membership tests;
:func:`_shape` and the save path convert sets to sorted lists for persistence.
The sync accessors read the cache so the engine notify path can check mutes
without awaiting storage. SECURITY: no PIN material is involved — these are
operational alert-type identifiers only.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Set

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

_LOGGER = logging.getLogger(__name__)

STORAGE_VERSION = 1

# Dedicated key for the muted blob.
MUTED_STORAGE_KEY = "smart_lock_manager_muted"

# Sentinel meaning "mute every alert type for this member".
MUTE_ALL = "all"

# In-memory cache so the engine can read mute state synchronously inside its
# notify path (``_notify``) without awaiting storage. Primed on load/save.
# Shape: ``{"muted": {member_entity_id: set([alert_type | "all", ...])}}``.
_CACHE: Dict[str, Any] = {}


def _store(hass: HomeAssistant) -> Store:
    """Return the HA Store backing the muted blob.

    - Inputs: hass (HomeAssistant).
    - Outputs: Store keyed by :data:`MUTED_STORAGE_KEY`.
    """
    return Store(hass, STORAGE_VERSION, MUTED_STORAGE_KEY)


def _shape(data: Any) -> Dict[str, Any]:
    """Return a fully-shaped muted dict with each member's types as a SORTED LIST.

    - Description: Each member maps to a list of alert-type strings (or the lone
      ``"all"`` sentinel). Non-dict / non-string / empty inputs collapse to the
      empty shape. The returned per-member value is a sorted list of unique
      strings — JSON-friendly and stable for persistence.
    - Inputs: data (the raw loaded value, possibly None / partial).
    - Outputs: dict ``{"muted": {entity_id: [alert_type, ...]}}``.
    """
    empty: Dict[str, Any] = {"muted": {}}
    if not isinstance(data, dict):
        return empty

    raw_muted = data.get("muted")
    if not isinstance(raw_muted, dict):
        return empty

    muted: Dict[str, List[str]] = {}
    for entity_id, value in raw_muted.items():
        types: Set[str] = set()
        if isinstance(value, (list, set, tuple)):
            for item in value:
                if isinstance(item, str) and item:
                    types.add(item)
        if types:
            muted[str(entity_id)] = sorted(types)
    return {"muted": muted}


def _ensure_cache() -> None:
    """Ensure the cache has its baseline ``muted`` key before a sync mutation.

    - Inputs: none.
    - Outputs: None (mutates :data:`_CACHE` in place).
    """
    if "muted" not in _CACHE or not isinstance(_CACHE.get("muted"), dict):
        _CACHE["muted"] = {}


def _prime_from_shaped(shaped: Dict[str, Any]) -> None:
    """Replace the cache from a shaped (list-valued) muted dict, storing sets.

    - Inputs: shaped (dict ``{"muted": {entity_id: [type, ...]}}``).
    - Outputs: None (mutates :data:`_CACHE`; per-member values become sets).
    """
    _CACHE.clear()
    _CACHE["muted"] = {
        entity_id: set(types) for entity_id, types in shaped.get("muted", {}).items()
    }


async def load_muted(hass: HomeAssistant) -> Dict[str, Any]:
    """Load the persisted muted blob (always fully shaped) and prime the cache.

    - Inputs: hass (HomeAssistant).
    - Outputs: dict ``{"muted": {entity_id: [alert_type, ...]}}`` (sorted lists).
    """
    try:
        data = await _store(hass).async_load()
    except Exception as exc:  # pragma: no cover - storage I/O guard
        _LOGGER.error("Failed to load muted state: %s", exc)
        return _shape(None)
    shaped = _shape(data)
    _prime_from_shaped(shaped)
    return shaped


async def save_muted(hass: HomeAssistant, blob: Dict[str, Any]) -> None:
    """Merge a muted blob onto the cache, persist it (lists), and re-prime.

    - Description: The blob's per-member type lists/sets are MERGED onto the
      cache (cache first, blob wins per member key — a present member key in the
      blob REPLACES that member's set). The result is shaped to sorted lists,
      cached as sets, and written.
    - Inputs: hass (HomeAssistant), blob (dict, partial or full).
    - Outputs: None.
    """
    _ensure_cache()
    cache_muted = {
        entity_id: set(types)
        for entity_id, types in (_CACHE.get("muted") or {}).items()
    }
    blob_muted = (blob or {}).get("muted") or {}
    for entity_id, types in blob_muted.items():
        if isinstance(types, (list, set, tuple)):
            cache_muted[entity_id] = {t for t in types if isinstance(t, str) and t}
    shaped = _shape({"muted": {k: sorted(v) for k, v in cache_muted.items()}})
    _prime_from_shaped(shaped)
    try:
        await _store(hass).async_save(shaped)
    except Exception as exc:  # pragma: no cover - storage I/O guard
        _LOGGER.error("Failed to save muted state: %s", exc)


def get_cached_muted() -> Dict[str, Any]:
    """Return a copy of the cached muted state synchronously (sorted lists).

    - Description: Returns a JSON-friendly snapshot suitable for handing to
      :func:`save_muted` — per-member sets are converted to sorted lists.
    - Inputs: none.
    - Outputs: dict ``{"muted": {entity_id: [alert_type, ...]}}``.
    """
    return {
        "muted": {
            entity_id: sorted(types)
            for entity_id, types in (_CACHE.get("muted") or {}).items()
        }
    }


def is_muted(member_entity_id: str, alert_type: str) -> bool:
    """Return whether a (member, alert_type) pair is currently muted.

    - Description: True when the member has the ``"all"`` sentinel muted OR the
      exact ``alert_type`` muted.
    - Inputs: member_entity_id (str), alert_type (str alert-type id).
    - Outputs: bool.
    """
    types = (_CACHE.get("muted") or {}).get(member_entity_id)
    if not types:
        return False
    return MUTE_ALL in types or alert_type in types


def set_mute(member_entity_id: str, alert_type: str) -> None:
    """Add an alert type (or ``"all"``) to a member's muted set in memory.

    - Inputs: member_entity_id (str), alert_type (str id, or ``"all"``).
    - Outputs: None (mutates :data:`_CACHE`; persist via :func:`save_muted`).
    """
    _ensure_cache()
    _CACHE["muted"].setdefault(member_entity_id, set()).add(alert_type)


def clear_mute(member_entity_id: str, alert_type: str) -> None:
    """Remove a mute for a member in memory (one type, or ALL via ``"all"``).

    - Description: When ``alert_type == "all"`` every mute for the member is
      removed (the member key is dropped). Otherwise only that one type is
      removed; if the member's set becomes empty its key is dropped too.
    - Inputs: member_entity_id (str), alert_type (str id, or ``"all"``).
    - Outputs: None (mutates :data:`_CACHE`; persist via :func:`save_muted`).
    """
    _ensure_cache()
    muted = _CACHE["muted"]
    if alert_type == MUTE_ALL:
        muted.pop(member_entity_id, None)
        return
    types = muted.get(member_entity_id)
    if not types:
        return
    types.discard(alert_type)
    if not types:
        muted.pop(member_entity_id, None)


def muted_state_for_api() -> dict:
    """Return the muted state as JSON-safe sorted lists for the panel DATA API.

    - Inputs: none.
    - Outputs: dict ``{"muted": {entity_id: [alert_type, ...]}}`` (sorted).
    """
    return {
        "muted": {
            entity_id: sorted(types)
            for entity_id, types in (_CACHE.get("muted") or {}).items()
        }
    }
