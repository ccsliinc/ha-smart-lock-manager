"""Persistent storage for Smart Lock Manager zones.

Zones are persisted under their OWN storage keys, separate from the legacy
per-lock keys (which remain intact as a migration fallback):

  - ``smart_lock_manager_zone_{zone_id}``  — one HA ``Store`` per zone.
  - ``smart_lock_manager_zone_migration``  — the idempotency marker that
    records whether the one-time parent/child -> zone migration has run.

Loading all zones on startup requires enumerating the keys, which HA's
``Store`` abstraction does not expose. We therefore scan the ``.storage``
directory (``hass.config.path('.storage')``) for files matching the zone key
prefix and hand each key to a ``Store`` for normal (versioned) loading.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional, cast

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from ..models.zone import Zone

_LOGGER = logging.getLogger(__name__)

STORAGE_VERSION = 1

# Key prefix for individual zone records. A zone's key is
# ``{ZONE_KEY_PREFIX}{zone_id}``.
ZONE_KEY_PREFIX = "smart_lock_manager_zone_"

# Dedicated key for the migration idempotency marker. Chosen so it does NOT
# collide with any 32-hex ``zone_id`` (it ends in ``migration``, and zone ids
# are hex only), but the loader still filters it out explicitly for safety.
MIGRATION_MARKER_KEY = "smart_lock_manager_zone_migration"


def _zone_store(hass: HomeAssistant, zone_id: str) -> Store:
    """Return the HA Store for a single zone.

    - Inputs: hass (HomeAssistant), zone_id (str).
    - Outputs: Store keyed by ``smart_lock_manager_zone_{zone_id}``.
    """
    return Store(hass, STORAGE_VERSION, f"{ZONE_KEY_PREFIX}{zone_id}")


def _migration_store(hass: HomeAssistant) -> Store:
    """Return the HA Store for the migration marker."""
    return Store(hass, STORAGE_VERSION, MIGRATION_MARKER_KEY)


async def save_zone(hass: HomeAssistant, zone: Zone) -> None:
    """Persist a single zone to its own storage key.

    - Inputs: hass (HomeAssistant), zone (Zone).
    - Outputs: None.
    """
    try:
        await _zone_store(hass, zone.zone_id).async_save(zone.to_dict())
        _LOGGER.debug("Saved zone %s (%s)", zone.zone_id, zone.name)
    except Exception as exc:  # pragma: no cover - storage I/O guard
        _LOGGER.error("Failed to save zone %s: %s", zone.zone_id, exc)


async def delete_zone_storage(hass: HomeAssistant, zone_id: str) -> None:
    """Remove a zone's persisted record.

    - Inputs: hass (HomeAssistant), zone_id (str).
    - Outputs: None.
    """
    try:
        await _zone_store(hass, zone_id).async_remove()
        _LOGGER.debug("Removed zone storage for %s", zone_id)
    except Exception as exc:  # pragma: no cover - storage I/O guard
        _LOGGER.error("Failed to remove zone storage %s: %s", zone_id, exc)


def _list_zone_ids(hass: HomeAssistant) -> List[str]:
    """Enumerate persisted zone ids by scanning the .storage directory.

    - Description: HA ``Store`` cannot list keys, so read the storage dir and
      derive zone ids from filenames matching the zone key prefix. The
      migration-marker key is explicitly excluded.
    - Inputs: hass (HomeAssistant).
    - Outputs: list of zone_id strings (may be empty).
    """
    storage_dir = hass.config.path(".storage")
    if not os.path.isdir(storage_dir):
        return []
    zone_ids: List[str] = []
    for fname in os.listdir(storage_dir):
        if not fname.startswith(ZONE_KEY_PREFIX):
            continue
        if fname == MIGRATION_MARKER_KEY:
            continue
        zone_ids.append(fname[len(ZONE_KEY_PREFIX) :])
    return zone_ids


async def load_all_zones(hass: HomeAssistant) -> Dict[str, Zone]:
    """Load every persisted zone on startup.

    - Description: Scan the ``.storage`` directory for zone keys, load each via
      a versioned ``Store``, and rebuild ``Zone`` objects.
    - Inputs: hass (HomeAssistant).
    - Outputs: dict mapping ``zone_id`` -> ``Zone`` (empty if none persisted).
    """
    zones: Dict[str, Zone] = {}
    for zone_id in await hass.async_add_executor_job(_list_zone_ids, hass):
        try:
            data = await _zone_store(hass, zone_id).async_load()
            if not data:
                continue
            zone = Zone.from_dict(data)
            zones[zone.zone_id] = zone
            _LOGGER.debug(
                "Loaded zone %s (%s) with %d members",
                zone.zone_id,
                zone.name,
                len(zone.member_lock_entity_ids),
            )
        except Exception as exc:  # pragma: no cover - storage I/O guard
            _LOGGER.error("Failed to load zone %s: %s", zone_id, exc)
    return zones


async def load_migration_marker(hass: HomeAssistant) -> Optional[Dict[str, Any]]:
    """Return the persisted migration marker, or None if never run.

    - Outputs: the marker dict (e.g. ``{"migrated": True, ...}``) or None.
    """
    try:
        return cast(Optional[Dict[str, Any]], await _migration_store(hass).async_load())
    except Exception as exc:  # pragma: no cover - storage I/O guard
        _LOGGER.error("Failed to load zone migration marker: %s", exc)
        return None


async def save_migration_marker(hass: HomeAssistant, marker: Dict[str, Any]) -> None:
    """Persist the migration idempotency marker.

    - Inputs: hass (HomeAssistant), marker (dict — must contain ``migrated``).
    - Outputs: None.
    """
    try:
        await _migration_store(hass).async_save(marker)
        _LOGGER.debug("Saved zone migration marker: %s", marker)
    except Exception as exc:  # pragma: no cover - storage I/O guard
        _LOGGER.error("Failed to save zone migration marker: %s", exc)
