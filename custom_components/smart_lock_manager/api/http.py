"""HTTP views for Smart Lock Manager frontend."""

import logging
import os
from pathlib import Path

from aiohttp import web
from homeassistant.components.http import HomeAssistantView
from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)


class SmartLockManagerFrontendView(HomeAssistantView):
    """View to serve Smart Lock Manager frontend files."""

    requires_auth = False
    url = "/api/smart_lock_manager/frontend/{filename}"
    name = "api:smart_lock_manager:frontend"

    def __init__(self, hass: HomeAssistant):
        """Initialize the view."""
        self.hass = hass
        # Get the path to our frontend files
        self.frontend_path = Path(__file__).parent.parent / "frontend" / "dist"

    async def get(self, request, filename):
        """Serve frontend files."""

        try:
            file_path = self.frontend_path / filename

            if not file_path.exists():
                _LOGGER.error("Frontend file not found: %s", file_path)
                return web.Response(text=f"File not found: {filename}", status=404)

            # Read the file (async to avoid blocking)
            content = await self.hass.async_add_executor_job(
                lambda: file_path.read_text(encoding="utf-8")
            )

            # Determine content type
            content_type = "application/javascript"
            if filename.endswith(".css"):
                content_type = "text/css"
            elif filename.endswith(".html"):
                content_type = "text/html"
            elif filename.endswith(".map"):
                content_type = "application/json"

            _LOGGER.debug("Serving frontend file: %s", filename)

            return web.Response(
                text=content,
                content_type=content_type,
                headers={
                    "Cache-Control": "no-cache, no-store, must-revalidate",
                    "Pragma": "no-cache",
                    "Expires": "0",
                },
            )

        except Exception as e:
            _LOGGER.error("Error serving frontend file %s: %s", filename, e)
            return web.Response(text=f"Error serving file: {e}", status=500)


async def async_register_http_views(hass: HomeAssistant) -> None:
    """Register HTTP views for Smart Lock Manager."""

    _LOGGER.debug("Registering Smart Lock Manager HTTP views")

    # Register the frontend view
    hass.http.register_view(SmartLockManagerFrontendView(hass))

    _LOGGER.info("Smart Lock Manager HTTP views registered")


async def async_unregister_http_views(hass: HomeAssistant) -> None:
    """Unregister HTTP views for Smart Lock Manager."""

    _LOGGER.debug("Unregistering Smart Lock Manager HTTP views")

    # Home Assistant will automatically clean up views when the integration unloads
    # But we can add specific cleanup here if needed

    _LOGGER.info("Smart Lock Manager HTTP views unregistered")
