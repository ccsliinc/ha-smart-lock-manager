"""Service layer for Smart Lock Manager (behavior-preserving, from __init__).

Exposes ``async_register_services`` / ``async_unregister_services`` which
register + tear down every service (core, advanced, the access-log listener,
and the dev-gated services). Schemas live in ``schemas``; the access-log
mapping helpers live in ``access_log`` (re-imported here for re-export).
"""

import logging

import voluptuous as vol
from homeassistant.const import ATTR_ENTITY_ID
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv

from ..const import (
    ATTR_CODE_SLOT,
    DOMAIN,
    SERVICE_ADD_LOCK_TO_ZONE,
    SERVICE_APPLY_ZONE_CODES,
    SERVICE_CLEAR_ALL_SLOTS,
    SERVICE_CLEAR_CODE,
    SERVICE_CLEAR_ZONE_CODES,
    SERVICE_CREATE_ZONE,
    SERVICE_DELETE_ZONE,
    SERVICE_DISABLE_SLOT,
    SERVICE_ENABLE_SLOT,
    SERVICE_GENERATE_PACKAGE,
    SERVICE_GET_USAGE_STATS,
    SERVICE_PAUSE_ALERTS,
    SERVICE_READ_ZWAVE_CODES,
    SERVICE_REFRESH_CODES,
    SERVICE_REMOVE_LOCK_FROM_ZONE,
    SERVICE_RESET_SLOT_USAGE,
    SERVICE_RESET_SYNC,
    SERVICE_RESIZE_SLOTS,
    SERVICE_RESUME_ALERTS,
    SERVICE_SET_CODE,
    SERVICE_SET_CODE_ADVANCED,
    SERVICE_SET_SWEEP_INTERVALS,
    SERVICE_UPDATE_GLOBAL_SETTINGS,
    SERVICE_UPDATE_LOCK_SETTINGS,
    SERVICE_UPDATE_ZONE,
    SERVICE_UPDATE_ZONE_SETTINGS,
)
from ..dev_mock import (
    dev_inject_sync_error,
    fire_mock_notification,
    is_dev_mock,
)

# ``map_access_control_event`` + ``_resolve_lock_for_node`` are re-imported so
# the package root can re-export them from ``services.registration`` (frozen
# public names / test patch target).
from .access_log import (  # noqa: F401
    _build_access_log_handler,
    _resolve_lock_for_node,
    map_access_control_event,
)
from .lock_services import LockServices
from .management_services import ManagementServices
from .schemas import (
    APPLY_ZONE_CODES_SCHEMA,
    CLEAR_ALL_SLOTS_SCHEMA,
    CLEAR_CODE_SCHEMA,
    CLEAR_ZONE_CODES_SCHEMA,
    CREATE_ZONE_SCHEMA,
    DELETE_ZONE_SCHEMA,
    ENABLE_DISABLE_SLOT_SCHEMA,
    GENERATE_PACKAGE_SCHEMA,
    GET_USAGE_STATS_SCHEMA,
    PAUSE_ALERTS_SCHEMA,
    READ_ZWAVE_CODES_SCHEMA,
    REFRESH_CODES_SCHEMA,
    RESET_SLOT_USAGE_SCHEMA,
    RESIZE_SLOTS_SCHEMA,
    RESUME_ALERTS_SCHEMA,
    SET_CODE_ADVANCED_SCHEMA,
    SET_CODE_SCHEMA,
    SET_SWEEP_INTERVALS_SCHEMA,
    UPDATE_GLOBAL_SETTINGS_SCHEMA,
    UPDATE_LOCK_SETTINGS_SCHEMA,
    UPDATE_ZONE_SCHEMA,
    UPDATE_ZONE_SETTINGS_SCHEMA,
    ZONE_MEMBER_SCHEMA,
)
from .slot_services import SlotServices
from .system_services import SystemServices
from .zone_services import ZoneServices
from .zone_settings_service import ZoneSettingsService
from .zwave_services import ZWaveServices

# Module-level logger so log entries appear under
# ``custom_components.smart_lock_manager`` (not ``.services.registration``).
_LOGGER = logging.getLogger("custom_components.smart_lock_manager")


async def async_register_services(hass: HomeAssistant) -> None:
    """Register every Smart Lock Manager service + the access-log listener.

    - Description: registers core services, advanced services, the global
      access-log notification listener, and the dev-gated dev_fire_notification
      / dev_inject_sync_error services — in that relative order, identical to
      the prior inline registration in ``async_setup_entry``.
    - Inputs: hass (HomeAssistant).
    - Outputs: None.
    """

    # --- Core services -----------------------------------------------------
    # Create service wrappers to pass hass parameter to static methods
    async def set_code_wrapper(service_call: ServiceCall) -> None:
        return await LockServices.set_code(hass, service_call)

    async def clear_code_wrapper(service_call: ServiceCall) -> None:
        return await LockServices.clear_code(hass, service_call)

    async def refresh_codes_wrapper(service_call: ServiceCall) -> None:
        return await ZWaveServices.refresh_codes(hass, service_call)

    async def generate_package_wrapper(service_call: ServiceCall) -> None:
        return await SystemServices.generate_package(hass, service_call)

    # Register services using modular classes
    hass.services.async_register(
        DOMAIN, SERVICE_SET_CODE, set_code_wrapper, schema=SET_CODE_SCHEMA
    )
    hass.services.async_register(
        DOMAIN, SERVICE_CLEAR_CODE, clear_code_wrapper, schema=CLEAR_CODE_SCHEMA
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_REFRESH_CODES,
        refresh_codes_wrapper,
        schema=REFRESH_CODES_SCHEMA,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_GENERATE_PACKAGE,
        generate_package_wrapper,
        schema=GENERATE_PACKAGE_SCHEMA,
    )

    # --- Advanced services -------------------------------------------------
    # Create service wrappers to pass hass parameter to static methods
    async def set_code_advanced_wrapper(service_call: ServiceCall) -> None:
        return await LockServices.set_code_advanced(hass, service_call)

    async def enable_slot_wrapper(service_call: ServiceCall) -> None:
        return await SlotServices.enable_slot(hass, service_call)

    async def disable_slot_wrapper(service_call: ServiceCall) -> None:
        return await SlotServices.disable_slot(hass, service_call)

    async def reset_slot_usage_wrapper(service_call: ServiceCall) -> None:
        return await SlotServices.reset_slot_usage(hass, service_call)

    async def reset_sync_wrapper(service_call: ServiceCall) -> None:
        return await SlotServices.reset_sync(hass, service_call)

    async def resize_slots_wrapper(service_call: ServiceCall) -> None:
        return await SlotServices.resize_slots(hass, service_call)

    async def read_zwave_codes_wrapper(service_call: ServiceCall) -> None:
        return await ZWaveServices.read_zwave_codes(hass, service_call)

    async def sync_slot_to_zwave_wrapper(service_call: ServiceCall) -> None:
        return await ZWaveServices.sync_slot_to_zwave(hass, service_call)

    async def get_usage_stats_wrapper(service_call: ServiceCall) -> None:
        return await ManagementServices.get_usage_stats(hass, service_call)

    async def update_lock_settings_wrapper(service_call: ServiceCall) -> None:
        return await ManagementServices.update_lock_settings(hass, service_call)

    async def clear_all_slots_wrapper(service_call: ServiceCall) -> None:
        return await ManagementServices.clear_all_slots(hass, service_call)

    async def create_zone_wrapper(service_call: ServiceCall) -> None:
        return await ZoneServices.create_zone(hass, service_call)

    async def delete_zone_wrapper(service_call: ServiceCall) -> None:
        return await ZoneServices.delete_zone(hass, service_call)

    async def add_lock_to_zone_wrapper(service_call: ServiceCall) -> None:
        return await ZoneServices.add_lock_to_zone(hass, service_call)

    async def remove_lock_from_zone_wrapper(service_call: ServiceCall) -> None:
        return await ZoneServices.remove_lock_from_zone(hass, service_call)

    async def apply_zone_codes_wrapper(service_call: ServiceCall) -> None:
        return await ZoneServices.apply_zone_codes(hass, service_call)

    async def update_zone_wrapper(service_call: ServiceCall) -> None:
        return await ZoneServices.update_zone(hass, service_call)

    async def clear_zone_codes_wrapper(service_call: ServiceCall) -> None:
        return await ZoneServices.clear_zone_codes(hass, service_call)

    async def update_zone_settings_wrapper(service_call: ServiceCall) -> None:
        return await ZoneSettingsService.update_zone_settings(hass, service_call)

    async def update_global_settings_wrapper(service_call: ServiceCall) -> None:
        return await SystemServices.update_global_settings(hass, service_call)

    async def set_sweep_intervals_wrapper(service_call: ServiceCall) -> None:
        return await SystemServices.set_sweep_intervals(hass, service_call)

    async def pause_alerts_wrapper(service_call: ServiceCall) -> None:
        return await SystemServices.pause_alerts(hass, service_call)

    async def resume_alerts_wrapper(service_call: ServiceCall) -> None:
        return await SystemServices.resume_alerts(hass, service_call)

    # Register advanced services using modular classes
    _LOGGER.debug("Registering advanced services...")

    hass.services.async_register(
        DOMAIN,
        SERVICE_SET_CODE_ADVANCED,
        set_code_advanced_wrapper,
        schema=SET_CODE_ADVANCED_SCHEMA,
    )
    _LOGGER.debug("Registered set_code_advanced service")

    hass.services.async_register(
        DOMAIN,
        SERVICE_ENABLE_SLOT,
        enable_slot_wrapper,
        schema=ENABLE_DISABLE_SLOT_SCHEMA,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_DISABLE_SLOT,
        disable_slot_wrapper,
        schema=ENABLE_DISABLE_SLOT_SCHEMA,
    )
    _LOGGER.debug("Registered enable/disable_slot services")

    hass.services.async_register(
        DOMAIN,
        SERVICE_RESET_SLOT_USAGE,
        reset_slot_usage_wrapper,
        schema=RESET_SLOT_USAGE_SCHEMA,
    )
    hass.services.async_register(
        DOMAIN, SERVICE_RESIZE_SLOTS, resize_slots_wrapper, schema=RESIZE_SLOTS_SCHEMA
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_RESET_SYNC,
        reset_sync_wrapper,
        schema=ENABLE_DISABLE_SLOT_SCHEMA,
    )
    _LOGGER.debug("Registered slot management services")

    hass.services.async_register(
        DOMAIN,
        SERVICE_READ_ZWAVE_CODES,
        read_zwave_codes_wrapper,
        schema=READ_ZWAVE_CODES_SCHEMA,
    )
    hass.services.async_register(
        DOMAIN,
        "sync_slot_to_zwave",
        sync_slot_to_zwave_wrapper,
        schema=vol.Schema(
            {
                vol.Required(ATTR_ENTITY_ID): cv.entity_id,
                vol.Required(ATTR_CODE_SLOT): vol.Coerce(int),
                vol.Optional("action", default="auto"): vol.In(
                    ["auto", "enable", "disable"]
                ),
            }
        ),
    )
    _LOGGER.debug("Registered Z-Wave services")

    hass.services.async_register(
        DOMAIN,
        SERVICE_GET_USAGE_STATS,
        get_usage_stats_wrapper,
        schema=GET_USAGE_STATS_SCHEMA,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_UPDATE_LOCK_SETTINGS,
        update_lock_settings_wrapper,
        schema=UPDATE_LOCK_SETTINGS_SCHEMA,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_CLEAR_ALL_SLOTS,
        clear_all_slots_wrapper,
        schema=CLEAR_ALL_SLOTS_SCHEMA,
    )
    _LOGGER.debug("Registered management services")

    # Zone-management services (replace retired parent/child services).
    hass.services.async_register(
        DOMAIN, SERVICE_CREATE_ZONE, create_zone_wrapper, schema=CREATE_ZONE_SCHEMA
    )
    hass.services.async_register(
        DOMAIN, SERVICE_DELETE_ZONE, delete_zone_wrapper, schema=DELETE_ZONE_SCHEMA
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_ADD_LOCK_TO_ZONE,
        add_lock_to_zone_wrapper,
        schema=ZONE_MEMBER_SCHEMA,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_REMOVE_LOCK_FROM_ZONE,
        remove_lock_from_zone_wrapper,
        schema=ZONE_MEMBER_SCHEMA,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_APPLY_ZONE_CODES,
        apply_zone_codes_wrapper,
        schema=APPLY_ZONE_CODES_SCHEMA,
    )
    hass.services.async_register(
        DOMAIN, SERVICE_UPDATE_ZONE, update_zone_wrapper, schema=UPDATE_ZONE_SCHEMA
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_CLEAR_ZONE_CODES,
        clear_zone_codes_wrapper,
        schema=CLEAR_ZONE_CODES_SCHEMA,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_UPDATE_ZONE_SETTINGS,
        update_zone_settings_wrapper,
        schema=UPDATE_ZONE_SETTINGS_SCHEMA,
    )
    _LOGGER.debug("Registered zone-management services")

    hass.services.async_register(
        DOMAIN,
        SERVICE_UPDATE_GLOBAL_SETTINGS,
        update_global_settings_wrapper,
        schema=UPDATE_GLOBAL_SETTINGS_SCHEMA,
    )

    hass.services.async_register(
        DOMAIN,
        SERVICE_SET_SWEEP_INTERVALS,
        set_sweep_intervals_wrapper,
        schema=SET_SWEEP_INTERVALS_SCHEMA,
    )

    hass.services.async_register(
        DOMAIN,
        SERVICE_PAUSE_ALERTS,
        pause_alerts_wrapper,
        schema=PAUSE_ALERTS_SCHEMA,
    )

    hass.services.async_register(
        DOMAIN,
        SERVICE_RESUME_ALERTS,
        resume_alerts_wrapper,
        schema=RESUME_ALERTS_SCHEMA,
    )
    _LOGGER.debug("Registered system services")

    # --- Access-log notification listener ----------------------------------
    # Register a single global Z-Wave notification listener for the access log.
    # One event bus serves all locks; the handler resolves the target lock by
    # node_id, so we only subscribe once and store the unsub callback.
    #
    # IMPORTANT: the unsub callback (a functools.partial) is stored in a
    # SEPARATE ``{DOMAIN}_runtime`` namespace — never inside
    # ``hass.data[DOMAIN]``, which is the per-config-entry registry that many
    # loops iterate expecting only entry dicts. Storing a non-dict there caused
    # an ``AttributeError: 'functools.partial' object has no attribute 'get'``.
    runtime = hass.data.setdefault(f"{DOMAIN}_runtime", {})
    if "access_log_unsub" not in runtime:
        runtime["access_log_unsub"] = hass.bus.async_listen(
            "zwave_js_notification", _build_access_log_handler(hass)
        )
        _LOGGER.info("Access log: registered zwave_js_notification listener")

    # --- Dev-gated services ------------------------------------------------
    # DEV-ONLY: register a service to fire a mock zwave_js_notification so the
    # access-log handler can be driven end-to-end without real Z-Wave hardware.
    # Registered only under SLM_DEV_MOCK and only once per HA process.
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
    # exercise that path without real Z-Wave failures. Registered only under
    # SLM_DEV_MOCK and only once per HA process.
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


async def async_unregister_services(hass: HomeAssistant) -> None:
    """Tear down the access-log listener and remove every service.

    - Description: replicates the prior inline teardown in
      ``async_unload_entry``: when no lock entries remain, pop+unsub the
      access-log listener; when ``hass.data[DOMAIN]`` is empty, remove all
      registered services. Both guards are computed internally so the net
      effect is identical (same keys popped, same services removed).
    - Inputs: hass (HomeAssistant).
    - Outputs: None.
    """
    # If no lock entries remain, tear down the global access-log listener.
    # The unsub lives in the separate ``{DOMAIN}_runtime`` namespace, not in
    # the per-entry registry (see async_register_services for rationale).
    remaining_locks = [v for v in hass.data[DOMAIN].values() if isinstance(v, dict)]
    if not remaining_locks:
        runtime = hass.data.get(f"{DOMAIN}_runtime", {})
        unsub = runtime.pop("access_log_unsub", None)
        if unsub:
            unsub()
            _LOGGER.info("Access log: removed zwave_js_notification listener")

    # Remove services only if this is the last instance
    if not hass.data[DOMAIN]:
        hass.services.async_remove(DOMAIN, SERVICE_SET_CODE)
        hass.services.async_remove(DOMAIN, SERVICE_CLEAR_CODE)
        hass.services.async_remove(DOMAIN, SERVICE_REFRESH_CODES)
        hass.services.async_remove(DOMAIN, SERVICE_GENERATE_PACKAGE)

        # Remove advanced services
        hass.services.async_remove(DOMAIN, SERVICE_SET_CODE_ADVANCED)
        hass.services.async_remove(DOMAIN, SERVICE_ENABLE_SLOT)
        hass.services.async_remove(DOMAIN, SERVICE_DISABLE_SLOT)
        hass.services.async_remove(DOMAIN, SERVICE_RESET_SLOT_USAGE)
        hass.services.async_remove(DOMAIN, SERVICE_RESET_SYNC)
        hass.services.async_remove(DOMAIN, SERVICE_RESIZE_SLOTS)
        hass.services.async_remove(DOMAIN, SERVICE_GET_USAGE_STATS)

        # Zone-management services
        hass.services.async_remove(DOMAIN, SERVICE_CREATE_ZONE)
        hass.services.async_remove(DOMAIN, SERVICE_DELETE_ZONE)
        hass.services.async_remove(DOMAIN, SERVICE_ADD_LOCK_TO_ZONE)
        hass.services.async_remove(DOMAIN, SERVICE_REMOVE_LOCK_FROM_ZONE)
        hass.services.async_remove(DOMAIN, SERVICE_APPLY_ZONE_CODES)
        hass.services.async_remove(DOMAIN, SERVICE_UPDATE_ZONE)
        hass.services.async_remove(DOMAIN, SERVICE_CLEAR_ZONE_CODES)
        hass.services.async_remove(DOMAIN, SERVICE_UPDATE_ZONE_SETTINGS)
        hass.services.async_remove(DOMAIN, SERVICE_SET_SWEEP_INTERVALS)
        hass.services.async_remove(DOMAIN, SERVICE_PAUSE_ALERTS)
        hass.services.async_remove(DOMAIN, SERVICE_RESUME_ALERTS)
