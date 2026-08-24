"""Tests for the 2026.8.4 P2 cleanup pass.

Covers:
- the deprecated ``generate_package`` service is no longer registered;
- ``zwave_js`` unavailability raises a repair issue (and clears it when the
  dependency becomes importable again);
- domain services and the zones HTTP view register exactly once per HA run even
  when multiple config entries (locks) are set up.
"""

from unittest.mock import AsyncMock, Mock, patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.service import (
    async_register_admin_service as _real_admin_service,
)

from custom_components.smart_lock_manager import coordinator_helpers
from custom_components.smart_lock_manager.api import http
from custom_components.smart_lock_manager.const import DOMAIN
from custom_components.smart_lock_manager.coordinator_helpers import (
    ISSUE_ZWAVE_JS_UNAVAILABLE,
)
from custom_components.smart_lock_manager.models.lock import SmartLockManagerLock
from custom_components.smart_lock_manager.services import registration


@pytest.mark.asyncio
async def test_generate_package_service_not_registered(hass: HomeAssistant) -> None:
    """The deprecated ``generate_package`` no-op must not be registered.

    It was dead surface (a warn + fire-event no-op) that still appeared in the
    HA service picker. After removal it must be absent while the other core
    services remain present.
    """
    await registration.async_register_services(hass)

    assert not hass.services.has_service(DOMAIN, "generate_package")
    # Sanity: a real core service is still registered, so the assertion above is
    # meaningful and not just "nothing registered".
    assert hass.services.has_service(DOMAIN, "set_code")


@pytest.mark.asyncio
async def test_services_registered_once_across_two_setups(
    hass: HomeAssistant,
) -> None:
    """A second config-entry setup must not re-register domain services.

    ``async_register_services`` runs on every entry setup (once per lock) but
    domain services are process-wide. The once-per-run guard means the second
    call registers nothing.
    """
    with patch.object(
        registration,
        "async_register_admin_service",
        side_effect=_real_admin_service,
    ) as spy:
        await registration.async_register_services(hass)
        first_count = spy.call_count
        assert first_count > 0

        # Second "entry" setup: guard should short-circuit, registering nothing.
        await registration.async_register_services(hass)
        assert spy.call_count == first_count

    runtime = hass.data[f"{DOMAIN}_runtime"]
    assert runtime.get("services_registered") is True


@pytest.mark.asyncio
async def test_services_reregister_after_full_unregister(
    hass: HomeAssistant,
) -> None:
    """After the last entry unloads, the guard clears so re-setup registers.

    ``async_unregister_services`` (with ``hass.data[DOMAIN]`` empty) clears the
    ``services_registered`` flag, so a subsequent ``async_register_services``
    registers the full set again.
    """
    hass.data.setdefault(DOMAIN, {})
    await registration.async_register_services(hass)
    assert hass.data[f"{DOMAIN}_runtime"].get("services_registered") is True

    # Simulate the last entry being gone.
    hass.data[DOMAIN] = {}
    await registration.async_unregister_services(hass)
    assert hass.data[f"{DOMAIN}_runtime"].get("services_registered") is None
    assert not hass.services.has_service(DOMAIN, "set_code")

    # A fresh setup re-registers.
    await registration.async_register_services(hass)
    assert hass.services.has_service(DOMAIN, "set_code")


@pytest.mark.asyncio
async def test_http_zones_view_registered_once() -> None:
    """The zones HTTP view registers exactly once across two setups."""
    hass = Mock()
    hass.data = {}
    hass.http = Mock()
    hass.http.async_register_static_paths = AsyncMock()
    hass.http.register_view = Mock()

    await http.async_register_http_views(hass)
    await http.async_register_http_views(hass)

    assert hass.http.register_view.call_count == 1
    assert hass.http.async_register_static_paths.call_count == 1
    runtime = hass.data[f"{DOMAIN}_runtime"]
    assert runtime.get("zones_view_registered") is True
    assert runtime.get("static_path_registered") is True


@pytest.mark.asyncio
async def test_repair_issue_created_when_zwave_unavailable(
    hass: HomeAssistant,
) -> None:
    """Missing zwave_js surfaces as a repair issue and returns no codes."""
    lock = SmartLockManagerLock(lock_name="Test Lock", lock_entity_id="lock.test_lock")

    with (
        patch.object(coordinator_helpers, "ZWAVE_JS_AVAILABLE", False),
        patch.object(coordinator_helpers, "is_dev_mock", return_value=False),
    ):
        result = coordinator_helpers.read_zwave_codes(hass, lock)

    assert result == {}
    issues = ir.async_get(hass).issues
    assert (DOMAIN, ISSUE_ZWAVE_JS_UNAVAILABLE) in issues


@pytest.mark.asyncio
async def test_repair_issue_cleared_when_zwave_available(
    hass: HomeAssistant,
) -> None:
    """Once zwave_js imports again, a prior repair issue is deleted."""
    lock = SmartLockManagerLock(lock_name="Test Lock", lock_entity_id="lock.test_lock")

    # Seed the issue as if a prior unavailable cycle had raised it.
    ir.async_create_issue(
        hass,
        DOMAIN,
        ISSUE_ZWAVE_JS_UNAVAILABLE,
        is_fixable=False,
        severity=ir.IssueSeverity.WARNING,
        translation_key=ISSUE_ZWAVE_JS_UNAVAILABLE,
    )
    assert (DOMAIN, ISSUE_ZWAVE_JS_UNAVAILABLE) in ir.async_get(hass).issues

    # zwave_js importable (default in the test venv). The lock entity is not in
    # the registry, so the read returns empty, but the issue must be cleared.
    with (
        patch.object(coordinator_helpers, "ZWAVE_JS_AVAILABLE", True),
        patch.object(coordinator_helpers, "is_dev_mock", return_value=False),
    ):
        result = coordinator_helpers.read_zwave_codes(hass, lock)

    assert result == {}
    assert (
        DOMAIN,
        ISSUE_ZWAVE_JS_UNAVAILABLE,
    ) not in ir.async_get(hass).issues
