"""Tests for the per-member companion-entity overrides (``member_meta``).

``member_meta`` is the SOURCE OF TRUTH the jam / low_battery health detectors
resolve before auto-discovery (see :mod:`tests.test_engines` for the detector
behaviour). These tests cover the MODEL + SERVICE + API plumbing:

* the dataclass round-trips through ``to_dict`` / ``settings_from_dict`` and a
  zone persisted before the field existed hydrates to an empty map;
* the ``update_zone_settings`` service deep-merges a partial ``member_meta``
  block without clobbering sibling members or other settings blocks; and
* the override is exposed verbatim over the zone DATA API (entity ids only,
  never PIN material).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from homeassistant.core import HomeAssistant

from custom_components.smart_lock_manager.api.zones import _serialize_zone
from custom_components.smart_lock_manager.models.zone import Zone
from custom_components.smart_lock_manager.models.zone_settings import (
    MemberMeta,
    ZoneSettings,
    merge_settings,
    settings_from_dict,
)
from custom_components.smart_lock_manager.services.zone_settings_service import (
    ZoneSettingsService,
)
from custom_components.smart_lock_manager.zone_runtime import ZONE_REGISTRY_KEY

LOCK_A = "lock.bathroom"
LOCK_B = "lock.front_north"


def test_member_meta_round_trips_through_to_dict() -> None:
    """A populated member_meta survives to_dict -> settings_from_dict intact."""
    settings = ZoneSettings()
    settings.member_meta[LOCK_A] = MemberMeta(
        jam_sensor="binary_sensor.bathroom_lock_jammed",
        battery_entity="sensor.bathroom_battery_level",
    )
    rebuilt = settings_from_dict(settings.to_dict())
    meta = rebuilt.member_meta[LOCK_A]
    assert meta.jam_sensor == "binary_sensor.bathroom_lock_jammed"
    assert meta.battery_entity == "sensor.bathroom_battery_level"


def test_legacy_zone_without_member_meta_hydrates_empty() -> None:
    """A settings blob with no member_meta key yields an empty map (no crash)."""
    rebuilt = settings_from_dict({"alerts": {"jam": {"enabled": True}}})
    assert rebuilt.member_meta == {}
    # Defaults for an unconfigured MemberMeta are empty -> auto-discovery.
    assert MemberMeta().jam_sensor == ""
    assert MemberMeta().battery_entity == ""


def test_merge_settings_adds_member_meta_without_clobbering_siblings() -> None:
    """Partial member_meta merge keeps existing members + other blocks intact."""
    current = ZoneSettings()
    current.alerts.jam.enabled = True
    current.member_meta[LOCK_A] = MemberMeta(jam_sensor="binary_sensor.a_jam")

    merged = merge_settings(
        current,
        {"member_meta": {LOCK_B: {"battery_entity": "sensor.front_battery_level"}}},
    )
    # Existing member preserved.
    assert merged.member_meta[LOCK_A].jam_sensor == "binary_sensor.a_jam"
    # New member added.
    assert merged.member_meta[LOCK_B].battery_entity == "sensor.front_battery_level"
    # Sibling settings block untouched.
    assert merged.alerts.jam.enabled is True


async def test_update_zone_settings_service_persists_member_meta(
    hass: HomeAssistant,
) -> None:
    """The update_zone_settings service accepts + persists a member_meta block."""
    zone = Zone(zone_id="z1", name="Zone One", member_lock_entity_ids=[LOCK_A])
    hass.data[ZONE_REGISTRY_KEY] = {"z1": zone}

    call = type(
        "C",
        (),
        {
            "data": {
                "zone_id": "z1",
                "settings": {
                    "member_meta": {
                        LOCK_A: {
                            "jam_sensor": "binary_sensor.bathroom_lock_jammed",
                            "battery_entity": "sensor.bathroom_battery_level",
                        }
                    }
                },
            }
        },
    )()

    with (
        patch(
            "custom_components.smart_lock_manager.services."
            "zone_settings_service.save_zone",
            AsyncMock(),
        ),
        patch(
            "custom_components.smart_lock_manager.services."
            "zone_settings_service._refresh_all_coordinators",
            AsyncMock(),
        ),
    ):
        await ZoneSettingsService.update_zone_settings(
            hass, call  # type: ignore[arg-type]
        )

    meta = hass.data[ZONE_REGISTRY_KEY]["z1"].settings.member_meta[LOCK_A]
    assert meta.jam_sensor == "binary_sensor.bathroom_lock_jammed"
    assert meta.battery_entity == "sensor.bathroom_battery_level"


def test_member_meta_exposed_over_zone_api(hass: HomeAssistant) -> None:
    """The zone DATA API surfaces member_meta under settings (entity ids only)."""
    zone = Zone(zone_id="z1", name="Zone One", member_lock_entity_ids=[LOCK_A])
    zone.settings.member_meta[LOCK_A] = MemberMeta(
        jam_sensor="binary_sensor.bathroom_lock_jammed",
        battery_entity="sensor.bathroom_battery_level",
    )
    serialized = _serialize_zone(hass, zone, {})
    api_meta = serialized["settings"]["member_meta"][LOCK_A]
    assert api_meta["jam_sensor"] == "binary_sensor.bathroom_lock_jammed"
    assert api_meta["battery_entity"] == "sensor.bathroom_battery_level"
