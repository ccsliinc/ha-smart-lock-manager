"""Smart Lock Manager Integration."""

from datetime import timedelta
from typing import Any, Dict

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_ENTITY_ID
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    _LOGGER,
    ATTR_CODE_SLOT,
    ATTR_CODE_SLOT_NAME,
    ATTR_NODE_ID,
    ATTR_USER_CODE,
    DOMAIN,
    ISSUE_URL,
    PLATFORMS,
    SERVICE_CLEAR_CODE,
    SERVICE_GENERATE_PACKAGE,
    SERVICE_REFRESH_CODES,
    SERVICE_SET_CODE,
    VERSION,
)

# Service schemas
CLEAR_CODE_SCHEMA = vol.Schema(
    {vol.Required(ATTR_ENTITY_ID): cv.entity_id, vol.Required(ATTR_CODE_SLOT): int}
)

SET_CODE_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_ENTITY_ID): cv.entity_id,
        vol.Required(ATTR_CODE_SLOT): int,
        vol.Required(ATTR_USER_CODE): cv.string,
        vol.Optional(ATTR_CODE_SLOT_NAME): cv.string,
    }
)

REFRESH_CODES_SCHEMA = vol.Schema({vol.Required(ATTR_ENTITY_ID): cv.entity_id})

GENERATE_PACKAGE_SCHEMA = vol.Schema({vol.Required(ATTR_NODE_ID): cv.string})


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Disallow configuration via YAML."""
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Smart Lock Manager from a config entry."""
    _LOGGER.info(
        "Smart Lock Manager Version %s starting up, please report issues to: %s",
        VERSION,
        ISSUE_URL,
    )

    hass.data.setdefault(DOMAIN, {})

    # Create coordinator for data updates
    coordinator = SmartLockManagerDataUpdateCoordinator(hass, entry)

    # Store coordinator and entry data
    hass.data[DOMAIN][entry.entry_id] = {
        "coordinator": coordinator,
        "entry": entry,
    }

    # Fetch initial data
    await coordinator.async_config_entry_first_refresh()

    # Set up platforms
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Register services
    await _register_services(hass)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)

        # Remove services only if this is the last instance
        if not hass.data[DOMAIN]:
            hass.services.async_remove(DOMAIN, SERVICE_SET_CODE)
            hass.services.async_remove(DOMAIN, SERVICE_CLEAR_CODE)
            hass.services.async_remove(DOMAIN, SERVICE_REFRESH_CODES)
            hass.services.async_remove(DOMAIN, SERVICE_GENERATE_PACKAGE)

    return unload_ok


async def _register_services(hass: HomeAssistant) -> None:
    """Register Smart Lock Manager services."""

    async def set_code(service_call: ServiceCall) -> None:
        """Set a user code."""
        _LOGGER.info("Set code service called: %s", service_call.data)
        # TODO: Implement actual code setting logic

    async def clear_code(service_call: ServiceCall) -> None:
        """Clear a user code."""
        _LOGGER.info("Clear code service called: %s", service_call.data)
        # TODO: Implement actual code clearing logic

    async def refresh_codes(service_call: ServiceCall) -> None:
        """Refresh user codes."""
        _LOGGER.info("Refresh codes service called: %s", service_call.data)
        # TODO: Implement actual code refresh logic

    async def generate_package(service_call: ServiceCall) -> None:
        """Generate package files."""
        _LOGGER.info("Generate package service called: %s", service_call.data)
        # TODO: Implement package generation logic

    # Register services
    hass.services.async_register(
        DOMAIN, SERVICE_SET_CODE, set_code, schema=SET_CODE_SCHEMA
    )
    hass.services.async_register(
        DOMAIN, SERVICE_CLEAR_CODE, clear_code, schema=CLEAR_CODE_SCHEMA
    )
    hass.services.async_register(
        DOMAIN, SERVICE_REFRESH_CODES, refresh_codes, schema=REFRESH_CODES_SCHEMA
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_GENERATE_PACKAGE,
        generate_package,
        schema=GENERATE_PACKAGE_SCHEMA,
    )


class SmartLockManagerDataUpdateCoordinator(DataUpdateCoordinator):
    """Class to manage fetching data from the lock."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Initialize."""
        self.entry = entry
        self.lock_name = entry.data.get("lock_name", "Smart Lock")

        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=30),
        )

    async def _async_update_data(self) -> Dict[str, Any]:
        """Update data via library."""
        try:
            _LOGGER.debug("Updating Smart Lock Manager data for %s", self.lock_name)

            # For now, return basic structure
            # TODO: Implement actual lock data fetching
            return {
                "user_codes": {},
                "lock_state": "unknown",
                "connection_status": True,
            }

        except Exception as exception:
            raise UpdateFailed(
                f"Error communicating with lock: {exception}"
            ) from exception
