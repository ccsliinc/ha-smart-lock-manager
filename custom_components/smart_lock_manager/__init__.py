"""Smart Lock Manager Integration."""

import logging
from datetime import datetime, timedelta
from typing import Any, Callable, Dict, Optional

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

from .alert_engine import ALERT_ENGINE_KEY, AlertEngine
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
    SERVICE_READ_ZWAVE_CODES,
    SERVICE_REFRESH_CODES,
    SERVICE_REMOVE_LOCK_FROM_ZONE,
    SERVICE_RESET_SLOT_USAGE,
    SERVICE_RESET_SYNC,
    SERVICE_RESIZE_SLOTS,
    SERVICE_SET_CODE,
    SERVICE_SET_CODE_ADVANCED,
    SERVICE_UPDATE_GLOBAL_SETTINGS,
    SERVICE_UPDATE_LOCK_SETTINGS,
    SERVICE_UPDATE_ZONE,
    SERVICE_UPDATE_ZONE_SETTINGS,
    VERSION,
)
from .dev_mock import (
    fire_mock_notification,
    is_dev_mock,
    mock_get_usercode,
    mock_node_for_entity,
)
from .frontend.panel import async_register_panel, async_unregister_panel
from .models.lock import ACCESS_LOG_MAX_ENTRIES, SmartLockManagerLock
from .services.lock_services import LockServices
from .services.management_services import ManagementServices
from .services.slot_services import SlotServices
from .services.system_services import SystemServices
from .services.zone_services import ZoneServices
from .services.zone_settings_service import ZoneSettingsService
from .services.zwave_services import ZWaveServices
from .zone_runtime import (
    async_ensure_zones_loaded,
    async_run_migration_if_needed,
    mirror_owning_zone_to_member,
)


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


# ---------------------------------------------------------------------------
# Z-Wave Access Control notification -> access-log mapping
# ---------------------------------------------------------------------------
# Kwikset-style Access Control (command_class 113) event codes mapped to an
# (action, source) tuple. Keypad events (5/6) additionally carry a
# parameters.userId pointing at the SLM code slot.
#   1 = manual lock (thumbturn)        2 = manual unlock (thumbturn)
#   3 = RF lock (app/HA)               4 = RF unlock (app/HA)
#   5 = keypad lock (-> userId)        6 = keypad unlock (-> userId)
#   9 = auto-lock                      11 = lock jammed
ACCESS_CONTROL_EVENT_MAP: Dict[int, Dict[str, str]] = {
    1: {"action": "locked", "source": "manual"},
    2: {"action": "unlocked", "source": "manual"},
    3: {"action": "locked", "source": "rf"},
    4: {"action": "unlocked", "source": "rf"},
    5: {"action": "locked", "source": "keypad"},
    6: {"action": "unlocked", "source": "keypad"},
    9: {"action": "locked", "source": "auto"},
    11: {"action": "jammed", "source": "manual"},
}

# Z-Wave Notification command class number for Access Control events.
NOTIFICATION_COMMAND_CLASS = 113


def map_access_control_event(event_code: int) -> Optional[Dict[str, str]]:
    """Map a Z-Wave Access Control event code to action/source.

    - Description: Translate a Kwikset Access Control event code into the
      ``{"action", "source"}`` dict used by the access log.
    - Inputs: event_code (int) — the ``event`` field of the notification.
    - Outputs: dict with "action" and "source", or None if unrecognized.
    - Example: ``map_access_control_event(6)`` ->
      ``{"action": "unlocked", "source": "keypad"}``
    """
    mapping = ACCESS_CONTROL_EVENT_MAP.get(event_code)
    return dict(mapping) if mapping else None


def _resolve_lock_for_node(
    hass: HomeAssistant, node_id: Optional[int]
) -> Optional[SmartLockManagerLock]:
    """Find the SLM-managed lock whose Z-Wave node matches ``node_id``.

    - Description: SLM never stores node_id on the lock object, so resolve
      each managed lock's entity_id to its Z-Wave node and compare node_id.
    - Inputs: node_id (int) from the notification event data.
    - Outputs: matching SmartLockManagerLock or None.
    """
    if node_id is None:
        return None

    dev_mock = is_dev_mock()
    if not dev_mock:
        try:
            from homeassistant.components.zwave_js.helpers import (
                async_get_node_from_entity_id,
            )
        except Exception:  # pragma: no cover - zwave_js always present in prod
            return None

    for entry_data in hass.data.get(DOMAIN, {}).values():
        if not isinstance(entry_data, dict):
            continue
        lock: Optional[SmartLockManagerLock] = entry_data.get(PRIMARY_LOCK)
        if not lock or not lock.lock_entity_id:
            continue
        try:
            if dev_mock:
                # DEV: resolve via the seeded entity->node_id table.
                node = mock_node_for_entity(lock.lock_entity_id)
            else:
                # async_get_node_from_entity_id is @callback (sync) — do NOT await
                node = async_get_node_from_entity_id(hass, lock.lock_entity_id)
        except Exception:
            node = None
        if node is not None and getattr(node, "node_id", None) == node_id:
            return lock

    return None


def _build_access_log_handler(hass: HomeAssistant) -> Callable:
    """Create the ``zwave_js_notification`` event handler for the access log.

    - Description: Returns an async listener that records lock/unlock/jam
      events (with user attribution for keypad events) on the matching SLM
      lock's bounded access log, then persists the lock data.
    - Inputs: hass (HomeAssistant).
    - Outputs: an async callable suitable for ``hass.bus.async_listen``.

    SECURITY: only user_name + slot number are logged — never PIN codes.
    """

    async def _handle_zwave_notification(event: Any) -> None:
        data = event.data or {}

        # Only Access Control (door lock) notifications carry lock events.
        if data.get("command_class") != NOTIFICATION_COMMAND_CLASS:
            return

        event_code = data.get("event")
        if not isinstance(event_code, int):
            return

        mapping = map_access_control_event(event_code)
        if not mapping:
            _LOGGER.debug(
                "Access log: ignoring unmapped Access Control event %s", event_code
            )
            return

        lock = _resolve_lock_for_node(hass, data.get("node_id"))
        if not lock:
            _LOGGER.debug(
                "Access log: no SLM lock matches node_id %s", data.get("node_id")
            )
            return

        # Resolve user attribution for keypad events via parameters.userId.
        user_name: Optional[str] = None
        slot: Optional[int] = None
        if mapping["source"] == "keypad":
            params = data.get("parameters") or {}
            raw_slot = params.get("userId")
            if isinstance(raw_slot, int):
                slot = raw_slot
                slot_obj = lock.code_slots.get(slot)
                user_name = (
                    slot_obj.user_name
                    if slot_obj and slot_obj.user_name
                    else f"slot {slot}"
                )

        entry = lock.add_access_log_entry(
            action=mapping["action"],
            source=mapping["source"],
            user_name=user_name,
            slot=slot,
        )
        _LOGGER.info(
            "Access log [%s]: %s via %s%s",
            lock.lock_name,
            entry["action"],
            entry["source"],
            f" by {user_name} (slot {slot})" if user_name else "",
        )

        # Persist the updated access log. Find this lock's entry_id to save.
        for entry_id, entry_data in hass.data.get(DOMAIN, {}).items():
            if isinstance(entry_data, dict) and entry_data.get(PRIMARY_LOCK) is lock:
                await _save_lock_data(hass, lock, entry_id)
                break

    return _handle_zwave_notification


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

GET_USAGE_STATS_SCHEMA = vol.Schema({vol.Required(ATTR_ENTITY_ID): cv.entity_id})

CLEAR_ALL_SLOTS_SCHEMA = vol.Schema({vol.Required(ATTR_ENTITY_ID): cv.entity_id})

UPDATE_LOCK_SETTINGS_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_ENTITY_ID): cv.entity_id,
        vol.Optional("friendly_name"): str,
        vol.Optional("slot_count"): vol.All(int, vol.Range(min=1, max=50)),
    }
)

# Zone-management service schemas.
CREATE_ZONE_SCHEMA = vol.Schema(
    {
        vol.Required("name"): str,
        vol.Optional("member_lock_entity_ids"): [cv.entity_id],
    }
)

DELETE_ZONE_SCHEMA = vol.Schema({vol.Required("zone_id"): str})

APPLY_ZONE_CODES_SCHEMA = vol.Schema({vol.Required("zone_id"): str})

CLEAR_ZONE_CODES_SCHEMA = vol.Schema({vol.Required("zone_id"): str})

UPDATE_ZONE_SCHEMA = vol.Schema(
    {
        vol.Required("zone_id"): str,
        vol.Required("name"): str,
    }
)

ZONE_MEMBER_SCHEMA = vol.Schema(
    {
        vol.Required("zone_id"): str,
        vol.Required("lock_entity_id"): cv.entity_id,
    }
)

# The settings payload is a free-form nested dict of config blocks; the service
# merges per-block and the model rebuilds tolerantly, so deep validation lives
# in models.zone_settings rather than the voluptuous schema.
UPDATE_ZONE_SETTINGS_SCHEMA = vol.Schema(
    {
        vol.Required("zone_id"): str,
        vol.Required("settings"): dict,
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
        _LOGGER.debug(
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
            _LOGGER.debug(
                "Restoring slot %s: user=%s, pin=%s, active=%s",
                slot_num,
                user_name,
                "****" if pin_code else None,
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
            _LOGGER.debug(
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
        _LOGGER.debug(
            "Initialized default friendly name for %s: %s", lock_name, lock_name
        )

    # Restore parent/child lock relationships from storage
    if stored_data.get("is_main_lock") is not None:
        lock.is_main_lock = stored_data["is_main_lock"]
    if stored_data.get("parent_lock_id"):
        lock.parent_lock_id = stored_data["parent_lock_id"]
    if stored_data.get("child_lock_ids"):
        lock.child_lock_ids = stored_data["child_lock_ids"]

    # Restore the access log (bounded) from storage so history survives restart
    if stored_data.get("access_log"):
        lock.access_log = stored_data["access_log"][-ACCESS_LOG_MAX_ENTRIES:]
        _LOGGER.debug(
            "Restored %s access-log entries for %s",
            len(lock.access_log),
            lock_name,
        )

    # Reconcile the slot collection to the configured count. The config entry
    # is authoritative for slot count: storage may hold MORE or FEWER slots
    # than ``entry.data['slots']`` after an OptionsFlow slot-count change.
    #   - Shrinking: drop slots above the configured count so removed slots
    #     are NOT resurrected from storage and leave no orphan stored data.
    #   - Growing: add empty slots up to the configured count.
    # Surviving slots keep their restored data. Reconcile against the ACTUAL
    # restored key set (not ``lock.slots``, which was set from entry.data at
    # construction), then re-save so pruned slots vanish from persistent
    # storage on this very setup.
    configured_slots = entry.data.get("slots", 10)
    valid_range = set(range(lock.start_from, lock.start_from + configured_slots))
    current_keys = set(lock.code_slots.keys())
    if current_keys != valid_range:
        from .models.lock import CodeSlot

        for slot_num in current_keys - valid_range:
            del lock.code_slots[slot_num]
        for slot_num in valid_range - current_keys:
            lock.code_slots[slot_num] = CodeSlot(slot_number=slot_num)
        lock.slots = configured_slots
        _LOGGER.debug(
            "Reconciled %s to configured %s slots (stored: %s -> now: %s)",
            lock_name,
            configured_slots,
            sorted(current_keys),
            sorted(lock.code_slots.keys()),
        )
    else:
        lock.slots = configured_slots

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

    # Fetch initial data and force full sync on startup
    await coordinator.async_config_entry_first_refresh()
    _LOGGER.info("Completed first refresh for %s coordinator", lock.lock_name)

    # Zone model (Phase 1): hydrate the zone registry from storage once, then
    # run the one-time parent/child -> zone migration if it has not happened.
    # The migration is idempotent (guarded by a persisted marker) and builds
    # zones from whatever locks are currently loaded. Because each lock is its
    # own config entry, the LAST entry to finish setup is the one that sees all
    # locks and actually builds every zone; earlier entries either find the
    # marker already set or build a partial set that the final pass completes.
    await async_ensure_zones_loaded(hass)
    await async_run_migration_if_needed(hass)

    # DEV-ONLY: OBSERVE-ONLY alert detection engine. Instantiated exactly once
    # per HA process and ONLY under SLM_DEV_MOCK, so it never runs in
    # production alongside the live pyscripts. It records alerts but sends ZERO
    # notifications. Started after zones are loaded so it can enumerate every
    # member lock. The companion dev_simulate_alert service lets each alert
    # type be triggered on demand without waiting for real conditions.
    if is_dev_mock() and ALERT_ENGINE_KEY not in hass.data:
        engine = AlertEngine(hass)
        hass.data[ALERT_ENGINE_KEY] = engine
        await engine.async_start()

        if not hass.services.has_service(DOMAIN, "dev_simulate_alert"):

            async def dev_simulate_alert_wrapper(service_call: ServiceCall) -> None:
                active = hass.data.get(ALERT_ENGINE_KEY)
                if active is None:
                    return
                data = dict(service_call.data)
                active.dev_simulate(
                    data.pop("alert_type"),
                    data.pop("entity_id"),
                    **data,
                )

            hass.services.async_register(
                DOMAIN,
                "dev_simulate_alert",
                dev_simulate_alert_wrapper,
                schema=vol.Schema(
                    {
                        vol.Required("alert_type"): vol.In(
                            [
                                "outside_hours",
                                "sustained_unlock",
                                "jam",
                                "low_battery",
                                "offline",
                            ]
                        ),
                        vol.Required("entity_id"): cv.entity_id,
                        vol.Optional("recover"): cv.boolean,
                        vol.Optional("seconds"): vol.Coerce(int),
                        vol.Optional("percent"): vol.Coerce(int),
                    }
                ),
            )
            _LOGGER.info(
                "DEV: registered smart_lock_manager.dev_simulate_alert service "
                "(observe-only alert engine)"
            )

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

    # Live reload: when the OptionsFlow updates entry.data (topology change),
    # reload this entry so async_setup_entry re-reads lock_name / lock_entity_id
    # / slots and reconciles the slot collection — no manual HA restart needed.
    entry.async_on_unload(entry.add_update_listener(async_reload_entry))

    return True


async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload the config entry after an options/topology update."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""

    # Unregister HTTP views
    await async_unregister_http_views(hass)

    # Unregister custom panel
    await async_unregister_panel(hass)

    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)

        # If no lock entries remain, tear down the global access-log listener.
        # The unsub lives in the separate ``{DOMAIN}_runtime`` namespace, not in
        # the per-entry registry (see async_setup_entry for rationale).
        remaining_locks = [v for v in hass.data[DOMAIN].values() if isinstance(v, dict)]
        if not remaining_locks:
            runtime = hass.data.get(f"{DOMAIN}_runtime", {})
            unsub = runtime.pop("access_log_unsub", None)
            if unsub:
                unsub()
                _LOGGER.info("Access log: removed zwave_js_notification listener")

            # Tear down the dev-only alert engine (only ever present under
            # SLM_DEV_MOCK) so its state listeners + timers are released.
            engine = hass.data.pop(ALERT_ENGINE_KEY, None)
            if engine is not None:
                engine.async_stop()

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
    _LOGGER.debug("Registered system services")


# NOTE (Zone model, Phase 1): the legacy ``_repair_parent_child_links`` helper
# was removed. Parent/child link reconciliation is obsolete now that zones own
# the canonical code set and each member lock is synced independently from its
# owning zone. The one-time parent/child -> zone migration lives in
# ``zone_runtime.async_run_migration_if_needed``.


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
                # Step 0 (Zone model): mirror the owning zone's canonical code
                # slots onto this member lock BEFORE computing sync actions, so
                # the per-lock sync logic below pushes the zone's codes to this
                # member's Z-Wave node. If the lock is unhomed (no zone), it
                # keeps its own slots and syncs them as before.
                owning_zone = mirror_owning_zone_to_member(self.hass, lock)
                if owning_zone is not None:
                    _LOGGER.debug(
                        "Coordinator: %s obeys zone '%s' (%d configured codes)",
                        lock.lock_entity_id,
                        owning_zone.name,
                        owning_zone.get_configured_codes_count(),
                    )

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
                    )

                    ent_reg = async_get_entity_registry(self.hass)
                    entity_entry = ent_reg.async_get(lock.lock_entity_id)

                    dev_mock = is_dev_mock()

                    if dev_mock:
                        # DEV: bypass the zwave_js platform guard and read codes
                        # from the in-memory MockValueDB via a fake node. The
                        # dummy locks are template entities (platform != zwave_js),
                        # so the real guard below would skip all reads.
                        node = mock_node_for_entity(lock.lock_entity_id)
                        if node:
                            for slot in range(1, 11):
                                try:
                                    code_data = mock_get_usercode(node, slot)
                                except Exception:
                                    code_data = None
                                if code_data and code_data.get("usercode"):
                                    in_use = code_data.get("in_use") is True
                                    zwave_codes[slot] = {
                                        "code": code_data.get("usercode"),
                                        "in_use": in_use,
                                        "status": (
                                            "occupied" if in_use else "disabled"
                                        ),
                                    }
                    elif not entity_entry:
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

                        _LOGGER.debug(
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
                                    _raw_usercode = code_data.get("usercode")
                                    _LOGGER.debug(
                                        "Coordinator: raw get_usercode slot %s:"
                                        " usercode=%s, in_use=%s",
                                        slot,
                                        (
                                            "MISSING"
                                            if _raw_usercode is None
                                            else "<set>"
                                        ),
                                        code_data.get("in_use"),
                                    )

                                # Cache empty for this slot — let sync logic handle it.
                                # No async fallback to avoid hammering the Z-Wave mesh.

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
                _LOGGER.debug(
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
                            _LOGGER.debug(
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
                            "Coordinator: slot %s sync: pin=%s vs zwave=%s"
                            " (match=%s) -> %s",
                            sn,
                            "<set>" if sl.pin_code else None,
                            "<set>" if zw else None,
                            str(zw) == str(sl.pin_code),
                            "synced" if sl.is_synced else "NOT synced",
                        )

                sync_actions = lock.get_slots_needing_sync(zwave_codes)
                if (
                    sync_actions.get("add")
                    or sync_actions.get("remove")
                    or sync_actions.get("retry")
                ):
                    _LOGGER.debug(
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
                            _LOGGER.debug(
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
                            _LOGGER.debug(
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
                            _LOGGER.debug(
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
                            _LOGGER.debug(
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
                            _LOGGER.debug(
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

                            _LOGGER.debug(
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

                # Step 6 (Zone model): the legacy parent -> child code push is
                # retired. Every member lock is now synced independently from
                # its owning zone via the Step 0 mirror above, so there is no
                # main-lock-to-child fan-out to perform here.

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
