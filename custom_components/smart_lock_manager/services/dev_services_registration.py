"""Dev-gated service registration for Smart Lock Manager.

Split out of :mod:`.registration` purely to keep that file under the 500-line
standard. Registers the two SLM_DEV_MOCK-only services that let the access-log
and zone sync-status paths be exercised without real Z-Wave hardware:

  - ``dev_fire_notification`` — fire a mock ``zwave_js_notification`` event.
  - ``dev_inject_sync_error`` — force a member slot into a hard sync-error state.

Both are registered ONLY when :func:`..dev_mock.is_dev_mock` is true and only
once per HA process. Behaviour is byte-identical to the prior inline block.
"""

import voluptuous as vol
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv

from ..const import DOMAIN
from ..dev_mock import dev_inject_sync_error, fire_mock_notification, is_dev_mock
from .registration import _LOGGER


def register_dev_services(hass: HomeAssistant) -> None:
    """Register the SLM_DEV_MOCK-only dev services (idempotent, gated).

    - Description: registers ``dev_fire_notification`` + ``dev_inject_sync_error``
      only under SLM_DEV_MOCK and only once per HA process — identical to the
      prior inline block in ``async_register_services``.
    - Inputs: hass (HomeAssistant).
    - Outputs: None.
    """
    # DEV-ONLY: register a service to fire a mock zwave_js_notification so the
    # access-log handler can be driven end-to-end without real Z-Wave hardware.
    if is_dev_mock() and not hass.services.has_service(DOMAIN, "dev_fire_notification"):

        async def dev_fire_notification_wrapper(service_call: ServiceCall) -> None:
            fire_mock_notification(
                hass,
                node_id=service_call.data["node_id"],
                event_code=service_call.data["event"],
                user_id=service_call.data.get("user_id"),
            )

        hass.services.async_register(
            DOMAIN,
            "dev_fire_notification",
            dev_fire_notification_wrapper,
            schema=vol.Schema(
                {
                    vol.Required("node_id"): vol.Coerce(int),
                    vol.Required("event"): vol.Coerce(int),
                    vol.Optional("user_id"): vol.Coerce(int),
                }
            ),
        )
        _LOGGER.info("DEV: registered smart_lock_manager.dev_fire_notification service")

    # DEV-ONLY: force a member lock slot into a hard sync-error state so the
    # LIVE zone sync-status roll-up (api/zones._derive_slot_sync) reports the
    # slot as "error" and the panel raises its warning banner — the only way to
    # exercise that path without real Z-Wave failures.
    if is_dev_mock() and not hass.services.has_service(DOMAIN, "dev_inject_sync_error"):

        async def dev_inject_sync_error_wrapper(service_call: ServiceCall) -> None:
            found = dev_inject_sync_error(
                hass,
                entity_id=service_call.data["entity_id"],
                code_slot=service_call.data["code_slot"],
                message=service_call.data.get("message"),
            )
            if not found:
                raise HomeAssistantError(
                    f"No member lock {service_call.data['entity_id']} with slot "
                    f"{service_call.data['code_slot']} to fault"
                )

        hass.services.async_register(
            DOMAIN,
            "dev_inject_sync_error",
            dev_inject_sync_error_wrapper,
            schema=vol.Schema(
                {
                    vol.Required("entity_id"): cv.entity_id,
                    vol.Required("code_slot"): vol.Coerce(int),
                    vol.Optional("message"): cv.string,
                }
            ),
        )
        _LOGGER.info("DEV: registered smart_lock_manager.dev_inject_sync_error service")
