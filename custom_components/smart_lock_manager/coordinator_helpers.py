"""Pure helper(s) for ``SmartLockManagerDataUpdateCoordinator``.

Holds the Z-Wave usercode read step extracted verbatim from
``coordinator._async_update_data`` to keep ``coordinator.py`` under the
file-size limit. ``read_zwave_codes`` is a standalone read: it takes the
``hass`` instance and the lock object, reads the first ten usercode slots
(dev-mock or real zwave_js path), and returns the ``zwave_codes`` dict the
coordinator's sync logic consumes. It fires no events and mutates neither
``self`` (it has none) nor the lock object — behavior is identical to the
prior inline block.
"""

import logging
from typing import Any, Dict

from homeassistant.core import HomeAssistant

from .dev_mock import is_dev_mock, mock_get_usercode, mock_node_for_entity
from .models.lock import SmartLockManagerLock

# Same logger name as coordinator.py so log records stay byte-identical to the
# prior inline behavior (``custom_components.smart_lock_manager``).
_LOGGER = logging.getLogger("custom_components.smart_lock_manager")


def read_zwave_codes(
    hass: HomeAssistant, lock: SmartLockManagerLock
) -> Dict[int, Dict[str, Any]]:
    """Read the first ten usercode slots from the lock's Z-Wave node.

    - Description: Quick scan of slots 1-10 via the cached ValueDB; returns
      the codes found. Honors the dev-mock path (fake node + MockValueDB) and
      the real zwave_js path (registry lookup + node fetch). Never raises:
      any failure is logged and an empty/partial dict is returned, exactly as
      the prior inline block did.
    - Inputs: hass (HomeAssistant), lock (SmartLockManagerLock).
    - Outputs: Dict[int, Dict[str, Any]] keyed by slot number, each value
      ``{"code", "in_use", "status"}``.
    """
    zwave_codes: Dict[int, Dict[str, Any]] = {}
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

        ent_reg = async_get_entity_registry(hass)
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
                            "status": ("occupied" if in_use else "disabled"),
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
                    hass,
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
                            ("MISSING" if _raw_usercode is None else "<set>"),
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
                                "status": ("occupied" if in_use else "disabled"),
                            }
                    except Exception as e:
                        _LOGGER.debug("Could not read Z-Wave slot %s: %s", slot, e)
    except Exception as e:
        _LOGGER.warning(
            "Z-Wave code reading failed for %s: %s",
            lock.lock_entity_id,
            e,
        )
        import traceback

        _LOGGER.warning("Traceback: %s", traceback.format_exc())

    return zwave_codes
