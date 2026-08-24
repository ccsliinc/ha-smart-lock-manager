"""Behavior tests for the Z-Wave read step (``coordinator_helpers.py``).

``read_zwave_codes`` is the coordinator's only window onto what the physical
deadbolt actually holds, so every sync decision downstream is made from its
return value. That makes its THREE-OUTCOME behaviour the thing worth testing:
"the lock has no code here" and "I could not read this slot" must not collapse
into the same empty dict silently, and a missing ``zwave_js`` must surface in the
UI instead of reading as a clean, codeless lock.

Both the dev-mock path (``MockValueDB``) and the real ``zwave_js`` path are
exercised. The real path is driven through the module's OWN imported wrappers
(``async_get_node_from_entity_id`` / ``get_usercode``, re-exported by
``zwave_compat``) rather than upstream ``zwave_js_server`` symbols, so the tests
do not depend on the optional dependency being installed.
"""

from __future__ import annotations

import os
from types import SimpleNamespace
from typing import Any, Dict, Optional
from unittest.mock import patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir

from custom_components.smart_lock_manager.const import DOMAIN, QUICK_SCAN_SLOT_COUNT
from custom_components.smart_lock_manager.coordinator_helpers import (
    ISSUE_ZWAVE_JS_UNAVAILABLE,
    read_zwave_codes,
)
from custom_components.smart_lock_manager.dev_mock import MOCK_DB
from custom_components.smart_lock_manager.models.lock import SmartLockManagerLock

MODULE = "custom_components.smart_lock_manager.coordinator_helpers"

# A dev-mock demo lock: entity -> node_id 10 (see dev_mock.ENTITY_TO_NODE_ID).
DEMO_ENTITY = "lock.demo_front"
DEMO_NODE = 10


@pytest.fixture(autouse=True)
def _reset_mock_db():
    """Reset the process-global MockValueDB around each test."""
    MOCK_DB._codes.clear()
    MOCK_DB.fail_next(False)
    yield
    MOCK_DB._codes.clear()
    MOCK_DB.fail_next(False)


@pytest.fixture
def dev_mock_env():
    """Enable SLM_DEV_MOCK for the duration of a test."""
    prev = os.environ.get("SLM_DEV_MOCK")
    os.environ["SLM_DEV_MOCK"] = "1"
    yield
    if prev is None:
        os.environ.pop("SLM_DEV_MOCK", None)
    else:
        os.environ["SLM_DEV_MOCK"] = prev


def _lock(entity_id: str = DEMO_ENTITY) -> SmartLockManagerLock:
    """Build a 10-slot lock bound to the given entity id."""
    return SmartLockManagerLock(
        lock_name="Demo Front", lock_entity_id=entity_id, slots=10, start_from=1
    )


def _register_lock_entity(hass: HomeAssistant, platform: str) -> str:
    """Register a lock entity under ``platform`` and return its entity id."""
    entry = er.async_get(hass).async_get_or_create(
        "lock", platform, f"uniq_{platform}", suggested_object_id="probe_lock"
    )
    return entry.entity_id


def _issue(hass: HomeAssistant) -> Optional[Any]:
    """Return the zwave_js-unavailable repair issue, or None."""
    return ir.async_get(hass).async_get_issue(DOMAIN, ISSUE_ZWAVE_JS_UNAVAILABLE)


# ---------------------------------------------------------------------------
# THREE-OUTCOME: a missing dependency is "could not read", not "no codes"
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_missing_zwave_js_raises_a_repair_issue(hass: HomeAssistant) -> None:
    """Without zwave_js the read is skipped and the outage is surfaced in the UI.

    Returning ``{}`` alone would be indistinguishable from a lock that genuinely
    holds no codes, which is the exact false-green this issue exists to prevent.
    """
    with patch(f"{MODULE}.ZWAVE_JS_AVAILABLE", False):
        codes = read_zwave_codes(hass, _lock("lock.real_lock"))

    assert codes == {}
    issue = _issue(hass)
    assert issue is not None
    assert issue.severity == ir.IssueSeverity.WARNING


@pytest.mark.asyncio
async def test_repair_issue_is_raised_only_once(hass: HomeAssistant) -> None:
    """Repeated cycles do not stack duplicate repair issues."""
    with patch(f"{MODULE}.ZWAVE_JS_AVAILABLE", False):
        for _ in range(3):
            read_zwave_codes(hass, _lock("lock.real_lock"))

    issues = [
        key
        for key in ir.async_get(hass).issues
        if key == (DOMAIN, ISSUE_ZWAVE_JS_UNAVAILABLE)
    ]
    assert len(issues) == 1


@pytest.mark.asyncio
async def test_recovered_dependency_clears_the_repair_issue(
    hass: HomeAssistant,
) -> None:
    """When zwave_js becomes importable again the UI stops nagging."""
    with patch(f"{MODULE}.ZWAVE_JS_AVAILABLE", False):
        read_zwave_codes(hass, _lock("lock.real_lock"))
    assert _issue(hass) is not None

    with patch(f"{MODULE}.ZWAVE_JS_AVAILABLE", True):
        read_zwave_codes(hass, _lock("lock.real_lock"))

    assert _issue(hass) is None


@pytest.mark.asyncio
async def test_dev_mock_does_not_need_zwave_js(
    hass: HomeAssistant, dev_mock_env
) -> None:
    """The dev-mock path never touches zwave_js, so it raises no repair issue."""
    MOCK_DB.set_usercode(DEMO_NODE, 1, "1111")

    with patch(f"{MODULE}.ZWAVE_JS_AVAILABLE", False):
        codes = read_zwave_codes(hass, _lock())

    assert codes[1]["code"] == "1111"
    assert _issue(hass) is None


# ---------------------------------------------------------------------------
# Dev-mock read path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dev_mock_read_reports_occupied_slots(
    hass: HomeAssistant, dev_mock_env
) -> None:
    """Codes present on the mock deadbolt come back with in_use/status set."""
    MOCK_DB.set_usercode(DEMO_NODE, 1, "1111")
    MOCK_DB.set_usercode(DEMO_NODE, 3, "3333")

    codes = read_zwave_codes(hass, _lock())

    assert sorted(codes) == [1, 3]
    assert codes[1] == {"code": "1111", "in_use": True, "status": "occupied"}


@pytest.mark.asyncio
async def test_dev_mock_read_of_empty_lock_is_empty(
    hass: HomeAssistant, dev_mock_env
) -> None:
    """An empty mock deadbolt reads as no codes (the genuine empty case)."""
    assert read_zwave_codes(hass, _lock()) == {}


@pytest.mark.asyncio
async def test_dev_mock_unknown_entity_reads_nothing(
    hass: HomeAssistant, dev_mock_env
) -> None:
    """An entity with no mock node resolves to no node and reads nothing."""
    assert read_zwave_codes(hass, _lock("lock.not_a_demo_lock")) == {}


@pytest.mark.asyncio
async def test_dev_mock_slot_read_error_is_not_a_missing_code(
    hass: HomeAssistant, dev_mock_env
) -> None:
    """An unreadable slot is skipped, and does not abort the rest of the scan.

    Slot 1 raises; slot 2 still reports its code. A read error must not be able
    to silently blank the whole scan and make live codes look absent.
    """
    MOCK_DB.set_usercode(DEMO_NODE, 2, "2222")

    def _flaky(node: Any, slot: int) -> Dict[str, Any]:
        if slot == 1:
            raise RuntimeError("mock read failure")
        return {"usercode": MOCK_DB.get_usercode(node.node_id, slot), "in_use": True}

    with patch(f"{MODULE}.mock_get_usercode", side_effect=_flaky):
        codes = read_zwave_codes(hass, _lock())

    assert 1 not in codes
    assert codes[2]["code"] == "2222"


# ---------------------------------------------------------------------------
# Real zwave_js read path: node resolution and per-slot reads
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_entity_missing_from_registry_reads_nothing(
    hass: HomeAssistant, caplog
) -> None:
    """An unregistered entity cannot be resolved to a node; say so in the log."""
    with patch(f"{MODULE}.ZWAVE_JS_AVAILABLE", True):
        codes = read_zwave_codes(hass, _lock("lock.never_registered"))

    assert codes == {}
    assert "not in registry" in caplog.text


@pytest.mark.asyncio
async def test_non_zwave_platform_reads_nothing(hass: HomeAssistant, caplog) -> None:
    """A template/other-platform lock is not a Z-Wave lock; skip it loudly."""
    entity_id = _register_lock_entity(hass, "template")

    with patch(f"{MODULE}.ZWAVE_JS_AVAILABLE", True):
        codes = read_zwave_codes(hass, _lock(entity_id))

    assert codes == {}
    assert "not zwave_js" in caplog.text


@pytest.mark.asyncio
async def test_node_resolution_failure_reads_nothing(
    hass: HomeAssistant, caplog
) -> None:
    """A node lookup that raises is contained: empty result, warning logged."""
    entity_id = _register_lock_entity(hass, "zwave_js")

    with (
        patch(f"{MODULE}.ZWAVE_JS_AVAILABLE", True),
        patch(
            f"{MODULE}.async_get_node_from_entity_id",
            side_effect=RuntimeError("driver not ready"),
        ),
    ):
        codes = read_zwave_codes(hass, _lock(entity_id))

    assert codes == {}
    assert "failed to get Z-Wave node" in caplog.text


@pytest.mark.asyncio
async def test_absent_node_reads_nothing(hass: HomeAssistant) -> None:
    """A registry entry with no live node yields no codes rather than raising."""
    entity_id = _register_lock_entity(hass, "zwave_js")

    with (
        patch(f"{MODULE}.ZWAVE_JS_AVAILABLE", True),
        patch(f"{MODULE}.async_get_node_from_entity_id", return_value=None),
    ):
        codes = read_zwave_codes(hass, _lock(entity_id))

    assert codes == {}


@pytest.mark.asyncio
async def test_real_path_maps_in_use_to_status(hass: HomeAssistant) -> None:
    """A code present but not in use is reported 'disabled', not 'occupied'.

    That distinction is what tells the sync layer a PIN is still physically on
    the deadbolt even though the lock has it switched off.
    """
    entity_id = _register_lock_entity(hass, "zwave_js")
    stored = {
        1: {"usercode": "1111", "in_use": True},
        2: {"usercode": "2222", "in_use": False},
        3: {"usercode": None, "in_use": False},
    }

    with (
        patch(f"{MODULE}.ZWAVE_JS_AVAILABLE", True),
        patch(
            f"{MODULE}.async_get_node_from_entity_id",
            return_value=SimpleNamespace(node_id=7),
        ),
        patch(
            f"{MODULE}.get_usercode", side_effect=lambda node, slot: stored.get(slot)
        ),
    ):
        codes = read_zwave_codes(hass, _lock(entity_id))

    assert codes[1] == {"code": "1111", "in_use": True, "status": "occupied"}
    assert codes[2] == {"code": "2222", "in_use": False, "status": "disabled"}
    assert 3 not in codes


@pytest.mark.asyncio
async def test_real_path_scans_exactly_the_quick_scan_range(
    hass: HomeAssistant,
) -> None:
    """The quick scan covers slots 1..QUICK_SCAN_SLOT_COUNT and stops there."""
    entity_id = _register_lock_entity(hass, "zwave_js")
    seen = []

    def _record(node: Any, slot: int) -> None:
        seen.append(slot)
        return None

    with (
        patch(f"{MODULE}.ZWAVE_JS_AVAILABLE", True),
        patch(
            f"{MODULE}.async_get_node_from_entity_id",
            return_value=SimpleNamespace(node_id=7),
        ),
        patch(f"{MODULE}.get_usercode", side_effect=_record),
    ):
        read_zwave_codes(hass, _lock(entity_id))

    assert seen == list(range(1, QUICK_SCAN_SLOT_COUNT + 1))


@pytest.mark.asyncio
async def test_real_path_slot_error_does_not_abort_the_scan(
    hass: HomeAssistant, caplog
) -> None:
    """One unreadable slot is logged and skipped; later slots still report."""
    entity_id = _register_lock_entity(hass, "zwave_js")

    def _flaky(node: Any, slot: int):
        if slot == 1:
            raise RuntimeError("value db timeout")
        if slot == 2:
            return {"usercode": "2222", "in_use": True}
        return None

    with (
        patch(f"{MODULE}.ZWAVE_JS_AVAILABLE", True),
        patch(
            f"{MODULE}.async_get_node_from_entity_id",
            return_value=SimpleNamespace(node_id=7),
        ),
        patch(f"{MODULE}.get_usercode", side_effect=_flaky),
    ):
        codes = read_zwave_codes(hass, _lock(entity_id))

    assert 1 not in codes
    assert codes[2]["code"] == "2222"
    assert "get_usercode failed" in caplog.text


@pytest.mark.asyncio
async def test_diagnostic_read_for_a_slot_slm_has_a_pin_for(
    hass: HomeAssistant, caplog
) -> None:
    """A slot SLM holds a PIN for gets its raw read logged, PIN-free.

    The diagnostic must never print the code itself -- only whether one was
    returned at all.
    """
    entity_id = _register_lock_entity(hass, "zwave_js")
    lock = _lock(entity_id)
    lock.set_code(1, "1111", "Alice")

    with (
        patch(f"{MODULE}.ZWAVE_JS_AVAILABLE", True),
        patch(
            f"{MODULE}.async_get_node_from_entity_id",
            return_value=SimpleNamespace(node_id=7),
        ),
        patch(
            f"{MODULE}.get_usercode",
            side_effect=lambda node, slot: (
                {"usercode": None, "in_use": False} if slot == 1 else None
            ),
        ),
    ):
        codes = read_zwave_codes(hass, lock)

    assert codes == {}
    assert "raw get_usercode slot 1: usercode=MISSING" in caplog.text
    assert "1111" not in caplog.text


@pytest.mark.asyncio
async def test_registry_failure_is_contained(hass: HomeAssistant, caplog) -> None:
    """A failure before the scan even starts returns empty and warns."""
    with (
        patch(f"{MODULE}.ZWAVE_JS_AVAILABLE", True),
        patch(
            f"{MODULE}.async_get_entity_registry",
            side_effect=RuntimeError("registry unavailable"),
        ),
    ):
        codes = read_zwave_codes(hass, _lock("lock.real_lock"))

    assert codes == {}
    assert "Z-Wave code reading failed" in caplog.text
