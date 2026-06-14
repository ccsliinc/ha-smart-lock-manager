"""Tests for the ZONE-LEVEL slot sync-status aggregation (panel warning banner).

The zone DATA API derives each zone slot's sync status LIVE from its member
locks rather than the zone's frozen mirror copy. Two production layers are
exercised here directly (never reimplemented in the test):

* :func:`_derive_slot_sync` — the per-slot worst-case aggregator that decides
  synced / pending / error across every contributing member.
* :func:`_serialize_zone` — the roll-up that collects ``error_slots`` and the
  ``has_sync_errors`` flag the panel uses to render its amber "⚠ Slot N failed
  to sync" warning banner.

The critical safety property under test: a not-yet-synced member that is still
BELOW the retry cap with no recorded error must report ``pending`` (an in-flight
write) and must NOT trip the banner. Only a genuine failure — an explicit
``sync_error`` or an exhausted retry cap — yields ``error``.
"""

from __future__ import annotations

from unittest.mock import Mock

from custom_components.smart_lock_manager.api.zones import (
    SYNC_STATUS_ERROR,
    SYNC_STATUS_PENDING,
    SYNC_STATUS_SYNCED,
    _derive_slot_sync,
    _serialize_zone,
)
from custom_components.smart_lock_manager.const import MAX_SYNC_ATTEMPTS
from custom_components.smart_lock_manager.models.lock import (
    CodeSlot,
    SmartLockManagerLock,
)
from custom_components.smart_lock_manager.models.zone import Zone

SLOT = 1
PIN = "1234"


def _member(entity_id: str, slot: CodeSlot) -> SmartLockManagerLock:
    """Build a member lock carrying exactly one code slot.

    - Inputs: entity_id (str), slot (CodeSlot to install at ``slot.slot_number``).
    - Outputs: SmartLockManagerLock with that single slot.
    """
    lock = SmartLockManagerLock(
        lock_name=entity_id,
        lock_entity_id=entity_id,
        slots=1,
        start_from=slot.slot_number,
    )
    lock.code_slots = {slot.slot_number: slot}
    return lock


def _member_slot(**kwargs: object) -> CodeSlot:
    """Build a member's copy of the slot with sync-state overrides.

    Carries the same PIN/name the zone owns so it counts as a contributing
    member; sync flags (``is_synced``, ``sync_attempts``, ``sync_error``) are
    supplied per-test.

    - Inputs: kwargs overriding CodeSlot sync fields.
    - Outputs: CodeSlot.
    """
    defaults: dict = {
        "slot_number": SLOT,
        "pin_code": PIN,
        "user_name": "Tester",
        "is_active": True,
    }
    defaults.update(kwargs)
    return CodeSlot(**defaults)  # type: ignore[arg-type]


def _zone(members: list[SmartLockManagerLock], *, pin: str | None = PIN) -> Zone:
    """Build a zone owning slot ``SLOT`` with ``pin`` over the given members.

    - Inputs: members (member lock objects), pin (zone slot PIN; None -> empty).
    - Outputs: Zone with one configured slot and those members.
    """
    zone = Zone(
        zone_id="zone_sync_test",
        name="Sync Test Zone",
        member_lock_entity_ids=[m.lock_entity_id for m in members],
        slots=1,
        start_from=SLOT,
    )
    zone.code_slots[SLOT] = CodeSlot(
        slot_number=SLOT, pin_code=pin, user_name="Tester", is_active=bool(pin)
    )
    return zone


def _serialize(zone: Zone, members: list[SmartLockManagerLock]) -> dict:
    """Run the real ``_serialize_zone`` roll-up with a no-op hass.

    ``_serialize_zone`` only reads member HARDWARE state off ``hass`` (lock
    state, battery, node id) for display; with ``hass.states.get`` returning
    None those degrade to ``unavailable``/None and never touch the sync logic
    under test. The slot sync derivation is the in-memory member objects.

    - Inputs: zone (Zone), members (member lock objects backing it).
    - Outputs: the serialized zone dict.
    """
    hass = Mock()
    hass.states.get.return_value = None
    locks = {m.lock_entity_id: m for m in members}
    return _serialize_zone(hass, zone, locks)


# -- _derive_slot_sync: per-slot worst-case aggregation ---------------------


def test_all_members_synced_is_synced() -> None:
    """Every contributing member synced -> slot synced, no error detail."""
    members = [
        _member("lock.a", _member_slot(is_synced=True)),
        _member("lock.b", _member_slot(is_synced=True)),
    ]
    zone = _zone(members)
    result = _derive_slot_sync(zone, SLOT, members)
    assert result["status"] == SYNC_STATUS_SYNCED
    assert result["sync_error"] is None


def test_member_with_explicit_sync_error_is_error() -> None:
    """A member carrying an explicit ``sync_error`` -> slot error."""
    members = [
        _member("lock.a", _member_slot(is_synced=True)),
        _member(
            "lock.b",
            _member_slot(is_synced=False, sync_error="supervision failure"),
        ),
    ]
    zone = _zone(members)
    result = _derive_slot_sync(zone, SLOT, members)
    assert result["status"] == SYNC_STATUS_ERROR
    assert result["sync_error"] == "supervision failure"


def test_member_over_retry_cap_is_error() -> None:
    """A member that exhausted the retry cap (no explicit error) -> error."""
    members = [
        _member(
            "lock.a",
            _member_slot(is_synced=False, sync_attempts=MAX_SYNC_ATTEMPTS),
        ),
    ]
    zone = _zone(members)
    result = _derive_slot_sync(zone, SLOT, members)
    assert result["status"] == SYNC_STATUS_ERROR
    # Falls back to the synthesized retry-cap detail string.
    assert str(MAX_SYNC_ATTEMPTS) in (result["sync_error"] or "")


def test_member_below_cap_no_error_is_pending() -> None:
    """In-flight write below the cap with no error -> pending, NOT error.

    This is the property that keeps the panel banner from firing on a normal
    in-progress sync.
    """
    members = [
        _member(
            "lock.a",
            _member_slot(is_synced=False, sync_attempts=MAX_SYNC_ATTEMPTS - 1),
        ),
    ]
    zone = _zone(members)
    result = _derive_slot_sync(zone, SLOT, members)
    assert result["status"] == SYNC_STATUS_PENDING
    assert result["sync_error"] is None


def test_multi_member_error_wins_over_synced() -> None:
    """Worst-case wins: one synced + one failed member -> error."""
    members = [
        _member("lock.a", _member_slot(is_synced=True)),
        _member(
            "lock.b",
            _member_slot(is_synced=False, sync_error="node 26 write failed"),
        ),
    ]
    zone = _zone(members)
    result = _derive_slot_sync(zone, SLOT, members)
    assert result["status"] == SYNC_STATUS_ERROR
    assert result["sync_error"] == "node 26 write failed"


def test_empty_codeless_slot_is_synced() -> None:
    """A zone slot with no PIN configured -> synced (nothing to push)."""
    members = [_member("lock.a", _member_slot(pin_code=None, is_active=False))]
    zone = _zone(members, pin=None)
    result = _derive_slot_sync(zone, SLOT, members)
    assert result["status"] == SYNC_STATUS_SYNCED
    assert result["sync_error"] is None


# -- _serialize_zone roll-up: error_slots / has_sync_errors -----------------


def test_zone_rollup_all_synced_has_no_errors() -> None:
    """All-synced zone -> has_sync_errors False, error_slots empty."""
    members = [
        _member("lock.a", _member_slot(is_synced=True)),
        _member("lock.b", _member_slot(is_synced=True)),
    ]
    zone = _zone(members)
    payload = _serialize(zone, members)
    assert payload["has_sync_errors"] is False
    assert payload["error_slots"] == []
    slot = next(s for s in payload["code_slots"] if s["slot_number"] == SLOT)
    assert slot["sync_status"] == SYNC_STATUS_SYNCED
    assert slot["is_synced"] is True


def test_zone_rollup_member_error_trips_banner() -> None:
    """A failed member surfaces in error_slots + has_sync_errors (banner)."""
    members = [
        _member("lock.a", _member_slot(is_synced=True)),
        _member(
            "lock.b",
            _member_slot(is_synced=False, sync_error="supervision failure"),
        ),
    ]
    zone = _zone(members)
    payload = _serialize(zone, members)
    assert payload["has_sync_errors"] is True
    assert payload["error_slots"] == [SLOT]
    slot = next(s for s in payload["code_slots"] if s["slot_number"] == SLOT)
    assert slot["sync_status"] == SYNC_STATUS_ERROR
    assert slot["sync_error"] == "supervision failure"
    assert slot["is_synced"] is False


def test_zone_rollup_pending_member_does_not_trip_banner() -> None:
    """An in-flight (below-cap) member must NOT populate error_slots."""
    members = [
        _member(
            "lock.a",
            _member_slot(is_synced=False, sync_attempts=MAX_SYNC_ATTEMPTS - 1),
        ),
    ]
    zone = _zone(members)
    payload = _serialize(zone, members)
    assert payload["has_sync_errors"] is False
    assert payload["error_slots"] == []
    slot = next(s for s in payload["code_slots"] if s["slot_number"] == SLOT)
    assert slot["sync_status"] == SYNC_STATUS_PENDING
