"""Z-Wave integration services for Smart Lock Manager.

Handles reading and writing user codes to physical Z-Wave locks via the
zwave_js integration. Uses the sync ``get_usercode`` helper (reads from
the cached ValueDB) for fast reads, with ``get_usercode_from_node``
(async, queries the physical device) as a fallback to populate the cache
after writes or when the cache is empty for slots that should have codes.

Write path: the HA ``set_lock_usercode`` service is used as the primary
mechanism to write the PIN value.  After the write, ``userIdStatus`` is
explicitly set to Enabled via ``node.async_set_value()`` to ensure the
slot is active on Kwikset 918 and similar locks.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from homeassistant.core import HomeAssistant, ServiceCall

from ..const import ATTR_CODE_SLOT, ATTR_ENTITY_ID, DOMAIN, PRIMARY_LOCK
from ..models.lock import (
    USER_ID_STATUS_AVAILABLE,
    USER_ID_STATUS_DISABLED,
    USER_ID_STATUS_ENABLED,
)

_LOGGER = logging.getLogger(__name__)

# Timeout for the entire read_zwave_codes operation (seconds)
_READ_CODES_TIMEOUT = 30

# Z-Wave JS integration support
try:
    # async_get_node_from_entity_id is a @callback (sync), NOT a coroutine
    from homeassistant.components.zwave_js.helpers import async_get_node_from_entity_id

    # get_usercode is sync (reads from cached ValueDB) - safe and fast
    # get_usercode_from_node is async (queries the device) - populates cache
    from zwave_js_server.const.command_class.lock import (
        LOCK_USERCODE_STATUS_PROPERTY,
        CodeSlotStatus,
    )
    from zwave_js_server.util.lock import (
        get_code_slot_value,
        get_usercode,
        get_usercode_from_node,
    )

    ZWAVE_JS_AVAILABLE = True
except (ModuleNotFoundError, ImportError):
    ZWAVE_JS_AVAILABLE = False


async def _set_usercode_with_status(
    hass: HomeAssistant,
    entity_id: str,
    code_slot: int,
    usercode: str,
    node: Any = None,
) -> None:
    """Write a user code via HA service, then explicitly enable userIdStatus.

    Uses the HA ``set_lock_usercode`` service as the primary write path —
    proven reliable on Kwikset 918 locks.  After the write completes, the
    userIdStatus value is explicitly set to Enabled via ``node.async_set_value()``
    to ensure the slot is active on locks that do not auto-enable on PIN write.

    Args:
        hass: Home Assistant instance.
        entity_id: Lock entity ID.
        code_slot: Slot number to program.
        usercode: PIN code string (numeric, 4-8 digits).
        node: Z-Wave JS node object (optional, used to set userIdStatus after write).
    """
    await hass.services.async_call(
        "zwave_js",
        "set_lock_usercode",
        {"entity_id": entity_id, "code_slot": code_slot, "usercode": usercode},
        blocking=True,
    )
    _LOGGER.info(
        "set_lock_usercode service call succeeded for slot %s on %s",
        code_slot,
        entity_id,
    )

    # Explicitly set userIdStatus=Enabled after the PIN write
    if node:
        try:
            await asyncio.sleep(2)
            status_value = get_code_slot_value(
                node, code_slot, LOCK_USERCODE_STATUS_PROPERTY
            )
            await node.async_set_value(status_value, CodeSlotStatus.ENABLED)
            _LOGGER.info(
                "Explicitly set userIdStatus=Enabled for slot %s on %s",
                code_slot,
                entity_id,
            )
        except Exception as err:
            _LOGGER.warning(
                "Could not set userIdStatus for slot %s on %s: %s",
                code_slot,
                entity_id,
                err,
            )


async def _refresh_slot_cache(node: Any, code_slot: int, entity_id: str) -> None:
    """Force-refresh the Z-Wave ValueDB cache for a specific slot by querying the node.

    After writing a code or clearing a slot, the Z-Wave JS cached ValueDB may
    not reflect the new value (especially for slots that were empty during the
    initial Z-Wave interview). This uses get_usercode_from_node() which calls
    node.async_invoke_cc_api(USER_CODE, "get", code_slot) to force the Z-Wave
    driver to query the physical node, populating the cache for subsequent sync reads.

    Args:
        node: Z-Wave JS node object.
        code_slot: The code slot number that was just written.
        entity_id: Lock entity ID (for logging).
    """
    try:
        _LOGGER.debug(
            "Refreshing Z-Wave cache for slot %s on %s via node query",
            code_slot,
            entity_id,
        )
        # Allow Z-Wave network propagation before querying back
        # Kwikset locks need extra time for mesh propagation
        await asyncio.sleep(5)

        result = await get_usercode_from_node(node, code_slot)
        in_use = result.get("in_use") if result else None
        _LOGGER.debug(
            "Z-Wave cache refreshed for slot %s on %s: in_use=%s",
            code_slot,
            entity_id,
            in_use,
        )
        if result and not result.get("in_use"):
            _LOGGER.warning(
                "Slot %s on %s still shows in_use=False after write+enable; "
                "the lock may need more time to update its cache",
                code_slot,
                entity_id,
            )
    except Exception as err:
        _LOGGER.warning(
            "Could not refresh Z-Wave cache for slot %s on %s: %s",
            code_slot,
            entity_id,
            err,
        )


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

        # Get Z-Wave node once for cache refresh operations
        try:
            node = async_get_node_from_entity_id(hass, entity_id)
        except Exception as e:
            _LOGGER.debug("Could not get Z-Wave node for %s: %s", entity_id, e)
            node = None

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
                            and slot.user_id_status == USER_ID_STATUS_ENABLED
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
                            and (
                                not current_in_use
                                or slot.user_id_status != USER_ID_STATUS_ENABLED
                            )
                        ):
                            _LOGGER.warning(
                                "Code matches but not enabled in lock"
                                " — re-sending to enable for slot %s on %s",
                                slot_number,
                                entity_id,
                            )

                        # If there's a different code in the slot, clear first
                        if current_code and str(current_code) != slot.pin_code:
                            _LOGGER.info(
                                "Clearing existing code from slot %s before"
                                " writing new code",
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
                            await asyncio.sleep(2)
                except Exception as e:
                    _LOGGER.debug(
                        "Could not pre-check code for slot %s,"
                        " proceeding with write: %s",
                        slot_number,
                        e,
                    )

                # Add/update code in Z-Wave lock with userIdStatus=Enabled
                await _set_usercode_with_status(
                    hass, entity_id, slot_number, slot.pin_code, node=node
                )
                slot.user_id_status = USER_ID_STATUS_ENABLED
                _LOGGER.info(
                    "Added code to Z-Wave lock %s slot %s (with status=Enabled)",
                    entity_id,
                    slot_number,
                )
                if node:
                    await _refresh_slot_cache(node, slot_number, entity_id)

            elif action == "disable":
                # Remove code from Z-Wave lock
                await hass.services.async_call(
                    "zwave_js",
                    "clear_lock_usercode",
                    {"entity_id": entity_id, "code_slot": slot_number},
                    blocking=True,
                )
                slot.user_id_status = USER_ID_STATUS_AVAILABLE
                _LOGGER.info(
                    "Removed code from Z-Wave lock %s slot %s", entity_id, slot_number
                )
                if node:
                    await _refresh_slot_cache(node, slot_number, entity_id)

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
                                and slot.user_id_status == USER_ID_STATUS_ENABLED
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
                                and (
                                    not current_in_use
                                    or slot.user_id_status != USER_ID_STATUS_ENABLED
                                )
                            ):
                                _LOGGER.warning(
                                    "Code matches but not enabled in lock"
                                    " — re-sending to enable for slot %s on %s",
                                    slot_number,
                                    entity_id,
                                )

                            # If there's a different code in the slot, clear it first
                            if current_code and str(current_code) != slot.pin_code:
                                _LOGGER.info(
                                    "Clearing existing code from slot %s before"
                                    " writing new code",
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
                                await asyncio.sleep(2)
                    except Exception as e:
                        _LOGGER.debug(
                            "Could not check existing code, proceeding with set: %s", e
                        )

                    # Should be in lock - add it with userIdStatus=Enabled
                    await _set_usercode_with_status(
                        hass, entity_id, slot_number, slot.pin_code, node=node
                    )
                    slot.user_id_status = USER_ID_STATUS_ENABLED
                    _LOGGER.info(
                        "Auto-added code to Z-Wave lock %s slot %s"
                        " (with status=Enabled)",
                        entity_id,
                        slot_number,
                    )
                    if node:
                        await _refresh_slot_cache(node, slot_number, entity_id)
                else:
                    # Should not be in lock - remove it
                    await hass.services.async_call(
                        "zwave_js",
                        "clear_lock_usercode",
                        {"entity_id": entity_id, "code_slot": slot_number},
                        blocking=True,
                    )
                    slot.user_id_status = USER_ID_STATUS_AVAILABLE
                    _LOGGER.info(
                        "Auto-removed code from Z-Wave lock %s slot %s",
                        entity_id,
                        slot_number,
                    )
                    if node:
                        await _refresh_slot_cache(node, slot_number, entity_id)

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
            # Mark as disabled if slot has a code but sync failed
            if slot.pin_code and slot.is_active:
                slot.user_id_status = USER_ID_STATUS_DISABLED

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

    # Find the lock object so we can update user_id_status on CodeSlots
    lock_obj = None
    for entry_id, entry_data in hass.data.get(DOMAIN, {}).items():
        if isinstance(entry_data, dict):
            candidate = entry_data.get(PRIMARY_LOCK)
            if candidate and candidate.lock_entity_id == entity_id:
                lock_obj = candidate
                break

    # Read codes from all slots using sync get_usercode (cached ValueDB).
    # This reads from the Z-Wave JS ValueDB cache, NOT the live device.
    # If a write just occurred and _refresh_zwave_cache hasn't completed yet,
    # values here may be stale. Do NOT switch to async get_usercode_from_node
    # as it queries the mesh and can hang indefinitely on sleeping nodes.
    codes_found = {}
    slots_tested = 0
    slots_with_errors = 0

    for slot_num in range(1, 31):  # Test slots 1-30
        try:
            slots_tested += 1
            # get_usercode is SYNC - reads from cached ValueDB, no network call
            code_info = get_usercode(node, slot_num)

            in_use = code_info.get("in_use") is True if code_info else False
            code_value = code_info.get("usercode") if code_info else None

            if in_use and code_value:
                codes_found[slot_num] = {
                    "code": str(code_value),
                    "status": "occupied",
                }
                _LOGGER.debug("Found code in slot %s", slot_num)

            # Update user_id_status on the CodeSlot if lock object exists
            if lock_obj:
                code_slot = lock_obj.code_slots.get(slot_num)
                if code_slot:
                    if in_use and code_value:
                        code_slot.user_id_status = USER_ID_STATUS_ENABLED
                    elif code_value and not in_use:
                        code_slot.user_id_status = USER_ID_STATUS_DISABLED
                    else:
                        code_slot.user_id_status = USER_ID_STATUS_AVAILABLE

        except Exception as e:
            slots_with_errors += 1
            _LOGGER.debug("No code in slot %s: %s", slot_num, e)

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
