"""Tests for the config-entry startup-ordering gate (ConfigEntryNotReady).

Covers ``entry_setup.async_lock_not_ready_reason`` and the
``async_setup_entry`` call site that turns its verdict into a
``ConfigEntryNotReady`` raise.

Regression cover for public issues #2 / #4: on a Home Assistant restart this
integration could be set up before ``zwave_js`` had created the backing lock
entity, leaving a half-configured entry that surfaced as a confusing
"Failed to load services.yaml" error instead of HA's normal retry-with-backoff.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest
from homeassistant.exceptions import ConfigEntryNotReady

from custom_components.smart_lock_manager import async_setup_entry
from custom_components.smart_lock_manager.entry_setup import (
    async_lock_not_ready_reason,
)

LOCK_ENTITY = "lock.front_door"


def _make_hass(
    *,
    state=None,
    in_registry: bool = False,
    zwave_configured: bool = False,
    zwave_loaded: bool = False,
):
    """Build a minimal hass double shaped for the readiness gate.

    - Inputs: state (a state object or None), in_registry (bool),
      zwave_configured (bool), zwave_loaded (bool).
    - Outputs: a Mock exposing ``states``, ``config.components`` and
      ``config_entries.async_entries``.
    """
    hass = Mock()
    hass.states = Mock()
    hass.states.get = Mock(return_value=state)
    hass.config = SimpleNamespace(
        components={"zwave_js"} if zwave_loaded else set(),
    )
    hass.config_entries = Mock()
    hass.config_entries.async_entries = Mock(
        return_value=[Mock()] if zwave_configured else []
    )
    hass.data = {}

    registry = Mock()
    registry.async_get = Mock(return_value=Mock() if in_registry else None)
    return hass, registry


def _make_entry(entity_id: str = LOCK_ENTITY):
    """Return a config-entry double carrying the lock entity id."""
    entry = Mock()
    entry.entry_id = "entry_readiness"
    entry.data = {
        "lock_entity_id": entity_id,
        "lock_name": "Front Door",
        "slots": 10,
        "start_from": 1,
    }
    return entry


def _patch_registry(registry):
    """Patch the entity-registry accessor the gate uses."""
    return patch(
        "custom_components.smart_lock_manager.entry_setup." "async_get_entity_registry",
        return_value=registry,
    )


# --------------------------------------------------------------------------
# NOT READY - the startup race (must retry)
# --------------------------------------------------------------------------


def test_not_ready_when_registered_but_stateless():
    """A registry-known entity with no state yet is a resolving race."""
    hass, registry = _make_hass(state=None, in_registry=True)
    entry = _make_entry()

    with _patch_registry(registry):
        reason = async_lock_not_ready_reason(hass, entry)

    assert reason is not None
    assert LOCK_ENTITY in reason


def test_not_ready_when_zwave_js_configured_but_not_loaded():
    """No state, not registered, but zwave_js is still booting -> retry."""
    hass, registry = _make_hass(
        state=None, in_registry=False, zwave_configured=True, zwave_loaded=False
    )
    entry = _make_entry()

    with _patch_registry(registry):
        reason = async_lock_not_ready_reason(hass, entry)

    assert reason is not None
    assert LOCK_ENTITY in reason
    assert "zwave_js" in reason


# --------------------------------------------------------------------------
# READY - existence, not online-ness
# --------------------------------------------------------------------------


def test_ready_when_state_present():
    """A present state means the owning integration is up. Proceed."""
    hass, registry = _make_hass(state=Mock(state="locked"), in_registry=True)
    entry = _make_entry()

    with _patch_registry(registry):
        assert async_lock_not_ready_reason(hass, entry) is None


@pytest.mark.parametrize("lock_state", ["unavailable", "unknown"])
def test_ready_when_lock_is_offline(lock_state):
    """An OFFLINE lock must NOT trip the gate.

    ``unavailable`` / ``unknown`` may never resolve (dead batteries, out of
    range). Retrying forever would leave the user with no panel at all.
    """
    hass, registry = _make_hass(state=Mock(state=lock_state), in_registry=True)
    entry = _make_entry()

    with _patch_registry(registry):
        assert async_lock_not_ready_reason(hass, entry) is None


def test_ready_when_entity_unknown_and_nothing_pending():
    """A deleted/renamed entity is a config problem, not a race. Proceed."""
    hass, registry = _make_hass(
        state=None, in_registry=False, zwave_configured=True, zwave_loaded=True
    )
    entry = _make_entry()

    with _patch_registry(registry):
        assert async_lock_not_ready_reason(hass, entry) is None


def test_ready_when_no_lock_entity_configured():
    """An entry with no lock entity has nothing to wait on."""
    hass, registry = _make_hass(state=None, in_registry=False)
    entry = _make_entry(entity_id="")

    with _patch_registry(registry):
        assert async_lock_not_ready_reason(hass, entry) is None


def test_dev_mock_never_trips_the_gate():
    """Dev-mock locks are template entities; the gate must be exempt."""
    hass, registry = _make_hass(
        state=None, in_registry=True, zwave_configured=True, zwave_loaded=False
    )
    entry = _make_entry()

    with (
        _patch_registry(registry),
        patch(
            "custom_components.smart_lock_manager.entry_setup.is_dev_mock",
            return_value=True,
        ),
    ):
        assert async_lock_not_ready_reason(hass, entry) is None


# --------------------------------------------------------------------------
# async_setup_entry call site
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_setup_entry_raises_config_entry_not_ready():
    """The gate's reason becomes a ConfigEntryNotReady raise."""
    hass = Mock()
    hass.data = {}
    entry = _make_entry()

    with patch(
        "custom_components.smart_lock_manager.async_lock_not_ready_reason",
        return_value="Lock entity lock.front_door is registered but has no state yet",
    ):
        with pytest.raises(ConfigEntryNotReady) as excinfo:
            await async_setup_entry(hass, entry)

    assert LOCK_ENTITY in str(excinfo.value)
    # Nothing was half-configured: the gate runs before any hass.data writes.
    assert hass.data == {}


@pytest.mark.asyncio
async def test_setup_entry_proceeds_once_ready():
    """With the gate clear, setup runs through to completion."""
    hass = Mock()
    hass.data = {}
    hass.states = Mock()
    hass.states.async_entity_ids = Mock(return_value=[])
    hass.states.get = Mock(return_value=None)
    hass.bus = Mock()
    hass.async_add_executor_job = AsyncMock()
    hass.config_entries = Mock()
    hass.config_entries.async_forward_entry_setups = AsyncMock()
    entry = _make_entry()
    entry.async_on_unload = Mock()
    entry.add_update_listener = Mock()

    store = Mock()
    store.async_load = AsyncMock(return_value={})
    coordinator = Mock()
    coordinator.async_config_entry_first_refresh = AsyncMock()

    module = "custom_components.smart_lock_manager"
    with (
        patch(f"{module}.async_lock_not_ready_reason", return_value=None),
        patch(f"{module}.Store", return_value=store),
        patch(
            f"{module}.SmartLockManagerDataUpdateCoordinator", return_value=coordinator
        ),
        patch(f"{module}.async_register_services", AsyncMock()),
        patch(f"{module}.async_ensure_zones_loaded", AsyncMock()),
        patch(f"{module}.async_run_migration_if_needed", AsyncMock()),
        patch(f"{module}.engines_active", return_value=False),
        patch(f"{module}.async_register_http_views", AsyncMock()),
        patch(f"{module}.async_register_panel", AsyncMock()),
    ):
        assert await async_setup_entry(hass, entry) is True

    assert entry.entry_id in hass.data["smart_lock_manager"]
    coordinator.async_config_entry_first_refresh.assert_awaited_once()
