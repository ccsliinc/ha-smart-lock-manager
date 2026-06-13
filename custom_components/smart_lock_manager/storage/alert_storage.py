"""Persistent storage for the dev-only Smart Lock Manager alert log.

The OBSERVE-ONLY alert detection engine (:mod:`..alert_engine`) records every
detected alert (and recovery) into a single rolling, capped list. That list is
persisted here so it survives a Home Assistant restart, under one dedicated
storage key separate from the per-lock and per-zone keys:

  - ``smart_lock_manager_dev_alerts`` — ``{"alerts": [...], "alerted_state": {...}}``

This blob is DEV-ONLY: it is read/written exclusively from the alert engine,
which itself only instantiates under ``SLM_DEV_MOCK``. In production (flag
unset) nothing here is ever touched.

SECURITY: alert records carry only entity ids, door names, severities and
human-readable messages — never PIN codes.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, cast

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

_LOGGER = logging.getLogger(__name__)

STORAGE_VERSION = 1

# Dedicated key for the dev alert log + persisted alerted-state map.
ALERT_STORAGE_KEY = "smart_lock_manager_dev_alerts"


def _alert_store(hass: HomeAssistant) -> Store:
    """Return the HA Store backing the dev alert log.

    - Inputs: hass (HomeAssistant).
    - Outputs: Store keyed by :data:`ALERT_STORAGE_KEY`.
    """
    return Store(hass, STORAGE_VERSION, ALERT_STORAGE_KEY)


async def load_alert_log(hass: HomeAssistant) -> Dict[str, Any]:
    """Load the persisted dev alert log blob, or an empty blob if absent.

    - Inputs: hass (HomeAssistant).
    - Outputs: dict ``{"alerts": list, "alerted_state": dict}`` (always shaped,
      even when nothing was persisted yet).
    """
    try:
        data = await _alert_store(hass).async_load()
    except Exception as exc:  # pragma: no cover - storage I/O guard
        _LOGGER.error("Failed to load dev alert log: %s", exc)
        data = None
    if not isinstance(data, dict):
        return {"alerts": [], "alerted_state": {}}
    data.setdefault("alerts", [])
    data.setdefault("alerted_state", {})
    return cast(Dict[str, Any], data)


async def save_alert_log(hass: HomeAssistant, blob: Dict[str, Any]) -> None:
    """Persist the dev alert log blob.

    - Inputs: hass (HomeAssistant), blob (dict with ``alerts`` +
      ``alerted_state`` keys).
    - Outputs: None.
    """
    try:
        await _alert_store(hass).async_save(blob)
    except Exception as exc:  # pragma: no cover - storage I/O guard
        _LOGGER.error("Failed to save dev alert log: %s", exc)
