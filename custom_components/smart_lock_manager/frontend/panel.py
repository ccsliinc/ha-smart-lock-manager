"""Smart Lock Manager Custom Panel."""

import logging

from homeassistant.components import frontend
from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)

PANEL_URL = "/smart-lock-manager-panel"
PANEL_TITLE = "Smart Lock Manager"
PANEL_ICON = "mdi:lock-smart"
PANEL_CONFIG_PANEL_DOMAIN = "smart_lock_manager_panel"


async def async_register_panel(hass: HomeAssistant) -> None:
    """Register the Smart Lock Manager panel."""
    _LOGGER.debug("Registering Smart Lock Manager panel")

    # Check if panel is already registered (for multiple lock instances)
    if (
        hasattr(hass.data, "frontend_panels")
        and "smart-lock-manager" in hass.data.frontend_panels
    ):
        _LOGGER.debug("Smart Lock Manager panel already registered, skipping")
        return

    try:
        # Register the panel with Home Assistant
        frontend.async_register_built_in_panel(
            hass,
            component_name="custom",
            sidebar_title=PANEL_TITLE,
            sidebar_icon=PANEL_ICON,
            frontend_url_path="smart-lock-manager",
            config={
                "_panel_custom": {
                    "name": "smart-lock-manager-panel",
                    "embed_iframe": False,
                    "trust": False,
                    "js_url": f"/api/smart_lock_manager/frontend/"
                    f"smart-lock-manager-panel.js?v=1.4.5&t="
                    f"{int(__import__('time').time())}",
                }
            },
            require_admin=False,
        )

        _LOGGER.info("Smart Lock Manager panel registered successfully")

    except ValueError as e:
        if "Overwriting panel" in str(e):
            _LOGGER.debug(
                "Panel already exists, this is expected with multiple " "lock instances"
            )
        else:
            _LOGGER.error("Error registering panel: %s", e)
            raise


async def async_unregister_panel(hass: HomeAssistant) -> None:
    """Unregister the Smart Lock Manager panel."""
    _LOGGER.debug("Unregistering Smart Lock Manager panel")

    # Remove the panel (this is done automatically by HA during unload)
    # but we can add cleanup logic here if needed

    _LOGGER.info("Smart Lock Manager panel unregistered")
