"""Zone runtime glue for Smart Lock Manager (Phase 1).

Holds the in-memory zone registry plus the three runtime behaviours that wire
the integration onto the zone model WITHOUT rewriting the delicate per-lock
Z-Wave sync dispatch:

1. **One-time migration** (``async_run_migration_if_needed``) — builds zones
   from the legacy parent/child lock data the first time it runs, lifting the
   codes off each former main lock. Idempotent via a persisted marker; never
   destroys the legacy per-lock storage (kept as fallback).
2. **Zone registry** — ``hass.data[ZONE_REGISTRY_KEY]`` maps ``zone_id`` ->
   ``Zone``. Loaded from storage on first setup.
3. **Per-cycle mirror** (``mirror_owning_zone_to_member``) — projects the
   owning zone's canonical code slots onto a member lock at the start of every
   coordinator cycle, so the EXISTING per-lock sync loop pushes the zone's
   codes to that member's Z-Wave node.

The registry is process-wide (one HA instance per dev/prod deployment) and
shared across all config entries.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Dict, List, Optional, cast

from homeassistant.core import HomeAssistant

from .const import DOMAIN, PRIMARY_LOCK
from .models.lock import SmartLockManagerLock
from .models.zone import Zone
from .storage import (
    load_all_zones,
    load_migration_marker,
    save_migration_marker,
    save_zone,
)

_LOGGER = logging.getLogger(__name__)

# hass.data key for the in-memory {zone_id: Zone} registry.
ZONE_REGISTRY_KEY = f"{DOMAIN}_zones"

# hass.data flag marking that zones were loaded from storage this process.
_ZONES_LOADED_FLAG = f"{DOMAIN}_zones_loaded"

# hass.data key for the asyncio.Lock that serializes the one-time migration.
# Each lock is its own config entry, so up to N entries call the migration
# concurrently during parallel setup. Without serialization they all observe
# "no marker yet" and each builds a full (duplicate) zone set. The lock makes
# the check-then-build-then-mark sequence atomic across entries.
_MIGRATION_LOCK_KEY = f"{DOMAIN}_zone_migration_lock"


def _migration_lock(hass: HomeAssistant) -> asyncio.Lock:
    """Return the process-wide migration lock, creating it on first use."""
    lock = hass.data.get(_MIGRATION_LOCK_KEY)
    if lock is None:
        lock = asyncio.Lock()
        hass.data[_MIGRATION_LOCK_KEY] = lock
    return cast(asyncio.Lock, lock)


def get_zone_registry(hass: HomeAssistant) -> Dict[str, Zone]:
    """Return the in-memory zone registry, creating it if absent.

    - Inputs: hass (HomeAssistant).
    - Outputs: dict mapping zone_id -> Zone (mutable, process-wide).
    """
    return cast(Dict[str, Zone], hass.data.setdefault(ZONE_REGISTRY_KEY, {}))


def get_zone_for_lock(hass: HomeAssistant, entity_id: str) -> Optional[Zone]:
    """Return the zone that owns ``entity_id``, or None if unhomed.

    - Inputs: hass (HomeAssistant), entity_id (str lock entity id).
    - Outputs: the owning Zone, or None.
    """
    for zone in get_zone_registry(hass).values():
        if zone.has_member(entity_id):
            return zone
    return None


def get_unhomed_lock_entity_ids(hass: HomeAssistant) -> List[str]:
    """Return entity ids of every loaded lock that is in no zone.

    The "unhomed pool" — locks with a config entry but no zone membership. The
    Phase-3 "+" picker draws from this. Order follows registry iteration.

    - Inputs: hass (HomeAssistant).
    - Outputs: sorted list of unhomed lock entity_id strings (may be empty).
    """
    homed: set[str] = set()
    for zone in get_zone_registry(hass).values():
        homed.update(zone.member_lock_entity_ids)
    unhomed = [eid for eid in _all_locks(hass) if eid not in homed]
    return sorted(unhomed)


def _all_locks(hass: HomeAssistant) -> Dict[str, SmartLockManagerLock]:
    """Return every loaded lock object keyed by entity_id.

    - Inputs: hass (HomeAssistant).
    - Outputs: dict entity_id -> SmartLockManagerLock.
    """
    locks: Dict[str, SmartLockManagerLock] = {}
    for entry_data in hass.data.get(DOMAIN, {}).values():
        if isinstance(entry_data, dict):
            lock = entry_data.get(PRIMARY_LOCK)
            if lock is not None:
                locks[lock.lock_entity_id] = lock
    return locks


async def async_ensure_zones_loaded(hass: HomeAssistant) -> None:
    """Load persisted zones into the registry once per HA process.

    - Description: On the first config entry setup, hydrate the in-memory zone
      registry from storage. Subsequent calls are no-ops.
    - Inputs: hass (HomeAssistant).
    - Outputs: None.
    """
    if hass.data.get(_ZONES_LOADED_FLAG):
        return
    registry = get_zone_registry(hass)
    loaded = await load_all_zones(hass)
    registry.update(loaded)
    hass.data[_ZONES_LOADED_FLAG] = True
    _LOGGER.info("Zone registry hydrated: %d zone(s) loaded", len(loaded))


def _build_zones_from_locks(locks: Dict[str, SmartLockManagerLock]) -> List[Zone]:
    """Construct zones from legacy parent/child lock data.

    Each ``is_main_lock`` lock plus its ``child_lock_ids`` becomes ONE zone,
    with the codes LIFTED from the (former) main lock. Each standalone main
    lock becomes a 1-member zone. Children are only attached to a zone via
    their parent; a child that also (incorrectly) claims ``is_main_lock`` is
    still treated as a member of its parent's zone, not its own.

    - Inputs: locks (dict entity_id -> SmartLockManagerLock).
    - Outputs: list of newly-built Zone objects.
    """
    # Identify every entity that is claimed as a child so we never also emit it
    # as its own standalone zone.
    claimed_children: set[str] = set()
    for lock in locks.values():
        for child_id in lock.child_lock_ids:
            if child_id in locks:
                claimed_children.add(child_id)
        if lock.parent_lock_id and lock.parent_lock_id in locks:
            claimed_children.add(lock.lock_entity_id)

    zones: List[Zone] = []
    for entity_id, lock in locks.items():
        # Skip anything owned by a parent — it becomes a member, not a zone.
        if entity_id in claimed_children:
            continue

        # Membership = this main/standalone lock first, then its children.
        members: List[str] = [entity_id]
        for child_id in lock.child_lock_ids:
            if child_id in locks and child_id not in members:
                members.append(child_id)
        # Also pick up any lock that points back at this one as parent but was
        # not listed in child_lock_ids (defensive: one-sided links).
        for other_id, other in locks.items():
            if other.parent_lock_id == entity_id and other_id not in members:
                members.append(other_id)

        zone = Zone.from_main_lock(lock, members)
        zones.append(zone)
        _LOGGER.info(
            "Migration: built zone '%s' members=%s lifted_codes=%d",
            zone.name,
            zone.member_lock_entity_ids,
            zone.get_configured_codes_count(),
        )
    return zones


async def async_run_migration_if_needed(hass: HomeAssistant) -> bool:
    """Run the one-time parent/child -> zone migration, idempotently.

    - Description: If no migration marker is persisted, build zones from the
      currently-loaded legacy locks, persist each zone, register them in
      memory, and write the marker. Safe to call on every setup — it returns
      early once the marker exists. Does NOT touch the legacy per-lock storage.
    - Inputs: hass (HomeAssistant).
    - Outputs: True if migration ran this call, False if it was already done.
    """
    # Fast pre-check (no lock) to avoid contending on the common already-done
    # path. The authoritative re-check happens inside the lock below.
    marker = await load_migration_marker(hass)
    if marker and marker.get("migrated"):
        _LOGGER.debug("Zone migration already complete (marker present)")
        return False

    # Serialize the whole check-build-mark sequence so concurrent config-entry
    # setups cannot each build a duplicate zone set.
    async with _migration_lock(hass):
        # Authoritative re-check: another entry may have migrated while we
        # waited for the lock.
        marker = await load_migration_marker(hass)
        if marker and marker.get("migrated"):
            _LOGGER.debug("Zone migration already done by a concurrent entry")
            return False

        locks = _all_locks(hass)
        if not locks:
            _LOGGER.warning("Zone migration skipped: no locks loaded yet")
            return False

        # Gate: only migrate once EVERY SLM config entry's lock is loaded, so
        # we build the full zone set in one shot rather than a partial set on
        # the first entry to finish setup. Each lock is its own config entry,
        # so the expected count is the number of SLM domain entries.
        expected = len(hass.config_entries.async_entries(DOMAIN))
        if expected and len(locks) < expected:
            _LOGGER.debug(
                "Zone migration deferred: %d/%d locks loaded",
                len(locks),
                expected,
            )
            return False

        registry = get_zone_registry(hass)
        zones = _build_zones_from_locks(locks)
        for zone in zones:
            registry[zone.zone_id] = zone
            await save_zone(hass, zone)

        await save_migration_marker(
            hass,
            {
                "migrated": True,
                "zone_count": len(zones),
                "zone_ids": [z.zone_id for z in zones],
            },
        )
        _LOGGER.info(
            "Zone migration complete: built %d zone(s) from %d lock(s)",
            len(zones),
            len(locks),
        )
        return True


def mirror_owning_zone_to_member(
    hass: HomeAssistant, lock: SmartLockManagerLock
) -> Optional[Zone]:
    """Project the owning zone's code slots onto a member lock.

    Called at the start of each coordinator cycle for a member lock. Finds the
    zone that owns ``lock`` and mirrors its canonical code definitions onto the
    member (without disturbing the member's per-lock usage/sync state). After
    this, the existing per-lock sync loop pushes the zone's codes to this
    member's Z-Wave node.

    - Inputs: hass (HomeAssistant), lock (SmartLockManagerLock).
    - Outputs: the owning Zone (for logging), or None if the lock is unhomed.
    """
    zone = get_zone_for_lock(hass, lock.lock_entity_id)
    if zone is None:
        return None
    zone.mirror_to_member(lock)
    return zone
