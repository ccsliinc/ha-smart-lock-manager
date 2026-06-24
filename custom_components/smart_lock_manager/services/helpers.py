"""Shared helpers for Smart Lock Manager service handlers."""

from typing import Any, Dict, Optional, Tuple

from homeassistant.core import HomeAssistant

from ..const import DOMAIN, PRIMARY_LOCK
from ..models.lock import SmartLockManagerLock


def find_lock(
    hass: HomeAssistant, entity_id: str
) -> Optional[Tuple[SmartLockManagerLock, str, Dict[str, Any]]]:
    """Locate the managed lock whose ``lock_entity_id`` matches ``entity_id``.

    Walks ``hass.data[DOMAIN]`` (skipping the non-dict ``global_settings``
    entry) and returns the first matching lock together with its config entry
    id and entry-data mapping, or ``None`` if no lock matches.

    - Inputs:
        hass: the Home Assistant instance.
        entity_id: the lock entity id to find (e.g. ``lock.front_door``).
    - Outputs: ``(lock, entry_id, entry_data)`` tuple, or ``None`` if not found.
    - Example: ``result = find_lock(hass, entity_id)``
    """
    for entry_id, entry_data in hass.data[DOMAIN].items():
        if isinstance(entry_data, dict):  # Skip global_settings
            lock = entry_data.get(PRIMARY_LOCK)
            if lock and lock.lock_entity_id == entity_id:
                return lock, entry_id, entry_data
    return None
