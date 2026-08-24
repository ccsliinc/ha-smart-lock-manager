"""Config-entry setup helpers for Smart Lock Manager.

Extracted from ``__init__.py`` to keep ``async_setup_entry`` lean. Holds the
lock-construction + storage-hydration + slot-reconcile logic as a single,
behavior-identical helper.
"""

import logging
from datetime import datetime, time
from typing import Any, Dict, Optional

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_registry import async_get as async_get_entity_registry

from .dev_mock import is_dev_mock
from .models.lock import ACCESS_LOG_MAX_ENTRIES, CodeSlot, SmartLockManagerLock

# Module-level logger pinned to the same name used elsewhere in the package so
# emitted log records are byte-identical to the prior inline implementation.
_LOGGER = logging.getLogger("custom_components.smart_lock_manager")

# The upstream integration this one's lock entities normally come from. Used
# ONLY to distinguish "still booting" from "gone": if zwave_js has a config
# entry but has not finished loading, its lock entities cannot exist yet.
ZWAVE_JS_DOMAIN = "zwave_js"


@callback
def async_lock_not_ready_reason(
    hass: HomeAssistant, entry: ConfigEntry
) -> Optional[str]:
    """Return why this entry's lock is not ready YET, or None if it is ready.

    Description:
        Startup-ordering gate for ``async_setup_entry``. On a Home Assistant
        restart this integration can be set up BEFORE the integration that owns
        the backing lock entity (normally ``zwave_js``) has created its
        entities. Setting up anyway leaves the entry half-configured and
        surfaces to the user as a confusing downstream error (public issues #2
        and #4: "Failed to load services.yaml"). Returning a reason here lets
        the caller raise ``ConfigEntryNotReady`` so HA retries with its built-in
        exponential backoff instead.

        THREE-OUTCOME rule, and the reason this gates on EXISTENCE rather than
        on the lock being online:

        - ready: the entity has a state in the state machine, WHATEVER that
          state is. ``unavailable`` / ``unknown`` mean the lock is registered
          and its owning integration is up, but the physical device is not
          answering right now. That is a runtime condition the coordinator
          already handles, and it may never resolve (dead batteries, out of
          range, decommissioned). Gating on it would retry forever and leave a
          user with an offline lock no panel and no way to manage codes.
        - not ready: the entity has NO state, AND there is positive evidence it
          is still coming - either it is already in the entity registry (so its
          platform is mid-setup), or ``zwave_js`` is configured but not yet
          loaded. Both are startup-ordering conditions that DO resolve.
        - ready (deliberately, degraded): the entity has no state, is not in the
          registry, and nothing is pending. That is a renamed or deleted entity,
          not a race. Retrying forever would never fix it, so setup proceeds and
          the user keeps a working panel and a reachable options flow to point
          the entry at the right entity.

        Division of labour with the ``zwave_js``-unavailable REPAIR ISSUE
        (``coordinator_helpers.ISSUE_ZWAVE_JS_UNAVAILABLE``): the repair issue
        fires when the ``zwave_js`` package is not IMPORTABLE at all - a
        permanent, user-actionable "install the integration" condition. This
        gate fires only when ``zwave_js`` is installed and configured and simply
        has not finished loading. The two conditions are mutually exclusive by
        construction, so they cannot both nag about the same fault.

        The dev-mock path is exempt: its locks are template entities that do not
        come from ``zwave_js`` and may not exist at setup time at all.

    Inputs:
        hass (HomeAssistant): the running Home Assistant instance.
        entry (ConfigEntry): this integration's config entry.

    Outputs:
        Optional[str]: a human-readable reason naming the entity when the entry
        must be retried later, or ``None`` when setup may proceed.
    """
    if is_dev_mock():
        return None

    lock_entity_id = entry.data.get("lock_entity_id", "")
    if not lock_entity_id:
        # Nothing to wait on. A misconfigured entry is not a startup race, and
        # ConfigEntryNotReady would never clear it.
        return None

    if hass.states.get(lock_entity_id) is not None:
        return None

    # No state. Is there positive evidence the entity is still on its way?
    try:
        registry_entry = async_get_entity_registry(hass).async_get(lock_entity_id)
    except (KeyError, AttributeError):  # pragma: no cover - registry always set
        registry_entry = None

    if registry_entry is not None:
        return (
            f"Lock entity {lock_entity_id} is registered but has no state yet; "
            "the integration that provides it is still starting"
        )

    zwave_configured = bool(hass.config_entries.async_entries(ZWAVE_JS_DOMAIN))
    zwave_loaded = ZWAVE_JS_DOMAIN in hass.config.components
    if zwave_configured and not zwave_loaded:
        return (
            f"Lock entity {lock_entity_id} is not available yet; the "
            f"{ZWAVE_JS_DOMAIN} integration has not finished setting up"
        )

    # Entity unknown and nothing pending: treat as a configuration problem, not
    # a race. Proceed so the panel and the options flow stay reachable.
    _LOGGER.warning(
        "Lock entity %s is unknown to Home Assistant and nothing is pending; "
        "setting up anyway so the panel stays available - check the entity id "
        "in the integration options",
        lock_entity_id,
    )
    return None


def build_lock_from_stored_data(
    entry: ConfigEntry,
    lock_name: str,
    lock_entity_id: str,
    stored_data: Dict[str, Any],
) -> SmartLockManagerLock:
    """Build a fully-hydrated SmartLockManagerLock from persisted storage.

    Description:
        Creates the SmartLockManagerLock object, restores its code slots,
        settings, parent/child relationships and access log from ``stored_data``,
        then reconciles the slot collection to the config-entry's slot count.
    Inputs:
        entry (ConfigEntry): the config entry (authoritative for slot count/start).
        lock_name (str): resolved friendly lock name.
        lock_entity_id (str): the backing lock entity id.
        stored_data (Dict[str, Any]): the loaded persistent storage payload.
    Outputs:
        SmartLockManagerLock: the fully-hydrated, slot-reconciled lock object.
    """
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
            lock.settings.auto_lock_time = time.fromisoformat(
                settings_data["auto_lock_time"]
            )
        if settings_data.get("auto_unlock_time"):
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

    return lock
