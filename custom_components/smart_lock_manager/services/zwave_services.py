"""Z-Wave integration services for Smart Lock Manager."""

import logging

from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers.entity_registry import async_get as async_get_entity_registry

from ..const import ATTR_CODE_SLOT, ATTR_ENTITY_ID, DOMAIN, PRIMARY_LOCK

_LOGGER = logging.getLogger(__name__)

# Z-Wave JS integration support
try:
    from homeassistant.components.zwave_js.const import DOMAIN as ZWAVE_JS_DOMAIN
    from homeassistant.components.zwave_js.helpers import async_get_node_from_entity_id
    from zwave_js_server.util.lock import get_usercode_from_node

    ZWAVE_JS_AVAILABLE = True
except (ModuleNotFoundError, ImportError):
    ZWAVE_JS_AVAILABLE = False


class ZWaveServices:
    """Service handler for Z-Wave integration operations."""

    @staticmethod
    async def read_zwave_codes(hass: HomeAssistant, service_call: ServiceCall) -> None:
        """Read user codes from the physical Z-Wave lock."""
        entity_id = service_call.data[ATTR_ENTITY_ID]

        if not ZWAVE_JS_AVAILABLE:
            _LOGGER.error("Z-Wave JS is not available for reading codes")
            return

        try:
            # Get the Z-Wave node from the lock entity
            entity_registry = async_get_entity_registry(hass)
            entity_entry = entity_registry.async_get(entity_id)

            if not entity_entry:
                _LOGGER.error("Entity %s not found in registry", entity_id)
                return

            device_id = entity_entry.device_id
            if not device_id:
                _LOGGER.error("No device_id found for entity %s", entity_id)
                return

            # Get device info and Z-Wave node
            from homeassistant.helpers import device_registry as dr

            device_registry = dr.async_get(hass)
            device = device_registry.async_get(device_id)

            if not device:
                _LOGGER.error("Device not found for device_id %s", device_id)
                return

            # Extract Z-Wave node info from device identifiers
            zwave_identifier = None
            for identifier_domain, identifier in device.identifiers:
                if identifier_domain == ZWAVE_JS_DOMAIN:
                    zwave_identifier = identifier
                    break

            if not zwave_identifier:
                raise ValueError(f"No Z-Wave identifier found for device {device_id}")

            # Get config entry for Z-Wave JS
            config_entries = [
                entry
                for entry in hass.config_entries.async_entries(ZWAVE_JS_DOMAIN)
                if entry.state.value == "loaded"
            ]

            if not config_entries:
                raise ValueError("No loaded Z-Wave JS config entries found")

            config_entry = config_entries[0]  # Use first loaded entry

            if config_entry.entry_id not in hass.data.get(ZWAVE_JS_DOMAIN, {}):
                raise ValueError(f"Device {device_id} config entry is not loaded")

            # Get the Z-Wave node
            node = await async_get_node_from_entity_id(hass, entity_id)
            if not node:
                raise ValueError(f"No Z-Wave node found for entity {entity_id}")

            # Read codes from all slots (typically 1-30 for most locks)
            codes_found = {}
            slots_tested = 0
            slots_with_errors = 0

            for slot in range(1, 31):  # Test slots 1-30
                try:
                    slots_tested += 1
                    code_info = get_usercode_from_node(node, slot)

                    if code_info and code_info.get("userIdStatus") == "occupied":
                        code_value = code_info.get("code")
                        if code_value:
                            codes_found[slot] = {
                                "code": str(code_value),
                                "status": code_info.get("userIdStatus", "unknown"),
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

        except Exception as e:
            _LOGGER.error("Error reading Z-Wave codes from %s: %s", entity_id, e)
            import traceback

            _LOGGER.error("Full traceback: %s", traceback.format_exc())

    @staticmethod
    async def sync_slot_to_zwave(
        hass: HomeAssistant, service_call: ServiceCall
    ) -> None:
        """Sync a specific slot to the Z-Wave lock (add or remove code)."""
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
                        f"PIN code must be 4-8 digits: {slot.pin_code} (length: {len(slot.pin_code)})"
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
                            f"PIN code must be 4-8 digits: {slot.pin_code} (length: {len(slot.pin_code)})"
                        )

                    # Get current Z-Wave code to check if we need to clear first
                    from homeassistant.components.zwave_js.helpers import (
                        async_get_node_from_entity_id,
                    )
                    from zwave_js_server.util.lock import get_usercode_from_node

                    try:
                        node = await async_get_node_from_entity_id(hass, entity_id)
                        if node:
                            current_code_info = get_usercode_from_node(
                                node, slot_number
                            )
                            current_code = (
                                current_code_info.get("code")
                                if current_code_info
                                else None
                            )

                            # If there's a different code in the slot, clear it first
                            if current_code and str(current_code) != slot.pin_code:
                                _LOGGER.info(
                                    "Clearing existing code before setting new one in slot %s (old: %s, new: %s)",
                                    slot_number,
                                    current_code,
                                    slot.pin_code,
                                )
                                await hass.services.async_call(
                                    "zwave_js",
                                    "clear_lock_usercode",
                                    {"entity_id": entity_id, "code_slot": slot_number},
                                    blocking=True,
                                )
                                # Small delay to let the clear complete
                                import asyncio

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
        """Legacy refresh codes service - triggers Z-Wave code reading."""
        entity_id = service_call.data[ATTR_ENTITY_ID]

        _LOGGER.info(
            "Legacy refresh_codes called, triggering read_zwave_codes for %s", entity_id
        )

        # Call the new read_zwave_codes service
        await ZWaveServices.read_zwave_codes(hass, service_call)
