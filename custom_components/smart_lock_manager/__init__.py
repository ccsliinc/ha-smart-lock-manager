"""Smart Lock Manager Integration."""

import asyncio
import logging
from datetime import timedelta
from typing import Any, Dict

import voluptuous as vol
from homeassistant.components.ozw import DOMAIN as OZW_DOMAIN
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_ENTITY_ID, EVENT_HOMEASSISTANT_STARTED
from homeassistant.core import Config, Event, HomeAssistant, ServiceCall, State
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.entity_registry import async_get_registry
from homeassistant.helpers.event import async_track_state_change
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from openzwavemqtt.const import ATTR_CODE_SLOT, CommandClass
from openzwavemqtt.exceptions import NotFoundError, NotSupportedError
from openzwavemqtt.util.node import get_node_from_manager

from .const import (
    _LOGGER,
    ATTR_CODE_SLOT_NAME,
    ATTR_NAME,
    ATTR_NODE_ID,
    ATTR_USER_CODE,
    CONF_ALARM_LEVEL,
    CONF_ALARM_TYPE,
    CONF_ENTITY_ID,
    CONF_GENERATE,
    CONF_LOCK_NAME,
    CONF_PATH,
    CONF_SENSOR_NAME,
    CONF_SLOTS,
    CONF_START,
    COORDINATOR,
    DEFAULT_CODE_SLOTS,
    DEFAULT_GENERATE,
    DEFAULT_PATH,
    DEFAULT_START,
    DOMAIN,
    ISSUE_URL,
    MANAGER,
    PLATFORMS,
    PRIMARY_LOCK,
    SERVICE_CLEAR_CODE,
    SERVICE_GENERATE_PACKAGE,
    SERVICE_REFRESH_CODES,
    SERVICE_SET_CODE,
    VERSION,
)

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


async def async_setup(hass: HomeAssistant, config: Config) -> bool:
    """Disallow configuration via YAML."""
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up is called when Home Assistant is loading our component."""
    _LOGGER.info(
        "Version %s is starting, if you have any issues please report" " them here: %s",
        VERSION,
        ISSUE_URL,
    )

    # Print startup message
    _LOGGER.info("keymaster Version %s", VERSION)
    _LOGGER.info("keymaster is starting setup...")

    hass.data.setdefault(DOMAIN, {})
    config_data = dict(entry.data)

    # If this is the first time we load the integration, create the coordinator
    if entry.entry_id not in hass.data[DOMAIN]:
        lock_name = config_data[CONF_LOCK_NAME]

        coordinator = KeymasterDataUpdateCoordinator(
            hass,
            lock_name,
            config_data[CONF_ENTITY_ID],
            config_data[CONF_SLOTS],
            config_data[CONF_START] - 1,
        )

        hass.data[DOMAIN][entry.entry_id] = {
            COORDINATOR: coordinator,
            PRIMARY_LOCK: config_data[CONF_ENTITY_ID],
            MANAGER: None,
        }

        # Fetch initial data so we have data when entities subscribe
        await coordinator.async_refresh()

        for component in PLATFORMS:
            hass.async_create_task(
                hass.config_entries.async_forward_entry_setup(entry, component)
            )

        async def _async_ozw_started(_event: Event) -> None:
            """Call when OZW starts."""
            await async_handle_ozw_events(hass, entry)

        # Check if OZW is already running
        if OZW_DOMAIN in hass.data:
            await async_handle_ozw_events(hass, entry)
        else:
            # Wait for OZW to load
            hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STARTED, _async_ozw_started)

    async def set_code(service_call: ServiceCall) -> None:
        """Set a user code."""
        entity_id = service_call.data[ATTR_ENTITY_ID]
        code_slot = service_call.data[ATTR_CODE_SLOT]
        user_code = service_call.data[ATTR_USER_CODE]
        code_slot_name = service_call.data.get(ATTR_CODE_SLOT_NAME, "")

        # Call the service
        await hass.services.async_call(
            "ozw",
            "set_usercode",
            service_data={
                ATTR_ENTITY_ID: entity_id,
                ATTR_CODE_SLOT: code_slot,
                ATTR_USER_CODE: user_code,
            },
        )

        # Update the coordinator with the new code slot name if provided
        if code_slot_name:
            coordinator = hass.data[DOMAIN][entry.entry_id][COORDINATOR]
            coordinator.data["code_slot_names"][code_slot] = code_slot_name

    async def clear_code(service_call: ServiceCall) -> None:
        """Clear a user code."""
        entity_id = service_call.data[ATTR_ENTITY_ID]
        code_slot = service_call.data[ATTR_CODE_SLOT]

        # Call the service
        await hass.services.async_call(
            "ozw",
            "clear_usercode",
            service_data={ATTR_ENTITY_ID: entity_id, ATTR_CODE_SLOT: code_slot},
        )

        # Update the coordinator to remove the code slot name
        coordinator = hass.data[DOMAIN][entry.entry_id][COORDINATOR]
        if code_slot in coordinator.data["code_slot_names"]:
            coordinator.data["code_slot_names"].pop(code_slot)

    async def refresh_codes(service_call: ServiceCall) -> None:
        """Refresh lock codes."""
        entity_id = service_call.data[ATTR_ENTITY_ID]

        # Call the service
        await hass.services.async_call(
            "ozw", "refresh_node_info", service_data={ATTR_ENTITY_ID: entity_id}
        )

    async def generate_package(service_call: ServiceCall) -> None:
        """Generate keymaster package."""
        node_id = service_call.data[ATTR_NODE_ID]
        _LOGGER.debug("Generating keymaster package for node %s", node_id)

        # This would generate the package files
        # Implementation would go here

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

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = all(
        await asyncio.gather(
            *[
                hass.config_entries.async_forward_entry_unload(entry, component)
                for component in PLATFORMS
            ]
        )
    )

    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)

        # Remove services only if this is the last instance
        if not hass.data[DOMAIN]:
            hass.services.async_remove(DOMAIN, SERVICE_SET_CODE)
            hass.services.async_remove(DOMAIN, SERVICE_CLEAR_CODE)
            hass.services.async_remove(DOMAIN, SERVICE_REFRESH_CODES)
            hass.services.async_remove(DOMAIN, SERVICE_GENERATE_PACKAGE)

    return unload_ok


async def async_handle_ozw_events(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Handle OZW events."""
    try:
        from openzwavemqtt import OZWManager, OZWOptions

        config_data = dict(entry.data)
        entity_id = config_data[CONF_ENTITY_ID]

        # Get the manager
        manager = hass.data[OZW_DOMAIN]["manager"]
        hass.data[DOMAIN][entry.entry_id][MANAGER] = manager

        # Get the node
        node_id = None
        entity_registry = await async_get_registry(hass)
        entity_entry = entity_registry.async_get(entity_id)

        if entity_entry:
            # Extract node_id from entity unique_id
            unique_id_parts = entity_entry.unique_id.split(".")
            if len(unique_id_parts) >= 2:
                node_id = int(unique_id_parts[1])

        if node_id:
            _LOGGER.debug("Found node_id %s for entity %s", node_id, entity_id)
            node = get_node_from_manager(manager, node_id)

            # Track user code changes
            async def user_code_changed(node, **kwargs):
                """Handle user code change events."""
                _LOGGER.debug("User code changed event: %s", kwargs)
                # Update the coordinator
                coordinator = hass.data[DOMAIN][entry.entry_id][COORDINATOR]
                await coordinator.async_refresh()

            node.on("value_changed", user_code_changed)

    except (ImportError, NotFoundError, NotSupportedError) as err:
        _LOGGER.error("Error setting up OZW events: %s", err)


class KeymasterDataUpdateCoordinator(DataUpdateCoordinator):
    """Class to manage fetching data from the API."""

    def __init__(
        self,
        hass: HomeAssistant,
        lock_name: str,
        entity_id: str,
        slots: int,
        start_from: int = 0,
    ) -> None:
        """Initialize."""
        self.lock_name = lock_name
        self.entity_id = entity_id
        self.slots = slots
        self.start_from = start_from

        super().__init__(
            hass,
            _LOGGER,
            name=lock_name,
            update_interval=timedelta(seconds=60),
        )

        # Initialize data dictionary
        self.data = {
            "code_slot_names": {},
            "user_codes": {},
        }

    async def _async_update_data(self) -> Dict[str, Any]:
        """Update data via library."""
        try:
            _LOGGER.debug("Updating keymaster data for %s", self.lock_name)

            # Update user codes from the lock entity
            lock_state = self.hass.states.get(self.entity_id)
            if lock_state:
                attributes = lock_state.attributes
                user_codes = attributes.get("user_codes", {})

                # Update our data
                self.data["user_codes"] = user_codes

                _LOGGER.debug("Updated user codes: %s", user_codes)

            return self.data

        except Exception as exception:
            raise UpdateFailed() from exception
