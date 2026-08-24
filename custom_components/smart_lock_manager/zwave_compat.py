"""Single guarded import point for the optional ``zwave_js`` dependency.

Both the coordinator's read helper (``coordinator_helpers.read_zwave_codes``)
and the Z-Wave service handlers (``services.zwave_services``) need the same two
upstream symbols: ``async_get_node_from_entity_id`` (a sync ``@callback`` from
the ``zwave_js`` integration) and ``get_usercode`` (the sync cached-ValueDB
reader from ``zwave_js_server``).

Historically each module imported them independently. ``zwave_services`` wrapped
its import in a ``try`` and set ``ZWAVE_JS_AVAILABLE``; ``coordinator_helpers``
imported the same names with no equivalent guard, so a future upstream symbol
move would throw a full traceback on every 30s coordinator cycle there while the
service path degraded cleanly. Centralising the import here means the attempt
happens in exactly ONE place and every caller reads the same
``ZWAVE_JS_AVAILABLE`` flag and re-exported symbols.

When ``zwave_js`` is not installed the symbols are ``None`` and
``ZWAVE_JS_AVAILABLE`` is ``False``; callers must gate on the flag before using
them (the dev-mock path never touches these).
"""

from __future__ import annotations

try:
    # async_get_node_from_entity_id is a @callback (sync), NOT a coroutine.
    from homeassistant.components.zwave_js.helpers import (
        async_get_node_from_entity_id,
    )

    # get_usercode is sync (reads the cached ValueDB) - fast and non-blocking.
    from zwave_js_server.util.lock import get_usercode

    ZWAVE_JS_AVAILABLE = True
except (ModuleNotFoundError, ImportError):
    async_get_node_from_entity_id = None
    get_usercode = None
    ZWAVE_JS_AVAILABLE = False

__all__ = [
    "ZWAVE_JS_AVAILABLE",
    "async_get_node_from_entity_id",
    "get_usercode",
]
