#!/usr/bin/env python3
"""Seed the dev HA .storage with the CURRENT office lock topology.

Reproduces the office parent/child structure as Smart Lock Manager config
entries + ``smart_lock_manager_<entry_id>`` state files, so the upcoming
zone-model refactor (and its migration) can be developed against a faithful
baseline. We have NOT built the zone model yet — this baseline mirrors the
parent/child relationships exactly as they exist today.

What it writes into ``dev-config/.storage/``:
  - ``core.config_entries``: one SLM entry per lock (merged with any existing
    non-SLM entries, e.g. sun/backup/go2rtc).
  - ``smart_lock_manager_<entry_id>``: per-lock state, shaped exactly like the
    existing single ``smart_lock_manager_01KT...`` entry (same keys/order).

The template lock entities themselves come from ``configuration.yaml`` and the
Z-Wave node mapping from ``dev_mock.ENTITY_TO_NODE_ID`` — this script only
seeds SLM's own persisted data.

Sample PINs are obviously fake (1111/2222/etc.) and never real codes.

Usage:
    python3 scripts/seed_dev_zones.py            # write seed files
    python3 scripts/seed_dev_zones.py --dry-run  # print, write nothing

Re-running is idempotent: SLM entries/state files are rewritten, other config
entries are preserved. Run while the dev HA container is STOPPED (HA caches
.storage in memory and rewrites on shutdown).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(_SCRIPT_DIR)
_STORAGE_DIR = os.path.join(_REPO_ROOT, "dev-config", ".storage")
_CONFIG_ENTRIES_PATH = os.path.join(_STORAGE_DIR, "core.config_entries")

_DOMAIN = "smart_lock_manager"

# ---------------------------------------------------------------------------
# Office topology seed. entity_id / node_id mirror dev_mock.ENTITY_TO_NODE_ID.
# Each lock: a deterministic entry_id (ULID-shaped), parent/child wiring, and a
# handful of obviously-fake active slots. Slots not listed here are emitted as
# empty, synced placeholders — matching the live entry's shape.
# ---------------------------------------------------------------------------
_SLOT_COUNT = 8  # match the existing live entry's slot count

# active_slots: {slot_number: (pin, user_name)}
_LOCKS: List[Dict[str, Any]] = [
    {
        "entry_id": "01KTDEV0000000000000NORTH0",
        "lock_entity_id": "lock.front_north",
        "lock_name": "Front North",
        "node_id": 28,
        "is_main_lock": True,
        "parent_lock_id": None,
        "child_lock_ids": ["lock.rear"],
        "active_slots": {1: ("1111", "North Tenant A"), 2: ("2222", "North Tenant B")},
    },
    {
        "entry_id": "01KTDEV00000000000000REAR0",
        "lock_entity_id": "lock.rear",
        "lock_name": "Rear",
        "node_id": 26,
        "is_main_lock": False,
        "parent_lock_id": "lock.front_north",
        "child_lock_ids": [],
        "active_slots": {},
    },
    {
        "entry_id": "01KTDEV000000000000MIDDLE0",
        "lock_entity_id": "lock.front_middle_door_lock",
        "lock_name": "Front Middle Door Lock",
        "node_id": 13,
        "is_main_lock": True,
        "parent_lock_id": None,
        "child_lock_ids": ["lock.bathroom"],
        "active_slots": {1: ("3333", "Middle Tenant")},
    },
    {
        "entry_id": "01KTDEV00000000000000BATH0",
        "lock_entity_id": "lock.bathroom",
        "lock_name": "Bathroom",
        "node_id": 19,
        "is_main_lock": False,
        "parent_lock_id": "lock.front_middle_door_lock",
        "child_lock_ids": [],
        "active_slots": {},
    },
    {
        "entry_id": "01KTDEV00000000000000SOUTH",
        "lock_entity_id": "lock.front_south_door_lock",
        "lock_name": "Front South Door Lock",
        "node_id": 14,
        "is_main_lock": True,
        "parent_lock_id": None,
        "child_lock_ids": [],
        "active_slots": {1: ("4444", "South Tenant")},
    },
    {
        "entry_id": "01KTDEV0000000000000ST1050",
        "lock_entity_id": "lock.suite_105",
        "lock_name": "Suite 105",
        "node_id": 22,
        "is_main_lock": True,
        "parent_lock_id": None,
        "child_lock_ids": [],
        "active_slots": {1: ("5555", "Suite 105 A"), 2: ("6666", "Suite 105 B")},
    },
    {
        "entry_id": "01KTDEV0000000000000ST1060",
        "lock_entity_id": "lock.suite_106",
        "lock_name": "Suite 106",
        "node_id": 20,
        "is_main_lock": True,
        "parent_lock_id": None,
        "child_lock_ids": [],
        "active_slots": {1: ("7777", "Suite 106 Tenant")},
    },
]


def _empty_slot(slot_number: int) -> Dict[str, Any]:
    """Build an empty, synced placeholder slot (mirrors the live entry shape).

    - Inputs: slot_number (int).
    - Outputs: dict for one inactive code slot.
    """
    return {
        "slot_number": slot_number,
        "pin_code": None,
        "user_name": None,
        "is_active": False,
        "is_synced": True,
        "sync_attempts": 0,
        "sync_error": None,
        "validation_rejections": 0,
        "user_id_status": None,
        "start_date": None,
        "end_date": None,
        "allowed_hours": None,
        "allowed_days": None,
        "max_uses": -1,
        "use_count": 0,
        "notify_on_use": False,
        "created_at": None,
        "last_used": None,
        "last_sync_attempt": None,
    }


def _active_slot(slot_number: int, pin: str, user_name: str) -> Dict[str, Any]:
    """Build an active slot with a fake PIN (mirrors the live entry shape).

    - Inputs: slot_number (int), pin (str fake PIN), user_name (str).
    - Outputs: dict for one active, synced code slot.
    """
    slot = _empty_slot(slot_number)
    slot.update(
        {
            "pin_code": pin,
            "user_name": user_name,
            "is_active": True,
            "is_synced": True,
            "user_id_status": 1,  # USER_ID_STATUS_ENABLED
        }
    )
    return slot


def _build_state(lock: Dict[str, Any]) -> Dict[str, Any]:
    """Build the full ``smart_lock_manager_<entry_id>`` Store payload.

    - Description: Construct the state file body with the SAME top-level shape
      as the existing live entry: ``version``/``minor_version``/``key``/``data``
      where ``data`` is exactly ``SmartLockManagerLock.to_dict()``-shaped.
    - Inputs: one entry from ``_LOCKS``.
    - Outputs: dict ready to JSON-dump as the Store file.
    """
    code_slots: Dict[str, Any] = {}
    active = lock["active_slots"]
    for slot_number in range(1, _SLOT_COUNT + 1):
        if slot_number in active:
            pin, user_name = active[slot_number]
            code_slots[str(slot_number)] = _active_slot(slot_number, pin, user_name)
        else:
            code_slots[str(slot_number)] = _empty_slot(slot_number)

    data = {
        "lock_name": lock["lock_name"],
        "lock_entity_id": lock["lock_entity_id"],
        "slots": _SLOT_COUNT,
        "start_from": 1,
        "is_main_lock": lock["is_main_lock"],
        "parent_lock_id": lock["parent_lock_id"],
        "child_lock_ids": lock["child_lock_ids"],
        "code_collision_prefix_length": 4,
        "settings": {
            "friendly_name": lock["lock_name"],
            "auto_lock_time": None,
            "auto_unlock_time": None,
            "timezone": "UTC",
        },
        "code_slots": code_slots,
        "access_log": [],
        "is_connected": True,
        "connection_status": "Connected",
        "last_updated": None,
    }

    return {
        "version": 1,
        "minor_version": 1,
        "key": f"{_DOMAIN}_{lock['entry_id']}",
        "data": data,
    }


def _build_config_entry(lock: Dict[str, Any], now_iso: str) -> Dict[str, Any]:
    """Build one ``core.config_entries`` entry for a lock.

    - Inputs: one entry from ``_LOCKS``, now_iso (ISO timestamp str).
    - Outputs: dict matching HA's config-entry schema (minor_version 5).
    """
    return {
        "created_at": now_iso,
        "data": {
            "lock_entity_id": lock["lock_entity_id"],
            "lock_name": lock["lock_name"],
            "slots": _SLOT_COUNT,
        },
        "disabled_by": None,
        "discovery_keys": {},
        "domain": _DOMAIN,
        "entry_id": lock["entry_id"],
        "minor_version": 1,
        "modified_at": now_iso,
        "options": {},
        "pref_disable_new_entities": False,
        "pref_disable_polling": False,
        "source": "user",
        "subentries": [],
        "title": f"{lock['lock_name']} SLM",
        "unique_id": lock["lock_entity_id"],
        "version": 1,
    }


def _load_config_entries() -> Dict[str, Any]:
    """Load the existing core.config_entries file, or a fresh skeleton.

    - Outputs: the parsed config-entries Store dict.
    """
    if os.path.exists(_CONFIG_ENTRIES_PATH):
        with open(_CONFIG_ENTRIES_PATH, "r", encoding="utf-8") as fh:
            return json.load(fh)
    return {
        "version": 1,
        "minor_version": 5,
        "key": "core.config_entries",
        "data": {"entries": []},
    }


def main(argv: Optional[List[str]] = None) -> int:
    """Write seed config entries + per-lock state files.

    - Inputs: argv (list[str]|None) — CLI args; supports ``--dry-run``.
    - Outputs: process exit code (0 success).
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would be written without touching disk.",
    )
    args = parser.parse_args(argv)

    if not os.path.isdir(_STORAGE_DIR):
        print(f"ERROR: storage dir not found: {_STORAGE_DIR}", file=sys.stderr)
        print("Boot the dev HA container at least once first.", file=sys.stderr)
        return 1

    now_iso = datetime.now(timezone.utc).isoformat()

    # Merge: keep all non-SLM config entries, replace SLM entries with ours.
    cfg = _load_config_entries()
    existing = cfg.get("data", {}).get("entries", [])
    kept = [e for e in existing if e.get("domain") != _DOMAIN]
    new_slm = [_build_config_entry(lock, now_iso) for lock in _LOCKS]
    cfg.setdefault("data", {})["entries"] = kept + new_slm

    state_files = {
        os.path.join(_STORAGE_DIR, f"{_DOMAIN}_{lock['entry_id']}"): _build_state(lock)
        for lock in _LOCKS
    }

    if args.dry_run:
        print("DRY RUN — nothing written.\n")
        print(f"core.config_entries: {len(kept)} kept + {len(new_slm)} SLM entries")
        for path in state_files:
            print(f"  would write {os.path.basename(path)}")
        return 0

    with open(_CONFIG_ENTRIES_PATH, "w", encoding="utf-8") as fh:
        json.dump(cfg, fh, indent=2)
    for path, payload in state_files.items():
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2)

    print(f"Seeded {len(new_slm)} SLM config entries -> {_CONFIG_ENTRIES_PATH}")
    for path in state_files:
        print(f"Seeded state -> {os.path.basename(path)}")
    print("\nDone. Start the dev HA container to load the seeded topology.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
