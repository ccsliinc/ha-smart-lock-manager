"""Zone-settings editor service for Smart Lock Manager (Phase 4a, backend).

Adds ``update_zone_settings(zone_id, settings)`` — the single service the
(future) per-zone settings modal calls to edit a zone's operational config
(business hours, scheduled/idle auto-lock, alerts, notify). Kept in its own
module so the lifecycle/membership service file (``zone_services.py``) stays
under the 500-line limit.

Behaviour:

* Validates the zone exists (raises ``HomeAssistantError`` otherwise).
* Performs a PARTIAL, per-block merge: only blocks named in the payload are
  touched; unspecified blocks are preserved verbatim (see
  :func:`models.zone_settings.merge_settings`). Nested sub-keys within a named
  block also merge, so e.g. setting only ``sustained_unlock.tiers`` does not
  wipe the sibling alert toggles.
* Persists via the existing zone storage layer (``save_zone``) and refreshes
  coordinators so any sensor/UI re-renders.

No PIN material is involved — these are operational config blocks only.
"""

from __future__ import annotations

import logging
from typing import Any, Dict

from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError

from ..models.zone_settings import SETTINGS_BLOCKS, merge_settings
from ..storage import save_zone
from ..zone_runtime import get_zone_registry
from .zone_services import _refresh_all_coordinators

_LOGGER = logging.getLogger(__name__)

# Service-call field carrying the target zone id.
ATTR_ZONE_ID = "zone_id"
# Service-call field carrying the (partial) settings blocks to merge.
ATTR_SETTINGS = "settings"


class ZoneSettingsService:
    """Service handler for editing a zone's operational settings."""

    @staticmethod
    async def update_zone_settings(
        hass: HomeAssistant, service_call: ServiceCall
    ) -> None:
        """Merge a partial settings payload onto a zone and persist it.

        - Description: Looks up the zone, merges the supplied ``settings`` blocks
          onto its current :class:`~..models.zone_settings.ZoneSettings`
          (per-block, non-clobbering), persists, and refreshes coordinators.
        - Inputs (service_call.data): ``zone_id`` (str, required); ``settings``
          (dict, required) — any subset of the blocks ``business_hours`` /
          ``scheduled_auto_lock`` / ``idle_auto_lock`` / ``alerts`` / ``notify``.
        - Outputs: None.
        - Raises: HomeAssistantError if the zone id is unknown or no recognised
          settings block was supplied.
        """
        zone_id = service_call.data[ATTR_ZONE_ID]
        updates: Dict[str, Any] = dict(service_call.data.get(ATTR_SETTINGS) or {})

        registry = get_zone_registry(hass)
        zone = registry.get(zone_id)
        if zone is None:
            raise HomeAssistantError(f"Unknown zone_id: {zone_id}")

        recognised = [block for block in SETTINGS_BLOCKS if block in updates]
        if not recognised:
            raise HomeAssistantError(
                "update_zone_settings requires at least one of: "
                + ", ".join(SETTINGS_BLOCKS)
            )

        zone.settings = merge_settings(zone.settings, updates)
        await save_zone(hass, zone)

        _LOGGER.info(
            "Updated settings on zone '%s' (%s); blocks=%s",
            zone.name,
            zone_id,
            recognised,
        )

        hass.bus.async_fire(
            "smart_lock_manager_zone_settings_updated",
            {"zone_id": zone_id, "name": zone.name, "blocks": recognised},
        )
        await _refresh_all_coordinators(hass)
