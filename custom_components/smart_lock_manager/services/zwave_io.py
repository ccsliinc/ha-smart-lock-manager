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

from homeassistant.core import HomeAssistant

from ..dev_mock import MOCK_DB, is_dev_mock, mock_node_for_entity

_LOGGER = logging.getLogger(__name__)


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
    - Outputs: None.
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
    await hass.services.async_call(
        "zwave_js",
        "clear_lock_usercode",
        {"entity_id": entity_id, "code_slot": code_slot},
        blocking=True,
    )
