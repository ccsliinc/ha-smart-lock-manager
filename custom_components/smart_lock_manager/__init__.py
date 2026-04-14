"""Smart Lock Manager Integration."""

import logging
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

# Module-level logger so log entries appear under
# ``custom_components.smart_lock_manager`` (not ``.const``).
_LOGGER = logging.getLogger(__name__)

from .const import (
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
    SERVICE_CLEAR_ALL_SLOTS,
    SERVICE_CLEAR_CODE,
    SERVICE_DISABLE_SLOT,
    SERVICE_ENABLE_SLOT,
    SERVICE_GENERATE_PACKAGE,
    SERVICE_GET_USAGE_STATS,
    SERVICE_READ_ZWAVE_CODES,
    SERVICE_REFRESH_CODES,
    SERVICE_REMOVE_CHILD_LOCK,
    SERVICE_RESET_SLOT_USAGE,
    SERVICE_RESET_SYNC,
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
    """Save complete lock data to persistent storage using lock's to_dict method."""
    try:
        store = hass.data[DOMAIN][entry_id]["store"]

        # Use the lock's to_dict method to ensure all data including settings is saved
        data_to_save = lock.to_dict()

        await store.async_save(data_to_save)
        _LOGGER.debug(
            "Saved complete lock data for %s (including settings)", lock.lock_name
        )

    except Exception as e:
        _LOGGER.error("Failed to save lock data for %s: %s", lock.lock_name, e)


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

REMOVE_CHILD_LOCK_SCHEMA = vol.Schema({vol.Required(ATTR_ENTITY_ID): cv.entity_id})

CLEAR_ALL_SLOTS_SCHEMA = vol.Schema({vol.Required(ATTR_ENTITY_ID): cv.entity_id})

UPDATE_LOCK_SETTINGS_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_ENTITY_ID): cv.entity_id,
        vol.Optional("friendly_name"): str,
        vol.Optional("slot_count"): vol.All(int, vol.Range(min=1, max=50)),
        vol.Optional("is_main_lock"): bool,
        vol.Optional("parent_lock_id"): vol.Any(cv.entity_id, None, ""),
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


async def async_setup(hass: HomeAssistant, config: dict) -> bool:  # noqa
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
                is_synced=slot_data.get("is_synced", False),
                sync_attempts=slot_data.get("sync_attempts", 0),
                sync_error=slot_data.get("sync_error"),
                last_sync_attempt=(
                    datetime.fromisoformat(slot_data["last_sync_attempt"])
                    if slot_data.get("last_sync_attempt")
                    else None
                ),
                user_id_status=slot_data.get("user_id_status"),
            )
            lock.code_slots[slot_num] = slot

    # Restore lock settings from storage
    if stored_data.get("settings"):
        settings_data = stored_data["settings"]
        if settings_data.get("friendly_name"):
            lock.settings.friendly_name = settings_data["friendly_name"]
            _LOGGER.info(
                "Restored friendly name for %s: %s",
                lock_name,
                settings_data["friendly_name"],
            )
        if settings_data.get("timezone"):
            lock.settings.timezone = settings_data["timezone"]
        if settings_data.get("auto_lock_time"):
            from datetime import time

            lock.settings.auto_lock_time = time.fromisoformat(
                settings_data["auto_lock_time"]
            )
        if settings_data.get("auto_unlock_time"):
            from datetime import time

            lock.settings.auto_unlock_time = time.fromisoformat(
                settings_data["auto_unlock_time"]
            )
    else:
        # Initialize with default friendly name from lock name if no settings exist
        lock.settings.friendly_name = lock_name
        _LOGGER.info(
            "Initialized default friendly name for %s: %s", lock_name, lock_name
        )

    # Restore parent/child lock relationships from storage
    if stored_data.get("is_main_lock") is not None:
        lock.is_main_lock = stored_data["is_main_lock"]
    if stored_data.get("parent_lock_id"):
        lock.parent_lock_id = stored_data["parent_lock_id"]
    if stored_data.get("child_lock_ids"):
        lock.child_lock_ids = stored_data["child_lock_ids"]

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

    # Auto-repair parent-child link inconsistencies after all locks loaded
    _repair_parent_child_links(hass)

    # NOTE: Initial Z-Wave code reading removed — it was blocking HA startup.
    # hass.services.async_call creates service-call tasks that HA's bootstrap
    # waits on, even when wrapped in async_create_background_task. With 7 locks
    # each sleeping 60s before calling the service, bootstrap would time out.
    # The coordinator's 30-second update cycle reads codes automatically.

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
            hass.services.async_remove(DOMAIN, SERVICE_RESET_SYNC)
            hass.services.async_remove(DOMAIN, SERVICE_RESIZE_SLOTS)
            hass.services.async_remove(DOMAIN, SERVICE_SYNC_CHILD_LOCKS)
            hass.services.async_remove(DOMAIN, SERVICE_GET_USAGE_STATS)

    return bool(unload_ok)


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

    async def reset_sync_wrapper(service_call: ServiceCall) -> None:
        return await SlotServices.reset_sync(hass, service_call)

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

    async def remove_child_lock_wrapper(service_call: ServiceCall) -> None:
        return await ManagementServices.remove_child_lock(hass, service_call)

    async def clear_all_slots_wrapper(service_call: ServiceCall) -> None:
        return await ManagementServices.clear_all_slots(hass, service_call)

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
    hass.services.async_register(
        DOMAIN,
        SERVICE_RESET_SYNC,
        reset_sync_wrapper,
        schema=ENABLE_DISABLE_SLOT_SCHEMA,
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
    hass.services.async_register(
        DOMAIN,
        SERVICE_REMOVE_CHILD_LOCK,
        remove_child_lock_wrapper,
        schema=REMOVE_CHILD_LOCK_SCHEMA,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_CLEAR_ALL_SLOTS,
        clear_all_slots_wrapper,
        schema=CLEAR_ALL_SLOTS_SCHEMA,
    )
    _LOGGER.info("Registered management services")

    hass.services.async_register(
        DOMAIN,
        SERVICE_UPDATE_GLOBAL_SETTINGS,
        update_global_settings_wrapper,
        schema=UPDATE_GLOBAL_SETTINGS_SCHEMA,
    )
    _LOGGER.info("Registered system services")


def _repair_parent_child_links(hass: HomeAssistant) -> None:
    """Auto-repair one-sided parent-child lock relationships.

    Iterates all loaded locks. If a child claims a parent but the parent's
    child_lock_ids list doesn't contain the child, the child is added.
    Also warns (but does not remove) if a parent lists a child that doesn't
    claim it as parent.
    """
    all_locks = {}
    for entry_id, entry_data in hass.data[DOMAIN].items():
        if isinstance(entry_data, dict):
            lock_obj = entry_data.get(PRIMARY_LOCK)
            if lock_obj:
                all_locks[lock_obj.lock_entity_id] = lock_obj

    for entity_id, lock_obj in all_locks.items():
        # Forward check: child claims parent -> ensure parent knows about child
        if lock_obj.parent_lock_id:
            parent = all_locks.get(lock_obj.parent_lock_id)
            if parent and entity_id not in parent.child_lock_ids:
                parent.child_lock_ids.append(entity_id)
                _LOGGER.warning(
                    "Auto-repaired: added %s to %s's child_lock_ids",
                    entity_id,
                    parent.lock_entity_id,
                )

        # Reverse check: parent lists child -> warn if child doesn't claim parent
        if lock_obj.child_lock_ids:
            for child_id in lock_obj.child_lock_ids:
                child = all_locks.get(child_id)
                if child and child.parent_lock_id != entity_id:
                    _LOGGER.warning(
                        "Parent %s lists %s as child, but child's parent_lock_id"
                        " is %s (not removing — may be intentional)",
                        entity_id,
                        child_id,
                        child.parent_lock_id,
                    )


class SmartLockManagerDataUpdateCoordinator(DataUpdateCoordinator):
    """Class to manage fetching data from the lock."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Initialize."""
        self.entry = entry
        self.lock_name = entry.data.get("lock_name", "Smart Lock")

        # Track periodic retry state for permanently failed slots.
        # Key: "{entity_id}_slot_{slot_num}"
        # Value: {"last_retry": datetime, "periodic_attempts": int}
        self._periodic_retry_tracker: dict[str, dict] = {}

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

            # Get the lock object from hass data using config entry ID
            entry_data = self.hass.data[DOMAIN].get(self.entry.entry_id)
            lock = (
                entry_data.get(PRIMARY_LOCK) if isinstance(entry_data, dict) else None
            )

            if not lock:
                _LOGGER.warning(
                    "Coordinator: no lock object found for entry %s (%s)",
                    self.entry.entry_id,
                    self.lock_name,
                )
                return {}

            if lock:
                # Step 0: Auto-repair parent-child link inconsistencies
                _repair_parent_child_links(self.hass)

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
                    from zwave_js_server.util.lock import (
                        get_usercode,
                        get_usercode_from_node,
                    )

                    ent_reg = async_get_entity_registry(self.hass)
                    entity_entry = ent_reg.async_get(lock.lock_entity_id)

                    if not entity_entry:
                        _LOGGER.warning(
                            "Coordinator: entity %s not in registry",
                            lock.lock_entity_id,
                        )
                    elif entity_entry.platform != "zwave_js":
                        _LOGGER.warning(
                            "Coordinator: entity %s platform is '%s', not zwave_js",
                            lock.lock_entity_id,
                            entity_entry.platform,
                        )
                    else:
                        try:
                            # async_get_node_from_entity_id is @callback (sync)
                            # -- do NOT await
                            node = async_get_node_from_entity_id(
                                self.hass,
                                lock.lock_entity_id,
                                ent_reg=ent_reg,
                            )
                        except Exception as exc:
                            _LOGGER.warning(
                                "Coordinator: failed to get Z-Wave node for %s: %s",
                                lock.lock_entity_id,
                                exc,
                            )
                            node = None

                        _LOGGER.info(
                            "Coordinator: Z-Wave node for %s: %s (type: %s)",
                            lock.lock_entity_id,
                            node,
                            type(node).__name__ if node else "None",
                        )
                        if node:
                            # Quick scan of first 10 slots only (performance
                            # optimization). Use get_usercode (sync, cached
                            # ValueDB) to avoid blocking startup.
                            for slot in range(1, 11):
                                try:
                                    code_data = get_usercode(node, slot)
                                except Exception:
                                    code_data = None

                                # Diagnostic: log raw get_usercode result for
                                # slots where SLM expects a code but data looks wrong
                                if (
                                    code_data is not None
                                    and lock
                                    and slot in lock.code_slots
                                    and lock.code_slots[slot].pin_code
                                ):
                                    _LOGGER.debug(
                                        "Coordinator: raw get_usercode slot %s:"
                                        " usercode=%s, in_use=%s",
                                        slot,
                                        repr(code_data.get("usercode", "MISSING"))[:10],
                                        code_data.get("in_use"),
                                    )

                                # If cache is empty OR returns no usercode but SLM
                                # expects a code in this slot, force-refresh from node
                                if (
                                    (code_data is None or not code_data.get("usercode"))
                                    and lock
                                    and slot in lock.code_slots
                                    and lock.code_slots[slot].pin_code
                                ):
                                    try:
                                        _LOGGER.debug(
                                            "Coordinator: cache miss/empty for slot %s"
                                            " with expected code, querying node",
                                            slot,
                                        )
                                        fallback_data = await get_usercode_from_node(
                                            node, slot
                                        )
                                        _LOGGER.debug(
                                            "Coordinator: get_usercode_from_node"
                                            " slot %s returned: usercode=%s, in_use=%s",
                                            slot,
                                            repr(
                                                fallback_data.get("usercode", "MISSING")
                                                if fallback_data
                                                else "None"
                                            )[:10],
                                            (
                                                fallback_data.get("in_use")
                                                if fallback_data
                                                else "N/A"
                                            ),
                                        )
                                        code_data = fallback_data
                                    except Exception as e:
                                        _LOGGER.debug(
                                            "Coordinator: async refresh failed"
                                            " for slot %s: %s",
                                            slot,
                                            e,
                                        )

                                try:
                                    if code_data and code_data.get("usercode"):
                                        in_use = code_data.get("in_use") is True
                                        zwave_codes[slot] = {
                                            "code": code_data.get("usercode"),
                                            "in_use": in_use,
                                            "status": (
                                                "occupied" if in_use else "disabled"
                                            ),
                                        }
                                except Exception as e:
                                    _LOGGER.debug(
                                        "Could not read Z-Wave slot %s: %s", slot, e
                                    )
                except Exception as e:
                    _LOGGER.warning(
                        "Z-Wave code reading failed for %s: %s",
                        lock.lock_entity_id,
                        e,
                    )
                    import traceback

                    _LOGGER.warning("Traceback: %s", traceback.format_exc())

                # Step 3: Update sync status and determine needed actions
                _LOGGER.info(
                    "Coordinator: Z-Wave codes for %s: %d found (slots: %s)",
                    lock.lock_entity_id,
                    len(zwave_codes),
                    list(zwave_codes.keys()) if zwave_codes else "none",
                )
                lock.update_sync_status(zwave_codes)

                # Clean up periodic retry tracker for slots that are now synced
                for sn, sl in lock.code_slots.items():
                    if sl.is_synced:
                        tk = f"{lock.lock_entity_id}_slot_{sn}"
                        if tk in self._periodic_retry_tracker:
                            _LOGGER.info(
                                "Slot %s on %s now synced, clearing"
                                " periodic retry tracker",
                                sn,
                                lock.lock_entity_id,
                            )
                            del self._periodic_retry_tracker[tk]

                # Log sync comparison for active slots
                for sn, sl in lock.code_slots.items():
                    if sl.is_active and sl.pin_code:
                        zw = zwave_codes.get(sn, {}).get("code")
                        _LOGGER.debug(
                            "Coordinator: slot %s sync: pin=%s vs zwave=%s -> %s",
                            sn,
                            sl.pin_code[:4] + "..." if sl.pin_code else None,
                            str(zw)[:4] + "..." if zw else None,
                            "synced" if sl.is_synced else "NOT synced",
                        )

                sync_actions = lock.get_slots_needing_sync(zwave_codes)
                if (
                    sync_actions.get("add")
                    or sync_actions.get("remove")
                    or sync_actions.get("retry")
                ):
                    _LOGGER.info(
                        "Coordinator: sync actions needed: add=%s, remove=%s, retry=%s",
                        sync_actions.get("add", []),
                        sync_actions.get("remove", []),
                        sync_actions.get("retry", []),
                    )

                # Step 4: Perform sync actions with retry logic
                for slot_number in sync_actions.get("add", []):
                    slot = lock.code_slots.get(slot_number)
                    if slot:
                        # Check if Z-Wave cached code already matches before
                        # attempting sync
                        cached_zwave_code = zwave_codes.get(slot_number, {}).get("code")
                        if cached_zwave_code and cached_zwave_code == slot.pin_code:
                            _LOGGER.info(
                                "Slot %s on %s already synced (code matches"
                                " Z-Wave cache), marking synchronized",
                                slot_number,
                                lock.lock_entity_id,
                            )
                            slot.is_synced = True
                            slot.sync_attempts = 0
                            slot.sync_error = None
                            continue

                        # Exponential backoff: 60s, 120s, 240s, 480s, max 600s
                        backoff_seconds = min(60 * (2**slot.sync_attempts), 600)
                        if (
                            slot.last_sync_attempt
                            and (
                                datetime.now() - slot.last_sync_attempt
                            ).total_seconds()
                            < backoff_seconds
                        ):
                            _LOGGER.debug(
                                "Skipping sync for slot %s (backoff %ss, attempt %s)",
                                slot_number,
                                backoff_seconds,
                                slot.sync_attempts,
                            )
                            continue

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
                        _LOGGER.warning(
                            "Coordinator: REMOVING code from physical lock %s slot %s"
                            " (reason: SLM intentionally disabled)",
                            lock.lock_entity_id,
                            slot_number,
                        )
                        if not slot:
                            # Should no longer happen since rogue code removal
                            # was removed
                            _LOGGER.warning(
                                "Coordinator: slot %s has no SLM entry but was"
                                " in remove list - skipping",
                                slot_number,
                            )
                            continue
                        elif not slot.is_active:
                            # Slot exists but disabled - remove from Z-Wave only
                            _LOGGER.info(
                                "Found disabled slot %s with code in Z-Wave,"
                                " removing from lock only",
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
                                "Auto-removing disabled slot %s from lock %s"
                                " (keeping Smart Lock Manager data)",
                                slot_number,
                                self.lock_name,
                            )
                        else:
                            # Slot is active but needs removal for another reason
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

                # Step 5: Periodic retry for permanently failed slots
                # Instead of just logging, actually re-attempt sync on a
                # schedule: every 30 min for the first 30 cumulative attempts,
                # then every 2 hours after that. Only ONE slot retried per cycle
                # to avoid flooding the Z-Wave mesh.
                retry_slots = sync_actions.get("retry", [])
                if retry_slots:
                    now = datetime.now()
                    best_candidate = None
                    best_last_retry = None

                    for slot_number in retry_slots:
                        slot = lock.code_slots.get(slot_number)
                        if not slot:
                            continue

                        tracker_key = f"{lock.lock_entity_id}_slot_{slot_number}"
                        tracker = self._periodic_retry_tracker.get(tracker_key, {})
                        last_retry = tracker.get("last_retry")
                        periodic_attempts = tracker.get("periodic_attempts", 0)

                        # Determine retry interval based on cumulative attempts
                        # (original 10 + periodic retries * 10 each round)
                        total_attempts = slot.sync_attempts + (periodic_attempts * 10)
                        if total_attempts >= 30:
                            retry_interval = timedelta(hours=2)
                        else:
                            retry_interval = timedelta(minutes=30)

                        # Check if enough time has passed
                        if last_retry and (now - last_retry) < retry_interval:
                            continue

                        # Pick the slot with the oldest (or missing) retry time
                        if best_candidate is None or (
                            last_retry is None
                            or (
                                best_last_retry is not None
                                and last_retry < best_last_retry
                            )
                        ):
                            best_candidate = slot_number
                            best_last_retry = last_retry

                    # Retry ONE slot per cycle
                    if best_candidate is not None:
                        slot = lock.code_slots.get(best_candidate)
                        if slot:
                            tracker_key = (
                                f"{lock.lock_entity_id}_slot_" f"{best_candidate}"
                            )
                            tracker = self._periodic_retry_tracker.get(tracker_key, {})
                            periodic_attempts = tracker.get("periodic_attempts", 0)
                            old_attempts = slot.sync_attempts

                            # Reset slot sync state for a fresh start
                            slot.sync_attempts = 0
                            slot.sync_error = None
                            slot.is_synced = False

                            _LOGGER.info(
                                "Periodic retry: re-attempting sync for"
                                " slot %s on %s (was stuck at %s"
                                " attempts, periodic retry #%s)",
                                best_candidate,
                                lock.lock_entity_id,
                                old_attempts,
                                periodic_attempts + 1,
                            )

                            try:
                                action = "enable" if slot.is_active else "disable"
                                await self.hass.services.async_call(
                                    DOMAIN,
                                    "sync_slot_to_zwave",
                                    {
                                        ATTR_ENTITY_ID: (lock.lock_entity_id),
                                        ATTR_CODE_SLOT: best_candidate,
                                        "action": action,
                                    },
                                )
                            except Exception as e:
                                _LOGGER.error(
                                    "Periodic retry failed for slot %s" " on %s: %s",
                                    best_candidate,
                                    lock.lock_entity_id,
                                    e,
                                )

                            # Update tracker regardless of success/failure
                            self._periodic_retry_tracker[tracker_key] = {
                                "last_retry": now,
                                "periodic_attempts": periodic_attempts + 1,
                            }

                            # Fire event for visibility
                            self.hass.bus.async_fire(
                                "smart_lock_manager_sync_retry",
                                {
                                    "entity_id": lock.lock_entity_id,
                                    "slot_number": best_candidate,
                                    "periodic_attempt": (periodic_attempts + 1),
                                    "previous_attempts": old_attempts,
                                },
                            )

                    # Still log warning for all stuck slots (once per hour)
                    for slot_number in retry_slots:
                        slot = lock.code_slots.get(slot_number)
                        if slot and slot.sync_error:
                            if slot.sync_attempts % 120 == 0:
                                _LOGGER.warning(
                                    "Slot %s sync stuck (attempt %s): %s",
                                    slot_number,
                                    slot.sync_attempts,
                                    slot.sync_error,
                                )
                                self.hass.bus.async_fire(
                                    "smart_lock_manager_sync_error",
                                    {
                                        "entity_id": lock.lock_entity_id,
                                        "slot_number": slot_number,
                                        "error": slot.sync_error,
                                        "attempts": slot.sync_attempts,
                                    },
                                )

                # Step 6: Auto-sync codes to child locks if this is a main lock
                if lock.is_main_lock and lock.child_lock_ids:
                    _LOGGER.debug(
                        "Main lock %s checking for child lock sync, children: %s",
                        lock.lock_name,
                        lock.child_lock_ids,
                    )

                    # Find child locks
                    child_locks = []
                    for entry_id, entry_data in self.hass.data[DOMAIN].items():
                        if isinstance(entry_data, dict):  # Skip global_settings
                            child_lock = entry_data.get(PRIMARY_LOCK)
                            if (
                                child_lock
                                and child_lock.lock_entity_id in lock.child_lock_ids
                            ):
                                child_locks.append(child_lock)

                    if child_locks:
                        # Check if main lock codes have changed since last sync
                        main_lock_changed = False
                        for slot_num, slot in lock.code_slots.items():
                            if slot.is_active and (
                                slot_num in sync_actions.get("add", [])
                                or slot_num in sync_actions.get("remove", [])
                            ):
                                main_lock_changed = True
                                break

                        # Sync to child locks if main lock changed
                        if main_lock_changed:
                            _LOGGER.info(
                                "Main lock %s codes changed, syncing to %d child locks",
                                lock.lock_name,
                                len(child_locks),
                            )

                            try:
                                await self.hass.services.async_call(
                                    DOMAIN,
                                    SERVICE_SYNC_CHILD_LOCKS,
                                    {ATTR_ENTITY_ID: lock.lock_entity_id},
                                )
                                _LOGGER.debug(
                                    "Auto-triggered child lock sync for %s",
                                    lock.lock_name,
                                )
                            except Exception as e:
                                _LOGGER.error(
                                    "Failed to auto-sync child locks for %s: %s",
                                    lock.lock_name,
                                    e,
                                )

            # Persist updated sync status to storage
            if lock:
                await _save_lock_data(self.hass, lock, self.entry.entry_id)

            return {
                "user_codes": {},
                "lock_state": "unknown",
                "connection_status": True,
            }

        except Exception as exception:
            raise UpdateFailed(
                f"Error communicating with lock: {exception}"
            ) from exception
