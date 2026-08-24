"""Pure helper(s) for ``SmartLockManagerDataUpdateCoordinator``.

Holds the Z-Wave usercode read step extracted verbatim from
``coordinator._async_update_data`` to keep ``coordinator.py`` under the
file-size limit. ``read_zwave_codes`` is a standalone read: it takes the
``hass`` instance and the lock object, reads the first
``QUICK_SCAN_SLOT_COUNT`` usercode slots (dev-mock or real zwave_js path), and
returns the ``zwave_codes`` dict the coordinator's sync logic consumes. It
fires no events and mutates neither ``self`` (it has none) nor the lock object.

When the optional ``zwave_js`` dependency is not importable the read cannot
happen at all. Rather than logging a fresh traceback every 30s (which read as a
per-cycle error), that state is surfaced ONCE as a Home Assistant repair issue
so it is visible in the UI, and cleared automatically when the dependency
becomes importable again. The guarded import itself lives in ``zwave_compat``
(the single place the optional dependency is imported).
"""

import logging
from typing import Any, Dict

from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.entity_registry import (
    async_get as async_get_entity_registry,
)

from .const import DOMAIN, QUICK_SCAN_SLOT_COUNT
from .dev_mock import is_dev_mock, mock_get_usercode, mock_node_for_entity
from .models.lock import SmartLockManagerLock
from .zwave_compat import (
    ZWAVE_JS_AVAILABLE,
    async_get_node_from_entity_id,
    get_usercode,
)

# Same logger name as coordinator.py so log records stay byte-identical to the
# prior inline behavior (``custom_components.smart_lock_manager``).
_LOGGER = logging.getLogger("custom_components.smart_lock_manager")

# Repair-issue id raised in the HA UI when the optional ``zwave_js`` dependency
# cannot be imported, so the resulting empty reads are visible rather than a
# silent false "no codes".
ISSUE_ZWAVE_JS_UNAVAILABLE = "zwave_js_unavailable"


def _flag_zwave_js_unavailable(hass: HomeAssistant) -> None:
    """Surface ``zwave_js`` unavailability as a repair issue.

    - Description: Creates (idempotently) a warning-severity repair issue so a
      missing ``zwave_js`` integration shows up in Settings -> Repairs instead
      of only in the log. ``async_create_issue`` deduplicates on the issue id,
      so calling this on every coordinator cycle raises the issue exactly once.
    - Inputs: hass (HomeAssistant).
    - Outputs: None.
    """
    ir.async_create_issue(
        hass,
        DOMAIN,
        ISSUE_ZWAVE_JS_UNAVAILABLE,
        is_fixable=False,
        severity=ir.IssueSeverity.WARNING,
        translation_key=ISSUE_ZWAVE_JS_UNAVAILABLE,
    )


def _clear_zwave_js_unavailable(hass: HomeAssistant) -> None:
    """Clear the ``zwave_js``-unavailable repair issue.

    - Description: Deletes the repair issue once the dependency is importable
      again. ``async_delete_issue`` is a no-op when the issue does not exist, so
      this is safe to call unconditionally on the healthy path.
    - Inputs: hass (HomeAssistant).
    - Outputs: None.
    """
    ir.async_delete_issue(hass, DOMAIN, ISSUE_ZWAVE_JS_UNAVAILABLE)


def read_zwave_codes(
    hass: HomeAssistant, lock: SmartLockManagerLock
) -> Dict[int, Dict[str, Any]]:
    """Read the first ``QUICK_SCAN_SLOT_COUNT`` usercode slots from the lock.

    - Description: Quick scan of the low slots via the cached ValueDB; returns
      the codes found. Honors the dev-mock path (fake node + MockValueDB) and
      the real zwave_js path (registry lookup + node fetch). Never raises: any
      failure is logged and an empty/partial dict is returned. When ``zwave_js``
      is not importable (and not dev-mock) it raises a repair issue and returns
      an empty dict rather than attempting the read.
    - Inputs: hass (HomeAssistant), lock (SmartLockManagerLock).
    - Outputs: Dict[int, Dict[str, Any]] keyed by slot number, each value
      ``{"code", "in_use", "status"}``.
    """
    zwave_codes: Dict[int, Dict[str, Any]] = {}
    dev_mock = is_dev_mock()

    # THREE-OUTCOME rule: a missing ``zwave_js`` dependency is not "no codes",
    # it is "could not read". Surface it once in the UI (repair issue) instead
    # of logging a fresh traceback every cycle, then short-circuit. The dev-mock
    # path does not use ``zwave_js`` at all, so it is exempt.
    if not ZWAVE_JS_AVAILABLE and not dev_mock:
        _flag_zwave_js_unavailable(hass)
        _LOGGER.debug(
            "Coordinator: zwave_js unavailable; skipping Z-Wave read for %s",
            lock.lock_entity_id,
        )
        return zwave_codes

    # Dependency importable (or a prior outage recovered): clear any stale
    # repair issue so the UI stops nagging.
    if ZWAVE_JS_AVAILABLE:
        _clear_zwave_js_unavailable(hass)

    try:
        ent_reg = async_get_entity_registry(hass)
        entity_entry = ent_reg.async_get(lock.lock_entity_id)

        if dev_mock:
            # DEV: bypass the zwave_js platform guard and read codes
            # from the in-memory MockValueDB via a fake node. The
            # dummy locks are template entities (platform != zwave_js),
            # so the real guard below would skip all reads.
            node = mock_node_for_entity(lock.lock_entity_id)
            if node:
                for slot in range(1, QUICK_SCAN_SLOT_COUNT + 1):
                    try:
                        code_data = mock_get_usercode(node, slot)
                    except Exception as exc:
                        # THREE-OUTCOME: a slot we cannot read is not an empty
                        # slot. Record why we treated it as unreadable so a
                        # can't-read does not silently look like no-code.
                        _LOGGER.debug(
                            "Coordinator: mock usercode read failed for %s"
                            " slot %s: %s",
                            lock.lock_entity_id,
                            slot,
                            exc,
                        )
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
                # Quick scan of the low slots only (performance
                # optimization). Use get_usercode (sync, cached
                # ValueDB) to avoid blocking startup.
                for slot in range(1, QUICK_SCAN_SLOT_COUNT + 1):
                    try:
                        code_data = get_usercode(node, slot)
                    except Exception as exc:
                        # THREE-OUTCOME: a slot we cannot read is not an empty
                        # slot. Log the reason so a can't-read is not mistaken
                        # for no-code by the sync logic downstream.
                        _LOGGER.debug(
                            "Coordinator: get_usercode failed for %s slot %s: %s",
                            lock.lock_entity_id,
                            slot,
                            exc,
                        )
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
            exc_info=True,
        )

    return zwave_codes
