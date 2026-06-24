"""Z-Wave integration services for Smart Lock Manager.

Handles reading and writing user codes to physical Z-Wave locks via the
zwave_js integration. Uses the sync ``get_usercode`` helper (reads from
the cached ValueDB) for fast reads.

Write path: the HA ``set_lock_usercode`` service is the sole write
mechanism. No explicit userIdStatus commands — the lock handles enablement
on its own after receiving the PIN write.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from homeassistant.core import HomeAssistant, ServiceCall

from ..const import ATTR_CODE_SLOT, ATTR_ENTITY_ID, DOMAIN, PRIMARY_LOCK
from ..dev_mock import (
    is_dev_mock,
    mock_get_usercode,
    mock_node_for_entity,
)
from ..models.lock import (
    USER_ID_STATUS_AVAILABLE,
    USER_ID_STATUS_DISABLED,
    USER_ID_STATUS_ENABLED,
    SmartLockManagerLock,
)
from .helpers import find_lock
from .zwave_io import (  # re-exported: external callers import these from here
    _clear_usercode,
    _refresh_slot_cache,
    _set_usercode_with_status,
)


def _reject_for_prefix_collision(
    lock: SmartLockManagerLock, slot_number: int, pin: str
) -> bool:
    """Return True if writing ``pin`` to ``slot_number`` would collide.

    Compares first-N digits against other active slots on this lock and
    tags the target slot's ``sync_error`` plus ``validation_rejections``.
    Used by the auto-sync coordinator path so silent Kwikset drops are caught
    BEFORE the Z-Wave write is dispatched. The slot's ``sync_error`` is
    populated and ``validation_rejections`` is incremented. PIN values are
    never logged — only the prefix is.
    """
    conflict = lock.find_prefix_conflict(pin, slot_number)
    if conflict is None:
        return False
    prefix_len = lock.code_collision_prefix_length
    prefix = pin[:prefix_len]
    message = (
        f"Cannot set slot {slot_number}: PIN starts with same {prefix_len} digits "
        f"({prefix}) as slot {conflict.slot_number} "
        f"({conflict.user_name or 'unnamed'}). Kwikset locks silently reject "
        f"such writes — pick a PIN with a different first {prefix_len} digits."
    )
    slot = lock.code_slots.get(slot_number)
    if slot is not None:
        slot.sync_error = message
        slot.validation_rejections += 1
        slot.is_synced = False
    _LOGGER.error(
        "Prefix-collision rejection on %s: slot %s vs slot %s (prefix=%s)",
        lock.lock_name,
        slot_number,
        conflict.slot_number,
        prefix,
    )
    return True


_LOGGER = logging.getLogger(__name__)

# Timeout for the entire read_zwave_codes operation (seconds)
_READ_CODES_TIMEOUT = 30

# Z-Wave JS integration support
try:
    # async_get_node_from_entity_id is a @callback (sync), NOT a coroutine
    from homeassistant.components.zwave_js.helpers import async_get_node_from_entity_id

    # get_usercode is sync (reads from cached ValueDB) - safe and fast
    # get_usercode_from_node is async (queries the device) - populates cache
    from zwave_js_server.util.lock import (
        get_usercode,
    )

    ZWAVE_JS_AVAILABLE = True
except (ModuleNotFoundError, ImportError):
    ZWAVE_JS_AVAILABLE = False


def _resolve_node(hass: HomeAssistant, entity_id: str) -> Any:
    """Return the Z-Wave node for ``entity_id`` (mock-aware).

    - Description: Under ``SLM_DEV_MOCK`` resolve to a fake node via
      ``mock_node_for_entity``; otherwise call the real
      ``async_get_node_from_entity_id``.
    - Inputs: hass (HomeAssistant), entity_id (str).
    - Outputs: a node-like object (real or mock), or None.
    """
    if is_dev_mock():
        return mock_node_for_entity(entity_id)
    return async_get_node_from_entity_id(hass, entity_id)


def _read_usercode(node: Any, slot: int) -> Any:
    """Read a cached usercode for ``slot`` on ``node`` (mock-aware).

    - Description: Under ``SLM_DEV_MOCK`` read from the MockValueDB via
      ``mock_get_usercode``; otherwise call the real sync ``get_usercode``.
    - Inputs: node (node-like object), slot (int).
    - Outputs: dict with ``usercode``/``in_use`` keys (same shape both paths).
    """
    if is_dev_mock():
        return mock_get_usercode(node, slot)
    return get_usercode(node, slot)


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

        if not ZWAVE_JS_AVAILABLE and not is_dev_mock():
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

        if not ZWAVE_JS_AVAILABLE and not is_dev_mock():
            _LOGGER.error("Z-Wave JS is not available for syncing codes")
            return

        # Find the lock object
        result = find_lock(hass, entity_id)
        if result is None:
            _LOGGER.error("No lock found for entity_id: %s", entity_id)
            return
        lock = result[0]

        slot = lock.code_slots.get(slot_number)
        if not slot:
            _LOGGER.error("No slot %s found in lock %s", slot_number, lock.lock_name)
            return

        # Get Z-Wave node once for cache refresh operations
        try:
            node = _resolve_node(hass, entity_id)
        except Exception as e:
            _LOGGER.debug("Could not get Z-Wave node for %s: %s", entity_id, e)
            node = None

        try:
            if action == "enable" and slot.is_active and slot.pin_code:
                # Validate PIN code before sending to Z-Wave
                if not slot.pin_code.isdigit():
                    raise ValueError(
                        "PIN code must be numeric only"
                        f" (length: {len(slot.pin_code)})"
                    )
                if len(slot.pin_code) < 4 or len(slot.pin_code) > 8:
                    raise ValueError(
                        f"PIN code must be 4-8 digits (length: {len(slot.pin_code)})"
                    )

                # Kwikset prefix-collision guard: bail before Z-Wave write
                if _reject_for_prefix_collision(lock, slot_number, slot.pin_code):
                    return

                # Check cached code to avoid unnecessary writes
                try:
                    if node:
                        current_code_info = _read_usercode(node, slot_number)
                        current_code = (
                            current_code_info.get("usercode")
                            if current_code_info
                            else None
                        )
                        # Code matches = synced, regardless of in_use status
                        if current_code and str(current_code) == slot.pin_code:
                            _LOGGER.info(
                                "Slot %s already has correct code, skipping write",
                                slot_number,
                            )
                            slot.is_synced = True
                            slot.sync_error = None
                            slot.sync_attempts = 0
                            return

                        # If there's a different code in the slot, clear first
                        if current_code and str(current_code) != slot.pin_code:
                            _LOGGER.info(
                                "Clearing existing code from slot %s before"
                                " writing new code",
                                slot_number,
                            )
                            await _clear_usercode(hass, entity_id, slot_number)
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
                await _clear_usercode(hass, entity_id, slot_number)
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
                            "PIN code must be numeric only"
                            f" (length: {len(slot.pin_code)})"
                        )
                    if len(slot.pin_code) < 4 or len(slot.pin_code) > 8:
                        raise ValueError(
                            "PIN code must be 4-8 digits"
                            f" (length: {len(slot.pin_code)})"
                        )

                    # Kwikset prefix-collision guard: bail before Z-Wave write
                    if _reject_for_prefix_collision(lock, slot_number, slot.pin_code):
                        return

                    # Check cached code - use sync get_usercode (fast, no network)
                    try:
                        if node:
                            current_code_info = _read_usercode(node, slot_number)
                            current_code = (
                                current_code_info.get("usercode")
                                if current_code_info
                                else None
                            )

                            # Code matches = synced, regardless of in_use
                            if current_code and str(current_code) == slot.pin_code:
                                _LOGGER.info(
                                    "Slot %s already has correct code,"
                                    " skipping write",
                                    slot_number,
                                )
                                slot.is_synced = True
                                slot.sync_error = None
                                slot.sync_attempts = 0
                                return

                            # If there's a different code in the slot, clear it first
                            if current_code and str(current_code) != slot.pin_code:
                                _LOGGER.info(
                                    "Clearing existing code from slot %s before"
                                    " writing new code",
                                    slot_number,
                                )
                                await _clear_usercode(hass, entity_id, slot_number)
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
                    await _clear_usercode(hass, entity_id, slot_number)
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
    node = _resolve_node(hass, entity_id)

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
            code_info = _read_usercode(node, slot_num)

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


__all__ = [
    "ZWaveServices",
    "_clear_usercode",
    "_refresh_slot_cache",
    "_set_usercode_with_status",
]
