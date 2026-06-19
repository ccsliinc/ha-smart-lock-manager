"""Authenticated zone DATA API for the Smart Lock Manager panel.

Exposes a single read-only JSON endpoint the redesigned (Phase 3) panel
consumes instead of scraping per-zone sensor states:

    GET /api/smart_lock_manager/zones   (requires_auth = True)

The payload serializes the FULL in-memory zone registry (so empty zones with
no live sensor still appear) plus the "unhomed" lock pool the "+" picker draws
from. Per-member live hardware state (lock/unlock/jammed, battery) is read from
the corresponding Home Assistant entities via ``hass.states``.

SECURITY: raw PIN codes are NEVER emitted. Each code slot reports only a
``has_code`` boolean (and non-sensitive scheduling/limit metadata). This module
deliberately avoids constructing any dict that pairs a PIN value with a slot so
the PIN-safety pre-commit guard stays satisfied.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, cast

from aiohttp import web
from homeassistant.components.http import HomeAssistantView
from homeassistant.core import HomeAssistant

from ..alert_engine import ALERT_ENGINE_KEY
from ..auto_lock import AUTO_LOCK_ENGINE_KEY
from ..gating import (
    MODE_OFF,
    current_engine_mode,
    real_autolock_enabled,
    real_notify_enabled,
)
from ..models.lock import SmartLockManagerLock
from ..storage import snooze_state_for_api
from ..zone_runtime import (
    get_unhomed_lock_entity_ids,
    get_zone_registry,
)
from .zones_serializers import (  # noqa: F401  (re-exported for tests/back-compat)
    SYNC_STATUS_ERROR,
    SYNC_STATUS_PENDING,
    SYNC_STATUS_SYNCED,
    _derive_slot_sync,
    _serialize_unhomed,
    _serialize_zone,
)

_LOGGER = logging.getLogger(__name__)


def _all_locks(hass: HomeAssistant) -> Dict[str, SmartLockManagerLock]:
    """Return every loaded SLM lock keyed by entity_id.

    Mirrors ``zone_runtime._all_locks`` but kept local so this module owns its
    view of the lock objects without importing a private helper.

    - Inputs: hass.
    - Outputs: dict entity_id -> SmartLockManagerLock.
    """
    from ..const import DOMAIN, PRIMARY_LOCK

    locks: Dict[str, SmartLockManagerLock] = {}
    for entry_data in hass.data.get(DOMAIN, {}).values():
        if isinstance(entry_data, dict):
            lock = entry_data.get(PRIMARY_LOCK)
            if lock is not None:
                locks[lock.lock_entity_id] = lock
    return locks


def _all_dev_alerts(hass: HomeAssistant) -> List[Dict[str, Any]]:
    """Return every recorded dev alert (most-recent first), or empty.

    - Description: Reads the OBSERVE-ONLY alert engine's recorded alerts. The
      engine is only present under ``SLM_DEV_MOCK``; outside dev this is always
      empty. Records are already PIN-free by construction (the engine never
      stores PINs). Each record may carry a ``notify_intents`` list — the
      DRY-RUN "would-notify" intents ({channel, recipients|targets, subject})
      rendered by the notification layer; nothing was actually sent.
    - Inputs: hass (HomeAssistant).
    - Outputs: list of alert record dicts (may be empty).
    """
    engine = hass.data.get(ALERT_ENGINE_KEY)
    if engine is None:
        return []
    return cast(List[Dict[str, Any]], engine.serialize())


def build_zones_payload(hass: HomeAssistant) -> Dict[str, Any]:
    """Build the full zones DATA payload (zones + unhomed pool + dev alerts).

    - Description: Serializes the entire in-memory zone registry (including
      empty zones) plus the unhomed lock pool, enriching each member with live
      hardware state. Also attaches the recorded alert log (top-level and
      per-zone) and the auto-lock outcome records, plus the engine-mode surface:
      ``engine_mode`` (``dev`` | ``observe`` | ``off``), the independent
      ``real_notify`` / ``real_autolock`` booleans, and the legacy
      ``observe_only`` flag (true whenever an engine is constructed). When the
      engines are off (production default) the alert/record lists are empty and
      ``engine_mode`` is ``off``. Never includes raw PINs.
    - Inputs: hass (HomeAssistant).
    - Outputs: dict with ``zones``, ``unhomed_locks``, ``dev_alerts``,
      ``auto_lock_records``, ``engine_mode``, ``real_notify``,
      ``real_autolock`` and ``observe_only``.
    """
    mode = current_engine_mode()
    locks = _all_locks(hass)
    registry = get_zone_registry(hass)
    dev_alerts = _all_dev_alerts(hass)

    # Bucket alerts by zone_id so each zone card can show its own slice.
    # Alerts whose member is unhomed (zone_id is None) only appear top-level.
    alerts_by_zone: Dict[str, List[Dict[str, Any]]] = {}
    for alert in dev_alerts:
        zone_id = alert.get("zone_id")
        if zone_id is not None:
            alerts_by_zone.setdefault(zone_id, []).append(alert)

    snooze_api = snooze_state_for_api()
    zone_snoozes = snooze_api.get("zones", {})

    zones: List[Dict[str, Any]] = []
    for zone in sorted(registry.values(), key=lambda z: z.name.lower()):
        serialized = _serialize_zone(hass, zone, locks)
        serialized["dev_alerts"] = alerts_by_zone.get(zone.zone_id, [])
        serialized["snoozed_until"] = zone_snoozes.get(zone.zone_id)
        zones.append(serialized)

    unhomed = [
        _serialize_unhomed(hass, entity_id, locks)
        for entity_id in get_unhomed_lock_entity_ids(hass)
    ]
    return {
        "zones": zones,
        "unhomed_locks": unhomed,
        "dev_alerts": dev_alerts,
        "auto_lock_records": _all_auto_lock_records(hass),
        # Phase 4d engine-mode surface. ``engine_mode`` drives the panel banner;
        # the real-flag booleans tell the user whether a real send / real
        # auto-lock could fire. ``observe_only`` (legacy) gates the alerts UI and
        # is true whenever an engine is constructed (dev OR observe).
        "engine_mode": mode,
        "real_notify": real_notify_enabled(),
        "real_autolock": real_autolock_enabled(),
        "observe_only": mode != MODE_OFF,
        "snooze": snooze_api,
    }


def _all_auto_lock_records(hass: HomeAssistant) -> List[Dict[str, Any]]:
    """Return every recorded auto-lock outcome (most-recent first), or empty.

    - Description: Reads the dev-gated AUTO-LOCK engine's outcome records. The
      engine is only present under ``SLM_DEV_MOCK``; outside dev this is always
      empty. Records carry {timestamp, zone, member, mode, result, attempts,
      method, state} and never any PIN material.
    - Inputs: hass (HomeAssistant).
    - Outputs: list of outcome record dicts (may be empty).
    """
    engine = hass.data.get(AUTO_LOCK_ENGINE_KEY)
    if engine is None:
        return []
    return cast(List[Dict[str, Any]], engine.serialize())


class SmartLockManagerZonesView(HomeAssistantView):
    """Authenticated JSON view serving the full zone model + unhomed pool."""

    requires_auth = True
    url = "/api/smart_lock_manager/zones"
    name = "api:smart_lock_manager:zones"

    def __init__(self, hass: HomeAssistant) -> None:
        """Store the hass reference for state lookups."""
        self.hass = hass

    async def get(self, request: web.Request) -> web.Response:  # noqa
        """Return the serialized zone model as JSON.

        - Inputs: request (aiohttp request; auth enforced by the base view).
        - Outputs: 200 JSON payload, or 500 with an error message on failure.
        """
        try:
            payload = build_zones_payload(self.hass)
            return self.json(payload)
        except Exception as err:  # pragma: no cover - defensive top-level guard
            _LOGGER.error("Error building zones payload: %s", err)
            return self.json({"error": str(err)}, status_code=500)
