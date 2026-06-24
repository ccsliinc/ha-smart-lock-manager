"""Config-entry setup helpers for Smart Lock Manager.

Extracted from ``__init__.py`` to keep ``async_setup_entry`` lean. Holds the
lock-construction + storage-hydration + slot-reconcile logic as a single,
behavior-identical helper.
"""

import logging
from datetime import datetime, time
from typing import Any, Dict

from homeassistant.config_entries import ConfigEntry

from .models.lock import ACCESS_LOG_MAX_ENTRIES, CodeSlot, SmartLockManagerLock

# Module-level logger pinned to the same name used elsewhere in the package so
# emitted log records are byte-identical to the prior inline implementation.
_LOGGER = logging.getLogger("custom_components.smart_lock_manager")


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
