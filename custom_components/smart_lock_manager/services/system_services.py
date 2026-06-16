"""System-wide services for Smart Lock Manager."""

import logging
from datetime import timedelta

from homeassistant.core import HomeAssistant, ServiceCall

from ..const import (
    ATTR_AUTO_DISABLE_EXPIRED,
    ATTR_COORDINATOR_INTERVAL,
    ATTR_DEBUG_LOGGING,
    ATTR_NODE_ID,
    ATTR_SYNC_ON_LOCK_EVENTS,
    DOMAIN,
)
from ..storage import save_global_settings
from ..storage.global_settings import (
    ATTR_HEALTH_SWEEP_MINUTES,
    ATTR_OUTSIDE_HOURS_SWEEP_MINUTES,
)

_LOGGER = logging.getLogger(__name__)


class SystemServices:
    """Service handler for system-wide operations."""

    @staticmethod
    async def update_global_settings(
        hass: HomeAssistant, service_call: ServiceCall
    ) -> None:
        """Update global Smart Lock Manager settings."""
        settings = service_call.data

        # Update coordinator intervals for all locks
        if ATTR_COORDINATOR_INTERVAL in settings:
            new_interval = settings[ATTR_COORDINATOR_INTERVAL]
            _LOGGER.info("Updating coordinator interval to %s seconds", new_interval)

            for entry_id, entry_data in hass.data[DOMAIN].items():
                if isinstance(entry_data, dict):  # Skip global_settings
                    coordinator = entry_data.get("coordinator")
                    if coordinator:
                        coordinator.update_interval = timedelta(seconds=new_interval)
                        _LOGGER.info(
                            "Updated coordinator interval for entry %s", entry_id
                        )

        # Store global settings
        if "global_settings" not in hass.data[DOMAIN]:
            hass.data[DOMAIN]["global_settings"] = {}

        hass.data[DOMAIN]["global_settings"].update(settings)

        # Update debug logging level
        if ATTR_DEBUG_LOGGING in settings:
            debug_enabled = settings[ATTR_DEBUG_LOGGING]
            level = logging.DEBUG if debug_enabled else logging.INFO
            _LOGGER.setLevel(level)
            _LOGGER.info(
                "Updated debug logging to: %s", "DEBUG" if debug_enabled else "INFO"
            )

        # Update auto-disable behavior
        if ATTR_AUTO_DISABLE_EXPIRED in settings:
            auto_disable = settings[ATTR_AUTO_DISABLE_EXPIRED]
            _LOGGER.info("Updated auto-disable expired slots: %s", auto_disable)

        # Update sync on lock events
        if ATTR_SYNC_ON_LOCK_EVENTS in settings:
            sync_events = settings[ATTR_SYNC_ON_LOCK_EVENTS]
            _LOGGER.info("Updated sync on lock events: %s", sync_events)

        # Fire event to notify about global settings change
        hass.bus.async_fire(
            "smart_lock_manager_global_settings_updated",
            {
                "settings": settings,
                "coordinator_interval": settings.get(ATTR_COORDINATOR_INTERVAL),
                "debug_logging": settings.get(ATTR_DEBUG_LOGGING),
                "auto_disable_expired": settings.get(ATTR_AUTO_DISABLE_EXPIRED),
                "sync_on_lock_events": settings.get(ATTR_SYNC_ON_LOCK_EVENTS),
            },
        )

        _LOGGER.info("Updated global settings: %s", settings)

    @staticmethod
    async def set_sweep_intervals(
        hass: HomeAssistant, service_call: ServiceCall
    ) -> None:
        """Persist the engine-wide periodic-sweep cadences and reschedule.

        - Description: Sets either / both GLOBAL sweep intervals (the
          outside-hours boundary sweep and the persistent health sweep). The
          cadence is engine-wide (one timer per sweep), so it is a global
          setting, not per-zone. Validated values (positive ints, 1..1440) are
          merged onto the persisted blob, then a
          ``smart_lock_manager_global_settings_updated`` event is fired so the
          alert engine's live-refresh listener tears down + re-subscribes with
          the new cadences WITHOUT a Home Assistant restart.
        - Inputs (service_call.data): ``outside_hours_sweep_minutes`` (int,
          optional) and/or ``health_sweep_minutes`` (int, optional). At least
          one must be supplied. Both are validated/clamped by the voluptuous
          schema at the registration site.
        - Outputs: None.
        """
        updates: dict = {}
        for key in (ATTR_OUTSIDE_HOURS_SWEEP_MINUTES, ATTR_HEALTH_SWEEP_MINUTES):
            if key in service_call.data:
                updates[key] = int(service_call.data[key])

        await save_global_settings(hass, updates)
        _LOGGER.info("Updated sweep intervals: %s", updates)

        # Notify the engine to reschedule its sweeps live.
        hass.bus.async_fire(
            "smart_lock_manager_global_settings_updated",
            {"settings": updates},
        )

    @staticmethod
    async def generate_package(hass: HomeAssistant, service_call: ServiceCall) -> None:
        """Generate legacy package files (deprecated service)."""
        node_id = service_call.data[ATTR_NODE_ID]

        _LOGGER.warning(
            "generate_package service is deprecated and no longer supported. "
            "Smart Lock Manager now uses object-oriented architecture instead "
            "of YAML packages. Node ID: %s",
            node_id,
        )

        # Fire event for backward compatibility
        hass.bus.async_fire(
            "smart_lock_manager_package_generation_deprecated",
            {
                "node_id": node_id,
                "message": (
                    "Package generation is deprecated. Use the Smart Lock "
                    "Manager panel instead."
                ),
            },
        )
