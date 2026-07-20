"""Mock-aware Z-Wave write/clear I/O helpers for Smart Lock Manager.

Split out of ``zwave_services.py`` to keep that module under the 500-line
limit. These helpers perform user-code writes/clears against either the
real ``zwave_js`` service or the in-memory dev MockValueDB. They read NO
module-level names that tests patch on ``zwave_services`` (those helpers
stay in ``zwave_services``), so this module is import-cycle-free.
"""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from ..dev_mock import MOCK_DB, is_dev_mock, mock_node_for_entity

_LOGGER = logging.getLogger(__name__)


def _ensure_entity_available(hass: HomeAssistant, entity_id: str) -> None:
    """Raise unless ``entity_id`` can currently accept a Z-Wave write.

    - Description: ``hass.services.async_call`` does NOT raise when its target
      entity is missing or unavailable. Home Assistant logs a warning
      ("Referenced entities ... are missing or not currently available") and the
      call silently becomes a no-op. Without this guard the caller logged
      "service call succeeded" and marked the slot synced/Enabled for a code
      that never reached the lock. Raising here matches the dev-mock path, so
      ``sync_slot_to_zwave``'s existing handler records ``sync_error`` and leaves
      the slot unsynced instead of reporting a false success.
    - Inputs: hass (HomeAssistant), entity_id (str) — the lock entity.
    - Outputs: None. Raises ``HomeAssistantError`` when the write cannot land.
    - Example: ``_ensure_entity_available(hass, "lock.bathroom")``
    """
    state = hass.states.get(entity_id)
    if state is None:
        raise HomeAssistantError(
            f"Lock entity {entity_id} is not present; Z-Wave write skipped"
        )
    if state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
        raise HomeAssistantError(
            f"Lock entity {entity_id} is {state.state}; Z-Wave write skipped"
        )


async def _set_usercode_with_status(
    hass: HomeAssistant,
    entity_id: str,
    code_slot: int,
    usercode: str,
    node: Any = None,
) -> None:
    """Write a user code to the lock via HA service.

    Simple write — no explicit userIdStatus set, no sleep, no cache refresh.
    The OLD working behavior: just write the code and let the lock handle it.

    Args:
        hass: Home Assistant instance.
        entity_id: Lock entity ID.
        code_slot: Slot number to program.
        usercode: PIN code string (numeric, 4-8 digits).
        node: Z-Wave JS node object (unused, kept for call-site compatibility).
    """
    if is_dev_mock():
        # DEV: write into the in-memory MockValueDB instead of Z-Wave. The
        # MockValueDB raises on an armed fail_next() to simulate supervision
        # failure, which propagates exactly like a real service-call failure.
        mock_node = mock_node_for_entity(entity_id)
        node_id = getattr(mock_node, "node_id", None)
        if node_id is None:
            raise ValueError(f"Dev mock: unknown lock entity {entity_id}")
        MOCK_DB.set_usercode(node_id, code_slot, usercode)
        _LOGGER.info(
            "DEV mock set_lock_usercode succeeded for slot %s on %s",
            code_slot,
            entity_id,
        )
        return
    # An unavailable/missing entity makes the service call a silent no-op, so
    # verify reachability BEFORE claiming the write succeeded.
    _ensure_entity_available(hass, entity_id)
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


async def _refresh_slot_cache(node: Any, code_slot: int, entity_id: str) -> None:
    """Skip cache refresh to reduce Z-Wave mesh traffic.

    The coordinator's 30-second cycle will pick up cache changes naturally.
    Kept as a no-op for call-site compatibility.
    """
    pass


async def _clear_usercode(hass: HomeAssistant, entity_id: str, code_slot: int) -> None:
    """Clear a usercode for ``entity_id``/``code_slot`` (mock-aware).

    - Description: Under ``SLM_DEV_MOCK`` clear from the MockValueDB (honoring
      failure injection); otherwise call the real ``zwave_js.clear_lock_usercode``
      service.
    - Inputs: hass (HomeAssistant), entity_id (str), code_slot (int).
    - Outputs: None. Raises ``HomeAssistantError`` if the lock is unreachable.
    """
    if is_dev_mock():
        mock_node = mock_node_for_entity(entity_id)
        node_id = getattr(mock_node, "node_id", None)
        if node_id is None:
            raise ValueError(f"Dev mock: unknown lock entity {entity_id}")
        MOCK_DB.clear_usercode(node_id, code_slot)
        _LOGGER.info(
            "DEV mock clear_lock_usercode for slot %s on %s", code_slot, entity_id
        )
        return
    # Same silent-no-op hazard as the write path: a clear against an unavailable
    # lock would otherwise be treated as a successful clear.
    _ensure_entity_available(hass, entity_id)
    await hass.services.async_call(
        "zwave_js",
        "clear_lock_usercode",
        {"entity_id": entity_id, "code_slot": code_slot},
        blocking=True,
    )
