"""Z-Wave integration services for Smart Lock Manager.

Handles reading and writing user codes to physical Z-Wave locks via the
zwave_js integration. Uses the sync ``get_usercode`` helper (reads from
the cached ValueDB) instead of the async ``get_usercode_from_node``
(which queries the device over the mesh and can hang if the node is
asleep or unreachable).
"""

import asyncio
import logging

from homeassistant.core import HomeAssistant, ServiceCall

from ..const import ATTR_CODE_SLOT, ATTR_ENTITY_ID, DOMAIN, PRIMARY_LOCK

_LOGGER = logging.getLogger(__name__)

# Timeout for the entire read_zwave_codes operation (seconds)
_READ_CODES_TIMEOUT = 30

# Z-Wave JS integration support
try:
    # async_get_node_from_entity_id is a @callback (sync), NOT a coroutine
    from homeassistant.components.zwave_js.helpers import async_get_node_from_entity_id

    # get_usercode is sync (reads from cached ValueDB) - safe and fast
    # get_usercode_from_node is async (queries the device) - can hang
    from zwave_js_server.util.lock import get_usercode

    ZWAVE_JS_AVAILABLE = True
except (ModuleNotFoundError, ImportError):
    ZWAVE_JS_AVAILABLE = False


class ZWaveServices:
    """Service handler for Z-Wave integration operations."""

    @staticmethod
    async def read_zwave_codes(hass: HomeAssistant, service_call: ServiceCall) -> None:
        """Read user codes from the physical Z-Wave lock.

        Uses the sync get_usercode() which reads from the cached ValueDB
        rather than querying the device over the mesh. This prevents hangs
        when nodes are asleep or unreachable.

        Args:
            hass: Home Assistant instance.
            service_call: Service call containing entity_id.
        """
        entity_id = service_call.data[ATTR_ENTITY_ID]

        if not ZWAVE_JS_AVAILABLE:
            _LOGGER.error("Z-Wave JS is not available for reading codes")
            return

        try:
            # Wrap entire operation in a timeout to prevent blocking HA startup
            async with asyncio.timeout(_READ_CODES_TIMEOUT):
                await _read_codes_inner(hass, entity_id)
        except TimeoutError:
            _LOGGER.warning(
                "read_zwave_codes timed out after %ss for %s - skipping",
                _READ_CODES_TIMEOUT,
                entity_id,
            )
        except Exception as e:
            _LOGGER.error("Error reading Z-Wave codes from %s: %s", entity_id, e)
            import traceback

            _LOGGER.error("Full traceback: %s", traceback.format_exc())

    @staticmethod
    async def sync_slot_to_zwave(
        hass: HomeAssistant, service_call: ServiceCall
    ) -> None:
        """Sync a specific slot to the Z-Wave lock (add or remove code).

        Args:
            hass: Home Assistant instance.
            service_call: Service call with entity_id, code_slot, action.
        """
        entity_id = service_call.data[ATTR_ENTITY_ID]
        slot_number = service_call.data[ATTR_CODE_SLOT]
        action = service_call.data.get("action", "auto")

        if not ZWAVE_JS_AVAILABLE:
            _LOGGER.error("Z-Wave JS is not available for syncing codes")
            return

        # Find the lock object
        lock = None
        for entry_id, entry_data in hass.data[DOMAIN].items():
            if isinstance(entry_data, dict):  # Skip global_settings
                lock_obj = entry_data.get(PRIMARY_LOCK)
                if lock_obj and lock_obj.lock_entity_id == entity_id:
                    lock = lock_obj
                    break

        if not lock:
            _LOGGER.error("No lock found for entity_id: %s", entity_id)
            return

        slot = lock.code_slots.get(slot_number)
        if not slot:
            _LOGGER.error("No slot %s found in lock %s", slot_number, lock.lock_name)
            return

        try:
            if action == "enable" and slot.is_active and slot.pin_code:
                # Validate PIN code before sending to Z-Wave
                if not slot.pin_code.isdigit():
                    raise ValueError(f"PIN code must be numeric only: {slot.pin_code}")
                if len(slot.pin_code) < 4 or len(slot.pin_code) > 8:
                    raise ValueError(
                        f"PIN code must be 4-8 digits (length: {len(slot.pin_code)})"
                    )

                # Check cached code to avoid unnecessary writes
                try:
                    node = async_get_node_from_entity_id(hass, entity_id)
                    if node:
                        current_code_info = get_usercode(node, slot_number)
                        current_code = (
                            current_code_info.get("usercode")
                            if current_code_info
                            else None
                        )
                        current_in_use = (
                            current_code_info.get("in_use") is True
                            if current_code_info
                            else False
                        )
                        if (
                            current_code
                            and str(current_code) == slot.pin_code
                            and current_in_use
                        ):
                            _LOGGER.info(
                                "Slot %s already has correct code and is enabled,"
                                " skipping write",
                                slot_number,
                            )
                            slot.is_synced = True
                            slot.sync_error = None
                            slot.sync_attempts = 0
                            return
                        if (
                            current_code
                            and str(current_code) == slot.pin_code
                            and not current_in_use
                        ):
                            _LOGGER.warning(
                                "Slot %s has correct code but userIdStatus"
                                " not Enabled - will re-set to fix",
                                slot_number,
                            )
                except Exception as e:
                    _LOGGER.debug(
                        "Could not pre-check code for slot %s,"
                        " proceeding with write: %s",
                        slot_number,
                        e,
                    )

                # Add/update code in Z-Wave lock
                await hass.services.async_call(
                    "zwave_js",
                    "set_lock_usercode",
                    {
                        "entity_id": entity_id,
                        "code_slot": slot_number,
                        "usercode": slot.pin_code,
                    },
                    blocking=True,
                )
                _LOGGER.info(
                    "Added code to Z-Wave lock %s slot %s", entity_id, slot_number
                )

            elif action == "disable":
                # Remove code from Z-Wave lock
                await hass.services.async_call(
                    "zwave_js",
                    "clear_lock_usercode",
                    {"entity_id": entity_id, "code_slot": slot_number},
                    blocking=True,
                )
                _LOGGER.info(
                    "Removed code from Z-Wave lock %s slot %s", entity_id, slot_number
                )

            elif action == "auto":
                # Automatically determine action based on slot state
                if slot.is_active and slot.pin_code:
                    # Validate PIN code before sending to Z-Wave
                    if not slot.pin_code.isdigit():
                        raise ValueError(
                            f"PIN code must be numeric only: {slot.pin_code}"
                        )
                    if len(slot.pin_code) < 4 or len(slot.pin_code) > 8:
                        raise ValueError(
                            "PIN code must be 4-8 digits"
                            f" (length: {len(slot.pin_code)})"
                        )

                    # Check cached code - use sync get_usercode (fast, no network)
                    try:
                        node = async_get_node_from_entity_id(hass, entity_id)
                        if node:
                            current_code_info = get_usercode(node, slot_number)
                            current_code = (
                                current_code_info.get("usercode")
                                if current_code_info
                                else None
                            )
                            current_in_use = (
                                current_code_info.get("in_use") is True
                                if current_code_info
                                else False
                            )

                            # If code matches AND is enabled, skip the write
                            if (
                                current_code
                                and str(current_code) == slot.pin_code
                                and current_in_use
                            ):
                                _LOGGER.info(
                                    "Slot %s already has correct code and is"
                                    " enabled, skipping write",
                                    slot_number,
                                )
                                slot.is_synced = True
                                slot.sync_error = None
                                slot.sync_attempts = 0
                                return

                            # Code matches but not enabled - log and re-set
                            if (
                                current_code
                                and str(current_code) == slot.pin_code
                                and not current_in_use
                            ):
                                _LOGGER.warning(
                                    "Slot %s has correct code but userIdStatus"
                                    " not Enabled - will re-set to fix",
                                    slot_number,
                                )

                            # If there's a different code in the slot, clear it first
                            if current_code and str(current_code) != slot.pin_code:
                                _LOGGER.info(
                                    "Clearing existing code before setting new"
                                    " one in slot %s",
                                    slot_number,
                                )
                                await hass.services.async_call(
                                    "zwave_js",
                                    "clear_lock_usercode",
                                    {
                                        "entity_id": entity_id,
                                        "code_slot": slot_number,
                                    },
                                    blocking=True,
                                )
                                await asyncio.sleep(1)
                    except Exception as e:
                        _LOGGER.debug(
                            "Could not check existing code, proceeding with set: %s", e
                        )

                    # Should be in lock - add it
                    await hass.services.async_call(
                        "zwave_js",
                        "set_lock_usercode",
                        {
                            "entity_id": entity_id,
                            "code_slot": slot_number,
                            "usercode": slot.pin_code,
                        },
                        blocking=True,
                    )
                    _LOGGER.info(
                        "Auto-added code to Z-Wave lock %s slot %s",
                        entity_id,
                        slot_number,
                    )
                else:
                    # Should not be in lock - remove it
                    await hass.services.async_call(
                        "zwave_js",
                        "clear_lock_usercode",
                        {"entity_id": entity_id, "code_slot": slot_number},
                        blocking=True,
                    )
                    _LOGGER.info(
                        "Auto-removed code from Z-Wave lock %s slot %s",
                        entity_id,
                        slot_number,
                    )

            # Update sync status
            slot.is_synced = True
            slot.sync_error = None
            slot.last_synced = None  # Will be updated by coordinator

        except Exception as e:
            _LOGGER.error(
                "Error syncing slot %s to Z-Wave lock %s: %s", slot_number, entity_id, e
            )
            slot.is_synced = False
            slot.sync_error = str(e)

    @staticmethod
    async def refresh_codes(hass: HomeAssistant, service_call: ServiceCall) -> None:
        """Legacy refresh codes service - triggers Z-Wave code reading.

        Args:
            hass: Home Assistant instance.
            service_call: Service call containing entity_id.
        """
        entity_id = service_call.data[ATTR_ENTITY_ID]

        _LOGGER.info(
            "Legacy refresh_codes called, triggering read_zwave_codes for %s", entity_id
        )

        # Call the new read_zwave_codes service
        await ZWaveServices.read_zwave_codes(hass, service_call)


async def _read_codes_inner(hass: HomeAssistant, entity_id: str) -> None:
    """Inner implementation of read_zwave_codes, separated for timeout wrapping.

    Uses sync get_usercode() to read from the cached ValueDB. This avoids
    querying the Z-Wave mesh which can hang indefinitely for sleeping nodes.

    Args:
        hass: Home Assistant instance.
        entity_id: Lock entity ID to read codes from.
    """
    # async_get_node_from_entity_id is a @callback (sync) - do NOT await
    node = async_get_node_from_entity_id(hass, entity_id)

    # Read codes from all slots using sync get_usercode (cached ValueDB)
    codes_found = {}
    slots_tested = 0
    slots_with_errors = 0

    for slot in range(1, 31):  # Test slots 1-30
        try:
            slots_tested += 1
            # get_usercode is SYNC - reads from cached ValueDB, no network call
            code_info = get_usercode(node, slot)

            if code_info and code_info.get("in_use") is True:
                code_value = code_info.get("usercode")
                if code_value:
                    codes_found[slot] = {
                        "code": str(code_value),
                        "status": "occupied",
                    }
                    _LOGGER.debug("Found code in slot %s", slot)

        except Exception as e:
            slots_with_errors += 1
            _LOGGER.debug("No code in slot %s: %s", slot, e)

    # Fire event with found codes
    hass.bus.async_fire(
        "smart_lock_manager_codes_read",
        {
            "entity_id": entity_id,
            "codes": codes_found,
            "total_found": len(codes_found),
            "slots_tested": slots_tested,
            "slots_with_errors": slots_with_errors,
        },
    )

    _LOGGER.info(
        "Read %s codes from Z-Wave lock %s (scanned %s slots)",
        len(codes_found),
        entity_id,
        slots_tested,
    )
    if codes_found:
        _LOGGER.info("Found codes in slots: %s", list(codes_found.keys()))
