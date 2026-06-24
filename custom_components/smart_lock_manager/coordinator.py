"""Data update coordinator for the Smart Lock Manager integration.

Holds ``SmartLockManagerDataUpdateCoordinator``, the per-lock
``DataUpdateCoordinator`` that drives the 30-second Z-Wave read/sync cycle.
Extracted verbatim from ``__init__.py`` (behavior-preserving) to keep the
package entry point under the file-size limit. The only ``__init__`` symbol it
needs (``_save_lock_data``) is imported LAZILY inside the methods that use it to
avoid a circular import (``__init__`` imports this module).
"""

import logging
from datetime import datetime, timedelta
from typing import Any, Dict

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_ENTITY_ID
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import ATTR_CODE_SLOT, DOMAIN, PRIMARY_LOCK
from .coordinator_helpers import read_zwave_codes
from .zone_runtime import mirror_owning_zone_to_member

# Module-level logger so log entries appear under
# ``custom_components.smart_lock_manager`` (matching the prior __name__ in
# __init__.py, which resolved to that exact string).
_LOGGER = logging.getLogger("custom_components.smart_lock_manager")


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

                # Step 2: Read current Z-Wave codes every 30 seconds.
                # Extracted to coordinator_helpers.read_zwave_codes (pure read,
                # no self/lock mutation, no events) to keep this file under the
                # size limit. Behavior is identical to the prior inline block.
                zwave_codes = read_zwave_codes(self.hass, lock)

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
                from . import _save_lock_data

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
