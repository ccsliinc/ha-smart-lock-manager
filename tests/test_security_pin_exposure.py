"""Security regression tests for PIN exposure and admin gating.

Covers the HACS review findings that raw door codes must never leave the
backend as broadcast data, and that code-mutating / code-reading services must
be admin-gated so a non-admin (guest) user cannot invoke them.

- Fix 2: the ``smart_lock_manager_codes_read`` event must carry occupancy only,
  never the plaintext PIN.
- Fix 3: code services (e.g. ``set_code``, ``read_zwave_codes``) must be
  registered as admin services and reject non-admin callers.
"""

from unittest.mock import Mock, patch

import pytest
from homeassistant.core import Context, HomeAssistant
from homeassistant.exceptions import Unauthorized

from custom_components.smart_lock_manager.const import DOMAIN
from custom_components.smart_lock_manager.services import registration, zwave_services


@pytest.mark.asyncio
async def test_codes_read_event_carries_no_raw_pin(hass: HomeAssistant) -> None:
    """Fix 2: the codes_read event exposes occupancy only, never the code.

    The read path is driven with a stubbed node so every scanned slot reports
    an occupied code value of "1234". The fired event must describe presence
    (slot number + in_use) without ever including that value.
    """
    events = []
    hass.bus.async_listen("smart_lock_manager_codes_read", lambda e: events.append(e))

    with (
        patch.object(zwave_services, "_resolve_node", return_value=Mock()),
        patch.object(
            zwave_services,
            "_read_usercode",
            return_value={"in_use": True, "usercode": "1234"},
        ),
    ):
        await zwave_services._read_codes_inner(hass, "lock.test_lock")
        await hass.async_block_till_done()

    assert len(events) == 1
    data = events[0].data
    codes = data["codes"]
    assert codes, "expected at least one occupied slot in the fixture"

    for slot_num, entry in codes.items():
        # Occupancy shape only.
        assert entry.get("in_use") is True
        assert entry.get("slot") == slot_num
        # The raw code value must never be present under any key.
        assert "code" not in entry
        assert "usercode" not in entry
        assert "1234" not in [str(v) for v in entry.values()]


@pytest.mark.asyncio
@pytest.mark.parametrize("service", ["set_code", "read_zwave_codes"])
async def test_code_services_require_admin(
    hass: HomeAssistant, hass_read_only_user, service: str
) -> None:
    """Fix 3: code-mutating and code-reading services reject non-admin callers.

    ``set_code`` (mutating) and ``read_zwave_codes`` (reading) must be
    registered via async_register_admin_service, so calling them with a
    non-admin user context raises Unauthorized BEFORE the handler runs.
    """
    await registration.async_register_services(hass)
    assert hass.services.has_service(DOMAIN, service)

    call_data = {
        "set_code": {
            "entity_id": "lock.test_lock",
            "code_slot": 1,
            "usercode": "1234",
        },
        "read_zwave_codes": {"entity_id": "lock.test_lock"},
    }[service]

    with pytest.raises(Unauthorized):
        await hass.services.async_call(
            DOMAIN,
            service,
            call_data,
            blocking=True,
            context=Context(user_id=hass_read_only_user.id),
        )


@pytest.mark.asyncio
async def test_non_code_service_is_not_admin_gated(
    hass: HomeAssistant, hass_read_only_user
) -> None:
    """Fix 3 (complement): a read-only status service is NOT admin-gated.

    ``pause_alerts`` exposes no code data, so a non-admin user must be able to
    invoke it without hitting Unauthorized. It may still fail for unrelated
    reasons, but never with Unauthorized.
    """
    await registration.async_register_services(hass)
    assert hass.services.has_service(DOMAIN, "pause_alerts")

    try:
        await hass.services.async_call(
            DOMAIN,
            "pause_alerts",
            {},
            blocking=True,
            context=Context(user_id=hass_read_only_user.id),
        )
    except Unauthorized:  # pragma: no cover - this is the failure we assert against
        pytest.fail("pause_alerts must not be admin-gated")
    except Exception:
        # Any other error (missing lock/state) is irrelevant to the gating test.
        pass
