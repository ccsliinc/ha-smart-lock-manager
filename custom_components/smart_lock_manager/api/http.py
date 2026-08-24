"""HTTP views for Smart Lock Manager frontend."""

import logging
from pathlib import Path

from homeassistant.components.http import StaticPathConfig
from homeassistant.core import HomeAssistant

from ..const import DOMAIN
from .zones import SmartLockManagerZonesView

_LOGGER = logging.getLogger(__name__)

# Public URL the panel bundle is served from. Home Assistant serves registered
# static paths with proper authentication and built-in path-traversal
# protection, so we no longer hand-roll a file-serving view.
FRONTEND_STATIC_URL = "/smart_lock_manager_static"

# On-disk directory that backs FRONTEND_STATIC_URL (the built panel bundle).
FRONTEND_DIST_PATH = Path(__file__).parent.parent / "frontend" / "dist"


async def async_register_http_views(hass: HomeAssistant) -> None:
    """Register HTTP views + the static frontend path for Smart Lock Manager.

    - Description: registers the authenticated zone DATA API and, exactly once
      per HA run, the static path that serves the panel bundle. The bundle is
      served via ``hass.http.async_register_static_paths`` instead of a custom
      unauthenticated view so HA enforces auth and containment (no path
      traversal, no event-loop-blocking ``stat``/read in our code).
    - Inputs: hass (HomeAssistant).
    - Outputs: None.
    """

    _LOGGER.debug("Registering Smart Lock Manager HTTP views")

    # Register the static frontend path ONCE. async_register_http_views runs on
    # every config-entry setup; re-registering the same URL raises, so guard on
    # a runtime flag stored outside the per-entry registry.
    runtime = hass.data.setdefault(f"{DOMAIN}_runtime", {})
    if not runtime.get("static_path_registered"):
        await hass.http.async_register_static_paths(
            [
                StaticPathConfig(
                    FRONTEND_STATIC_URL,
                    str(FRONTEND_DIST_PATH),
                    # cache_headers=False: the panel URL is cache-busted with a
                    # version + timestamp query string, so avoid long-lived
                    # caching of a stale bundle.
                    False,
                )
            ]
        )
        runtime["static_path_registered"] = True
        _LOGGER.info(
            "Registered Smart Lock Manager static path %s -> %s",
            FRONTEND_STATIC_URL,
            FRONTEND_DIST_PATH,
        )

    # Register the authenticated zone DATA API the redesigned panel consumes.
    # Like the static path above, this runs on EVERY config-entry setup, and HA
    # logs "View ... is already registered" when the same view is registered
    # twice. Guard on the same runtime namespace so the view registers exactly
    # once per HA run.
    if not runtime.get("zones_view_registered"):
        hass.http.register_view(SmartLockManagerZonesView(hass))
        runtime["zones_view_registered"] = True
        _LOGGER.info("Smart Lock Manager HTTP views registered")


async def async_unregister_http_views(hass: HomeAssistant) -> None:
    """Unregister HTTP views for Smart Lock Manager."""

    _LOGGER.debug("Unregistering Smart Lock Manager HTTP views")

    # Home Assistant will automatically clean up views when the integration unloads
    # But we can add specific cleanup here if needed

    _LOGGER.info("Smart Lock Manager HTTP views unregistered")
