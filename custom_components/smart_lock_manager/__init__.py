"""Smart Lock Manager Integration."""

from datetime import datetime, timedelta
from typing import Any, Dict

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_ENTITY_ID
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api.http import async_register_http_views, async_unregister_http_views

# Z-Wave JS imports for reading actual lock codes
try:
    ZWAVE_JS_AVAILABLE = True
except (ModuleNotFoundError, ImportError):
    ZWAVE_JS_AVAILABLE = False

from .const import (
    _LOGGER,
    ATTR_ALLOWED_DAYS,
    ATTR_ALLOWED_HOURS,
    ATTR_AUTO_DISABLE_EXPIRED,
    ATTR_CODE_SLOT,
    ATTR_CODE_SLOT_NAME,
    ATTR_COORDINATOR_INTERVAL,
    ATTR_DEBUG_LOGGING,
    ATTR_END_DATE,
    ATTR_MAX_USES,
    ATTR_NODE_ID,
    ATTR_NOTIFY_ON_USE,
    ATTR_SLOT_COUNT,
    ATTR_START_DATE,
    ATTR_SYNC_ON_LOCK_EVENTS,
    ATTR_USER_CODE,
    DOMAIN,
    ISSUE_URL,
    PLATFORMS,
    PRIMARY_LOCK,
    SERVICE_CLEAR_CODE,
    SERVICE_DISABLE_SLOT,
    SERVICE_ENABLE_SLOT,
    SERVICE_GENERATE_PACKAGE,
    SERVICE_GET_USAGE_STATS,
    SERVICE_READ_ZWAVE_CODES,
    SERVICE_REFRESH_CODES,
    SERVICE_RESET_SLOT_USAGE,
    SERVICE_RESIZE_SLOTS,
    SERVICE_SET_CODE,
    SERVICE_SET_CODE_ADVANCED,
    SERVICE_SYNC_CHILD_LOCKS,
    SERVICE_UPDATE_GLOBAL_SETTINGS,
    SERVICE_UPDATE_LOCK_SETTINGS,
    VERSION,
)
from .frontend.panel import async_register_panel, async_unregister_panel
from .models.lock import SmartLockManagerLock
from .services.lock_services import LockServices
from .services.management_services import ManagementServices
from .services.slot_services import SlotServices
from .services.system_services import SystemServices
from .services.zwave_services import ZWaveServices


async def _save_lock_data(
    hass: HomeAssistant, lock: SmartLockManagerLock, entry_id: str
) -> None:
    """Save lock slot data to persistent storage."""
    try:
        store = hass.data[DOMAIN][entry_id]["store"]

        # Convert slot data to serializable format
        slot_data = {}
        for slot_num, slot in lock.code_slots.items():
            slot_data[str(slot_num)] = {
                "slot_number": slot.slot_number,
                "pin_code": slot.pin_code,
                "user_name": slot.user_name,
                "is_active": slot.is_active,
                "start_date": slot.start_date.isoformat() if slot.start_date else None,
                "end_date": slot.end_date.isoformat() if slot.end_date else None,
                "allowed_hours": slot.allowed_hours,
                "allowed_days": slot.allowed_days,
                "max_uses": slot.max_uses,
                "use_count": slot.use_count,
                "notify_on_use": slot.notify_on_use,
            }

        data_to_save = {
            "code_slots": slot_data,
            "lock_name": lock.lock_name,
            "lock_entity_id": lock.lock_entity_id,
        }

        await store.async_save(data_to_save)
        _LOGGER.debug("Saved slot data for %s", lock.lock_name)

    except Exception as e:
        _LOGGER.error("Failed to save slot data for %s: %s", lock.lock_name, e)


# Service schemas
CLEAR_CODE_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_ENTITY_ID): cv.entity_id,
        vol.Required(ATTR_CODE_SLOT): vol.Coerce(int),
    }
)

SET_CODE_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_ENTITY_ID): cv.entity_id,
        vol.Required(ATTR_CODE_SLOT): vol.Coerce(int),
        vol.Required(ATTR_USER_CODE): cv.string,
        vol.Optional(ATTR_CODE_SLOT_NAME): cv.string,
    }
)

REFRESH_CODES_SCHEMA = vol.Schema({vol.Required(ATTR_ENTITY_ID): cv.entity_id})

GENERATE_PACKAGE_SCHEMA = vol.Schema({vol.Required(ATTR_NODE_ID): cv.string})

# Advanced service schemas
SET_CODE_ADVANCED_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_ENTITY_ID): cv.entity_id,
        vol.Required(ATTR_CODE_SLOT): vol.Coerce(int),
        vol.Required(ATTR_USER_CODE): cv.string,
        vol.Optional(ATTR_CODE_SLOT_NAME): cv.string,
        vol.Optional(ATTR_START_DATE): cv.datetime,
        vol.Optional(ATTR_END_DATE): cv.datetime,
        vol.Optional(ATTR_ALLOWED_HOURS): [int],
        vol.Optional(ATTR_ALLOWED_DAYS): [int],
        vol.Optional(ATTR_MAX_USES, default=-1): int,
        vol.Optional(ATTR_NOTIFY_ON_USE, default=False): cv.boolean,
    }
)

ENABLE_DISABLE_SLOT_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_ENTITY_ID): cv.entity_id,
        vol.Required(ATTR_CODE_SLOT): vol.Coerce(int),
    }
)

RESET_SLOT_USAGE_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_ENTITY_ID): cv.entity_id,
        vol.Required(ATTR_CODE_SLOT): vol.Coerce(int),
    }
)

RESIZE_SLOTS_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_ENTITY_ID): cv.entity_id,
        vol.Required(ATTR_SLOT_COUNT): vol.Coerce(int),
    }
)

SYNC_CHILD_LOCKS_SCHEMA = vol.Schema({vol.Required(ATTR_ENTITY_ID): cv.entity_id})

GET_USAGE_STATS_SCHEMA = vol.Schema({vol.Required(ATTR_ENTITY_ID): cv.entity_id})

UPDATE_LOCK_SETTINGS_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_ENTITY_ID): cv.entity_id,
        vol.Optional("friendly_name"): str,
        vol.Optional("slot_count"): vol.All(int, vol.Range(min=1, max=50)),
    }
)

READ_ZWAVE_CODES_SCHEMA = vol.Schema({vol.Required(ATTR_ENTITY_ID): cv.entity_id})

UPDATE_GLOBAL_SETTINGS_SCHEMA = vol.Schema(
    {
        vol.Optional(ATTR_COORDINATOR_INTERVAL): vol.All(
            int, vol.In([30, 60, 120, 300])
        ),
        vol.Optional(ATTR_AUTO_DISABLE_EXPIRED): bool,
        vol.Optional(ATTR_SYNC_ON_LOCK_EVENTS): bool,
        vol.Optional(ATTR_DEBUG_LOGGING): bool,
    }
)


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

    # Get the actual lock entity's friendly name
    lock_entity_id = entry.data.get("lock_entity_id", "")
    lock_name = entry.data.get("lock_name", "Smart Lock")

    # If we have a lock entity ID, try to get its friendly name
    if lock_entity_id and lock_entity_id in hass.states.async_entity_ids():
        state = hass.states.get(lock_entity_id)
        if state and state.attributes.get("friendly_name"):
            lock_name = state.attributes["friendly_name"]
        elif state:
            # Fall back to entity name without domain
            lock_name = (
                state.name or lock_entity_id.split(".")[-1].replace("_", " ").title()
            )

    # Create storage for persistent slot data
    storage_key = f"smart_lock_manager_{entry.entry_id}"
    store = Store(hass, 1, storage_key)

    # Load existing slot data if available
    stored_data = await store.async_load() or {}

    # Create the Smart Lock Manager lock object to store all data
    lock = SmartLockManagerLock(
        lock_name=lock_name,
        lock_entity_id=lock_entity_id,
        slots=entry.data.get("slots", 10),
        start_from=entry.data.get("start_from", 1),
    )

    # Restore slot data from storage
    if stored_data.get("code_slots"):
        _LOGGER.info(
            "Restoring %s saved slots for %s", len(stored_data["code_slots"]), lock_name
        )
        lock.code_slots = {}
        for slot_num_str, slot_data in stored_data["code_slots"].items():
            slot_num = int(slot_num_str)
            # Recreate CodeSlot objects from stored data
            from .models.lock import CodeSlot

            # Debug what we're restoring
            is_active = slot_data.get("is_active", False)
            pin_code = slot_data.get("pin_code")
            user_name = slot_data.get("user_name")
            _LOGGER.info(
                "Restoring slot %s: user=%s, pin=%s, active=%s",
                slot_num,
                user_name,
                pin_code[:4] + "***" if pin_code else None,
                is_active,
            )

            slot = CodeSlot(
                slot_number=slot_data.get("slot_number", slot_num),
                pin_code=pin_code,
                user_name=user_name,
                is_active=is_active,
                start_date=(
                    datetime.fromisoformat(slot_data["start_date"])
                    if slot_data.get("start_date")
                    else None
                ),
                end_date=(
                    datetime.fromisoformat(slot_data["end_date"])
                    if slot_data.get("end_date")
                    else None
                ),
                allowed_hours=slot_data.get("allowed_hours"),
                allowed_days=slot_data.get("allowed_days"),
                max_uses=slot_data.get("max_uses", -1),
                use_count=slot_data.get("use_count", 0),
                notify_on_use=slot_data.get("notify_on_use", False),
            )
            lock.code_slots[slot_num] = slot

    # Create coordinator for data updates
    coordinator = SmartLockManagerDataUpdateCoordinator(hass, entry)
    _LOGGER.debug("Created coordinator for %s with 30s update interval", lock.lock_name)

    # Store lock object, coordinator, storage and entry data
    hass.data[DOMAIN][entry.entry_id] = {
        PRIMARY_LOCK: lock,
        "coordinator": coordinator,
        "store": store,
        "entry": entry,
    }

    # Register services BEFORE initial sync
    await _register_services(hass)
    await _register_advanced_services(hass)

    # Fetch initial data and force full sync on startup
    await coordinator.async_config_entry_first_refresh()
    _LOGGER.info("Completed first refresh for %s coordinator", lock.lock_name)

    # Force a full synchronization on startup to ensure consistency
    _LOGGER.info("Starting initial synchronization for %s", lock.lock_name)
    try:
        # Force sync all active slots to ensure physical lock matches Smart Lock Manager
        await hass.services.async_call(
            DOMAIN, "read_zwave_codes", {ATTR_ENTITY_ID: lock.lock_entity_id}
        )
        _LOGGER.info("Initial Z-Wave code reading triggered for %s", lock.lock_name)
    except Exception as e:
        _LOGGER.warning("Failed to trigger initial sync for %s: %s", lock.lock_name, e)

    # Set up platforms (now empty - no sensors!)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Register HTTP views for frontend
    await async_register_http_views(hass)

    # Register custom panel
    await async_register_panel(hass)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""

    # Unregister HTTP views
    await async_unregister_http_views(hass)

    # Unregister custom panel
    await async_unregister_panel(hass)

    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)

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
            hass.services.async_remove(DOMAIN, SERVICE_RESIZE_SLOTS)
            hass.services.async_remove(DOMAIN, SERVICE_SYNC_CHILD_LOCKS)
            hass.services.async_remove(DOMAIN, SERVICE_GET_USAGE_STATS)

    return unload_ok


async def _register_services(hass: HomeAssistant) -> None:
    """Register Smart Lock Manager services using modular service classes."""

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


async def _register_advanced_services(hass: HomeAssistant) -> None:
    """Register advanced Smart Lock Manager services using modular service classes."""

    # Create service wrappers to pass hass parameter to static methods
    async def set_code_advanced_wrapper(service_call: ServiceCall) -> None:
        return await LockServices.set_code_advanced(hass, service_call)

    async def enable_slot_wrapper(service_call: ServiceCall) -> None:
        return await SlotServices.enable_slot(hass, service_call)

    async def disable_slot_wrapper(service_call: ServiceCall) -> None:
        return await SlotServices.disable_slot(hass, service_call)

    async def reset_slot_usage_wrapper(service_call: ServiceCall) -> None:
        return await SlotServices.reset_slot_usage(hass, service_call)

    async def resize_slots_wrapper(service_call: ServiceCall) -> None:
        return await SlotServices.resize_slots(hass, service_call)

    async def read_zwave_codes_wrapper(service_call: ServiceCall) -> None:
        return await ZWaveServices.read_zwave_codes(hass, service_call)

    async def sync_slot_to_zwave_wrapper(service_call: ServiceCall) -> None:
        return await ZWaveServices.sync_slot_to_zwave(hass, service_call)

    async def sync_child_locks_wrapper(service_call: ServiceCall) -> None:
        return await ManagementServices.sync_child_locks(hass, service_call)

    async def get_usage_stats_wrapper(service_call: ServiceCall) -> None:
        return await ManagementServices.get_usage_stats(hass, service_call)

    async def update_lock_settings_wrapper(service_call: ServiceCall) -> None:
        return await ManagementServices.update_lock_settings(hass, service_call)

    async def update_global_settings_wrapper(service_call: ServiceCall) -> None:
        return await SystemServices.update_global_settings(hass, service_call)

    # Register advanced services using modular classes
    _LOGGER.info("Registering advanced services...")

    hass.services.async_register(
        DOMAIN,
        SERVICE_SET_CODE_ADVANCED,
        set_code_advanced_wrapper,
        schema=SET_CODE_ADVANCED_SCHEMA,
    )
    _LOGGER.info("Registered set_code_advanced service")

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
    _LOGGER.info("Registered enable/disable_slot services")

    hass.services.async_register(
        DOMAIN,
        SERVICE_RESET_SLOT_USAGE,
        reset_slot_usage_wrapper,
        schema=RESET_SLOT_USAGE_SCHEMA,
    )
    hass.services.async_register(
        DOMAIN, SERVICE_RESIZE_SLOTS, resize_slots_wrapper, schema=RESIZE_SLOTS_SCHEMA
    )
    _LOGGER.info("Registered slot management services")

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
    _LOGGER.info("Registered Z-Wave services")

    hass.services.async_register(
        DOMAIN,
        SERVICE_SYNC_CHILD_LOCKS,
        sync_child_locks_wrapper,
        schema=SYNC_CHILD_LOCKS_SCHEMA,
    )
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
    _LOGGER.info("Registered management services")

    hass.services.async_register(
        DOMAIN,
        SERVICE_UPDATE_GLOBAL_SETTINGS,
        update_global_settings_wrapper,
        schema=UPDATE_GLOBAL_SETTINGS_SCHEMA,
    )
    _LOGGER.info("Registered system services")


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
        """Update data via library with comprehensive Z-Wave sync."""
        try:
            _LOGGER.debug("Updating Smart Lock Manager data for %s", self.lock_name)

            # Get the lock object from hass data
            lock = None
            for entry_id, entry_data in self.hass.data[DOMAIN].items():
                if (
                    entry_data.get(PRIMARY_LOCK)
                    and entry_data[PRIMARY_LOCK].lock_name == self.lock_name
                ):
                    lock = entry_data[PRIMARY_LOCK]
                    break

            if lock:
                # Step 1: Check for slot validity changes and auto-disable expired slots
                lock.check_and_update_slot_validity()

                # Step 2: Read current Z-Wave codes every 30 seconds
                zwave_codes = {}
                try:
                    # Read current codes from the physical lock
                    from homeassistant.components.zwave_js.helpers import (
                        async_get_node_from_entity_id,
                    )
                    from homeassistant.helpers.entity_registry import (
                        async_get as async_get_entity_registry,
                    )
                    from zwave_js_server.util.lock import get_usercode_from_node

                    ent_reg = async_get_entity_registry(self.hass)
                    entity_entry = ent_reg.async_get(lock.lock_entity_id)

                    if entity_entry and entity_entry.platform == "zwave_js":
                        node = async_get_node_from_entity_id(
                            self.hass, lock.lock_entity_id, ent_reg=ent_reg
                        )
                        if node:
                            # Quick scan of first 10 slots only (performance optimization)
                            for slot in range(1, 11):
                                try:
                                    code_data = await get_usercode_from_node(node, slot)
                                    if (
                                        code_data
                                        and code_data.get("usercode")
                                        and code_data.get("in_use")
                                    ):
                                        zwave_codes[slot] = {
                                            "code": code_data.get("usercode"),
                                            "in_use": code_data.get("in_use"),
                                            "status": code_data.get(
                                                "userIdStatus", "unknown"
                                            ),
                                        }
                                except Exception:
                                    pass  # Slot empty or error
                except Exception as e:
                    _LOGGER.debug("Z-Wave code reading failed: %s", e)

                # Step 3: Update sync status and determine needed actions
                _LOGGER.debug(
                    "Updating sync status, found %s Z-Wave codes", len(zwave_codes)
                )
                lock.update_sync_status(zwave_codes)
                sync_actions = lock.get_slots_needing_sync(zwave_codes)
                if (
                    sync_actions.get("add")
                    or sync_actions.get("remove")
                    or sync_actions.get("retry")
                ):
                    _LOGGER.info(
                        "Sync actions needed: add=%s, remove=%s, retry=%s",
                        sync_actions.get("add", []),
                        sync_actions.get("remove", []),
                        sync_actions.get("retry", []),
                    )

                # Step 4: Perform sync actions with retry logic
                for slot_number in sync_actions.get("add", []):
                    slot = lock.code_slots.get(slot_number)
                    if slot:
                        slot.sync_attempts += 1
                        slot.last_sync_attempt = datetime.now()

                        try:
                            await self.hass.services.async_call(
                                DOMAIN,
                                "sync_slot_to_zwave",
                                {
                                    ATTR_ENTITY_ID: lock.lock_entity_id,
                                    ATTR_CODE_SLOT: slot_number,
                                    "action": "enable",
                                },
                            )
                            _LOGGER.info(
                                "Auto-syncing code to lock %s slot %s (attempt %s)",
                                self.lock_name,
                                slot_number,
                                slot.sync_attempts,
                            )
                        except Exception as e:
                            _LOGGER.error(
                                "Failed to sync slot %s for %s (attempt %s): %s",
                                slot_number,
                                self.lock_name,
                                slot.sync_attempts,
                                e,
                            )

                for slot_number in sync_actions.get("remove", []):
                    slot = lock.code_slots.get(slot_number)
                    try:
                        # If this slot doesn't exist in Smart Lock Manager at all, it's a rogue code
                        if not slot:
                            await self.hass.services.async_call(
                                DOMAIN,
                                SERVICE_CLEAR_CODE,
                                {
                                    ATTR_ENTITY_ID: lock.lock_entity_id,
                                    ATTR_CODE_SLOT: slot_number,
                                },
                            )
                            _LOGGER.info(
                                "Auto-clearing rogue code from lock %s slot %s (no Smart Lock Manager entry)",
                                self.lock_name,
                                slot_number,
                            )
                        elif not slot.is_active:
                            # Slot exists but is disabled - remove from Z-Wave only, keep Smart Lock Manager data
                            _LOGGER.info(
                                "🔄 COORDINATOR DEBUG - Found disabled slot %s with code in Z-Wave, removing from lock only",
                                slot_number,
                            )
                            await self.hass.services.async_call(
                                DOMAIN,
                                "sync_slot_to_zwave",
                                {
                                    ATTR_ENTITY_ID: lock.lock_entity_id,
                                    ATTR_CODE_SLOT: slot_number,
                                    "action": "disable",
                                },
                            )
                            _LOGGER.info(
                                "🔄 COORDINATOR DEBUG - Auto-removing disabled slot %s from lock %s (keeping Smart Lock Manager data)",
                                slot_number,
                                self.lock_name,
                            )
                        else:
                            # Slot is active but needs to be removed for some other reason
                            await self.hass.services.async_call(
                                DOMAIN,
                                "sync_slot_to_zwave",
                                {
                                    ATTR_ENTITY_ID: lock.lock_entity_id,
                                    ATTR_CODE_SLOT: slot_number,
                                    "action": "disable",
                                },
                            )
                            _LOGGER.info(
                                "Auto-removing code from lock %s slot %s (sync issue)",
                                self.lock_name,
                                slot_number,
                            )
                    except Exception as e:
                        _LOGGER.error(
                            "Failed to remove slot %s for %s: %s",
                            slot_number,
                            self.lock_name,
                            e,
                        )

                # Step 5: Log sync errors that need attention
                for slot_number in sync_actions.get("retry", []):
                    slot = lock.code_slots.get(slot_number)
                    if slot and slot.sync_error:
                        _LOGGER.error(
                            "Slot %s sync failed permanently: %s",
                            slot_number,
                            slot.sync_error,
                        )

                        # Fire event for automation/notification
                        self.hass.bus.async_fire(
                            "smart_lock_manager_sync_error",
                            {
                                "entity_id": lock.lock_entity_id,
                                "slot_number": slot_number,
                                "error": slot.sync_error,
                                "attempts": slot.sync_attempts,
                            },
                        )

            return {
                "user_codes": {},
                "lock_state": "unknown",
                "connection_status": True,
            }

        except Exception as exception:
            raise UpdateFailed(
                f"Error communicating with lock: {exception}"
            ) from exception
