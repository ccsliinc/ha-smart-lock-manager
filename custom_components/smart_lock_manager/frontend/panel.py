"""Smart Lock Manager Custom Panel."""

import logging

from homeassistant.components import frontend
from homeassistant.core import HomeAssistant

from ..const import DOMAIN

_LOGGER = logging.getLogger(__name__)

# Panel url path (also the key under ``hass.data[frontend.DATA_PANELS]``).
PANEL_URL_PATH = "smart-lock-manager"

PANEL_TITLE = "Smart Lock Manager"
PANEL_ICON = "mdi:lock-smart"


async def async_register_panel(hass: HomeAssistant) -> None:
    """Register the Smart Lock Manager panel."""
    _LOGGER.debug("Registering Smart Lock Manager panel")

    # Check if panel is already registered (for multiple lock instances).
    # ``hass.data`` is a plain dict, so the panel registry is a KEY in it, not
    # an attribute — ``hasattr(hass.data, "frontend_panels")`` was ALWAYS False
    # and this dedup guard never fired (registration relied on the ValueError
    # "Overwriting panel" fallback below instead).
    if (
        frontend.DATA_PANELS in hass.data
        and PANEL_URL_PATH in hass.data[frontend.DATA_PANELS]
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
                    "js_url": f"/smart_lock_manager_static/"
                    f"smart-lock-manager-panel.js?v=2026.8.4&t="
                    f"{int(__import__('time').time())}",
                }
            },
            # SECURITY: this panel sets and clears physical door codes, so it
            # must not appear in the sidebar for non-admin users.
            require_admin=True,
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
    """Remove the Smart Lock Manager panel when the LAST lock entry unloads.

    The panel is process-wide (registered once, shared by every lock config
    entry), so it must only be removed when the final entry is unloading — the
    same last-instance guard the access-log listener teardown uses. This runs
    from ``async_unload_entry`` BEFORE the unloading entry is popped from
    ``hass.data[DOMAIN]``, so "this is the last entry" means at most one dict
    entry remains. Previously this was a pure no-op that only logged, leaving
    the panel (and its sidebar item) behind after the integration was removed.

    - Inputs: hass (HomeAssistant).
    - Outputs: None.
    """
    _LOGGER.debug("Unregistering Smart Lock Manager panel")

    remaining = [v for v in hass.data.get(DOMAIN, {}).values() if isinstance(v, dict)]
    if len(remaining) > 1:
        # Other lock entries still use the shared panel; leave it registered.
        return

    if (
        frontend.DATA_PANELS in hass.data
        and PANEL_URL_PATH in hass.data[frontend.DATA_PANELS]
    ):
        frontend.async_remove_panel(hass, PANEL_URL_PATH)
        _LOGGER.info("Smart Lock Manager panel unregistered")
