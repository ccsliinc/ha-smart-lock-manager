"""Smart Lock Manager Integration."""

import logging
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.storage import Store

from .api.http import async_register_http_views, async_unregister_http_views

# Module-level logger so log entries appear under
# ``custom_components.smart_lock_manager`` (not ``.const``).
_LOGGER = logging.getLogger(__name__)

from .alert_engine import ALERT_ENGINE_KEY, AlertEngine
from .auto_lock import AUTO_LOCK_ENGINE_KEY, AutoLockEngine

# hass.data key holding the unsub for the zone-settings-changed event listener
# that drives live engine re-subscription (Task 2). Registered once per process.
ENGINE_REFRESH_LISTENER_KEY = "smart_lock_manager_engine_refresh_listener"
# hass.data key for the global-settings-changed listener that reschedules the
# alert engine's periodic sweeps live (set_sweep_intervals). Once per process.
GLOBAL_SETTINGS_REFRESH_LISTENER_KEY = (
    "smart_lock_manager_global_settings_refresh_listener"
)
from .const import (
    DOMAIN,
    ISSUE_URL,
    PLATFORMS,
    PRIMARY_LOCK,
    VERSION,
)
from .coordinator import SmartLockManagerDataUpdateCoordinator
from .dev_mock import is_dev_mock
from .entry_setup import build_lock_from_stored_data
from .frontend.panel import async_register_panel, async_unregister_panel
from .gating import engines_active, prime_flags_cache
from .models.lock import SmartLockManagerLock

# Service layer (extracted). The schema/helper re-exports below keep the
# frozen public names resolvable from the package root for tests + callers.
from .services.registration import (  # noqa: E402,F401
    SET_SWEEP_INTERVALS_SCHEMA,
    _build_access_log_handler,
    _resolve_lock_for_node,
    async_register_services,
    async_unregister_services,
    map_access_control_event,
)
from .zone_runtime import (
    async_ensure_zones_loaded,
    async_run_migration_if_needed,
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

    # Build + hydrate the lock object from storage (lock construction, slot
    # restore, settings/relationship restore, access-log restore, and slot
    # reconcile to the configured count). Extracted to entry_setup for clarity.
    lock = build_lock_from_stored_data(entry, lock_name, lock_entity_id, stored_data)

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

    # Register services BEFORE initial sync. The service layer (core +
    # advanced services, the global access-log listener, and the dev-gated
    # services) lives in services.registration; behavior + ordering are
    # identical to the prior inline registration.
    await async_register_services(hass)

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

    # Prime the file-based engine flags OFF the event loop BEFORE the first gate
    # check below. gating's hot path (engines_active / engines_enabled /
    # real_notify_enabled / real_autolock_enabled) reads an in-memory cache with
    # NO disk I/O; the blocking open()/json parse lives in prime_flags_cache and
    # must run in the executor so HA's blocking-call detector stays quiet.
    await hass.async_add_executor_job(prime_flags_cache)

    # OBSERVE/DRY-RUN alert detection engine (Phase 4d). Instantiated exactly
    # once per HA process when ``engines_active()`` (dev-mock OR the explicit
    # SLM_ENABLE_ENGINES flag). With both off (production default) it is never
    # constructed and stays fully inert. In dev it drives mock locks; in PROD
    # OBSERVE it detects + records against the REAL office entities in parallel
    # with the live pyscripts. It sends ZERO notifications unless the
    # independent SLM_ENABLE_REAL_NOTIFY flag is set (and not dev). Started
    # after zones load so it can enumerate every member lock. The companion
    # dev_simulate_alert service is DEV-MOCK-ONLY (see its own guard below).
    if engines_active() and ALERT_ENGINE_KEY not in hass.data:
        engine = AlertEngine(hass)
        hass.data[ALERT_ENGINE_KEY] = engine
        await engine.async_start()

        if is_dev_mock() and not hass.services.has_service(
            DOMAIN, "dev_simulate_alert"
        ):

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

    # AUTO-LOCK engine (Phase 4c construction, Phase 4d mode-gating). Built once
    # per HA process when ``engines_active()`` (dev-mock OR SLM_ENABLE_ENGINES);
    # with both off (production default) it is never constructed. In dev it
    # issues lock.lock against the dummy template locks. In PROD OBSERVE it
    # RECORDS "would auto-lock" intents and issues NO lock.lock; a real lock.lock
    # requires the independent SLM_ENABLE_REAL_AUTOLOCK flag (default OFF).
    # Started after zones load so it can schedule COB triggers + idle timers per
    # zone. The companion dev_trigger_autolock service is DEV-MOCK-ONLY (guard
    # below).
    if engines_active() and AUTO_LOCK_ENGINE_KEY not in hass.data:
        auto_lock_engine = AutoLockEngine(hass)
        hass.data[AUTO_LOCK_ENGINE_KEY] = auto_lock_engine
        await auto_lock_engine.async_start()

        if is_dev_mock() and not hass.services.has_service(
            DOMAIN, "dev_trigger_autolock"
        ):

            async def dev_trigger_autolock_wrapper(service_call: ServiceCall) -> None:
                active = hass.data.get(AUTO_LOCK_ENGINE_KEY)
                if active is None:
                    return
                await active.dev_trigger(
                    service_call.data["zone_id"],
                    service_call.data["mode"],
                    fail_verify=service_call.data.get("fail_verify", False),
                )

            hass.services.async_register(
                DOMAIN,
                "dev_trigger_autolock",
                dev_trigger_autolock_wrapper,
                schema=vol.Schema(
                    {
                        vol.Required("zone_id"): cv.string,
                        vol.Required("mode"): vol.In(["scheduled", "idle"]),
                        vol.Optional("fail_verify"): cv.boolean,
                    }
                ),
            )
            _LOGGER.info(
                "DEV: registered smart_lock_manager.dev_trigger_autolock service "
                "(auto-lock engine)"
            )

    # Live re-subscribe: when a zone's settings change (via update_zone_settings,
    # which fires ``smart_lock_manager_zone_settings_updated``), tell both engines
    # to rebuild their per-zone subscriptions so a newly enabled detector / idle
    # auto-lock / COB schedule takes effect WITHOUT an HA restart. Registered once
    # per process and only when the engines are active. ``async_refresh`` is
    # idempotent (cancels old listeners/timers first), so repeated edits never
    # accumulate duplicate timers.
    if engines_active() and ENGINE_REFRESH_LISTENER_KEY not in hass.data:

        async def _on_zone_settings_updated(event: Any) -> None:
            # Re-prime the file-based flags OFF the loop first so a flags-file
            # edit paired with a settings change picks up new real-action values
            # without blocking the event loop, then rebuild engine subscriptions.
            await hass.async_add_executor_job(prime_flags_cache)
            alert_engine = hass.data.get(ALERT_ENGINE_KEY)
            if alert_engine is not None:
                alert_engine.async_refresh()
            auto_engine = hass.data.get(AUTO_LOCK_ENGINE_KEY)
            if auto_engine is not None:
                auto_engine.async_refresh()

        hass.data[ENGINE_REFRESH_LISTENER_KEY] = hass.bus.async_listen(
            "smart_lock_manager_zone_settings_updated", _on_zone_settings_updated
        )

        async def _on_global_settings_updated(event: Any) -> None:
            # Re-load the persisted global settings into the synchronous cache
            # FIRST (so _subscribe reads the new cadences), then rebuild the
            # alert-engine subscriptions so the sweep timers reschedule live —
            # no HA restart. async_refresh is idempotent (tears the old timers
            # down before re-subscribing) so repeated edits never accumulate.
            from .storage import load_global_settings

            await load_global_settings(hass)
            alert_engine = hass.data.get(ALERT_ENGINE_KEY)
            if alert_engine is not None:
                alert_engine.async_refresh()

        hass.data[GLOBAL_SETTINGS_REFRESH_LISTENER_KEY] = hass.bus.async_listen(
            "smart_lock_manager_global_settings_updated",
            _on_global_settings_updated,
        )
        _LOGGER.info(
            "Registered engine live-refresh listeners for zone + global settings"
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

        # If no lock entries remain, tear down the per-process engines + their
        # live-refresh listeners. The global access-log listener teardown +
        # service removal now live in async_unregister_services (called below).
        remaining_locks = [v for v in hass.data[DOMAIN].values() if isinstance(v, dict)]
        if not remaining_locks:
            # Tear down the alert engine (present under dev-mock OR
            # SLM_ENABLE_ENGINES) so its state listeners + timers are released.
            engine = hass.data.pop(ALERT_ENGINE_KEY, None)
            if engine is not None:
                engine.async_stop()

            # Tear down the auto-lock engine (present under dev-mock OR
            # SLM_ENABLE_ENGINES) so its time triggers, listeners + idle timers
            # are released.
            auto_lock_engine = hass.data.pop(AUTO_LOCK_ENGINE_KEY, None)
            if auto_lock_engine is not None:
                auto_lock_engine.async_stop()

            # Remove the engine live-refresh event listeners.
            refresh_unsub = hass.data.pop(ENGINE_REFRESH_LISTENER_KEY, None)
            if refresh_unsub is not None:
                refresh_unsub()
            global_refresh_unsub = hass.data.pop(
                GLOBAL_SETTINGS_REFRESH_LISTENER_KEY, None
            )
            if global_refresh_unsub is not None:
                global_refresh_unsub()

        # Tear down the global access-log listener (when no locks remain) and
        # remove every registered service (when DOMAIN is empty). Both guards
        # are replicated internally so the net effect is identical.
        await async_unregister_services(hass)

    return bool(unload_ok)


# NOTE (Zone model, Phase 1): the legacy ``_repair_parent_child_links`` helper
# was removed. Parent/child link reconciliation is obsolete now that zones own
# the canonical code set and each member lock is synced independently from its
# owning zone. The one-time parent/child -> zone migration lives in
# ``zone_runtime.async_run_migration_if_needed``.
