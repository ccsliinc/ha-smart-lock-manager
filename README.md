# Smart Lock Manager for Home Assistant

Manage PIN codes on Z-Wave smart locks (Kwikset, Yale, Schlage, and other locks that
expose user-code management through Z-Wave JS). Set per-slot codes, restrict them by
time of day, day of week, and date range, cap them by number of uses, group locks into
**zones** that share a single canonical code set, and keep an access log. All of it runs
off a single summary sensor per lock instead of dozens of entities.

[![HACS Custom](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://github.com/ccsliinc/ha-smart-lock-manager)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Home Assistant](https://img.shields.io/badge/Home%20Assistant-2024.8+-blue.svg)](https://www.home-assistant.io/)
[![Version](https://img.shields.io/badge/Version-2026.7.0-green.svg)](#)
[![Buy Me A Coffee](https://img.shields.io/badge/Buy%20Me%20A%20Coffee-Support-orange.svg?logo=buy-me-a-coffee)](https://www.buymeacoffee.com/ccsliinc)
[![PayPal](https://img.shields.io/badge/PayPal-Donate-blue.svg?logo=paypal)](https://paypal.me/jsugamele)

## Features

- Per-slot PIN codes, named, with up to 50 slots per lock.
- Scheduled access: allowed hours (0-23), allowed days (Mon=0 to Sun=6), and start/end
  date ranges. Codes outside their window are disabled and re-enabled automatically.
- Usage limits: cap a code at a fixed number of uses, then auto-disable it.
- **Zones**: a zone owns the canonical code-slot set; every member lock obeys it
  uniformly. Each lock belongs to exactly one zone (a single lock is a 1-member zone);
  locks not yet placed in a zone sit in an **unhomed pool**. Adding a lock to a zone
  applies the zone's codes to its hardware; removing it wipes them and returns it to the
  pool. This replaces the old parent/child "main lock + child locks" model.
- **Lock-health alerting (opt-in)**: detect outside-hours unlocks, sustained-unlock
  escalation, jam, low-battery, and offline conditions, record them to an alert log, and
  fire recovery notices when they clear. Email notifications render as HTML cards. The
  engine ships **off by default** in a safe observe / dry-run posture (see
  [Enabling alerting & auto-lock](#enabling-alerting--auto-lock-opt-in)).
- **Auto-lock (opt-in)**: scheduled close-of-business lockdown and idle-timeout
  auto-lock, configured per zone. Like alerting, it only records "would auto-lock"
  intents until you explicitly enable real actions.
- **Mute and snooze**: permanently mute a specific lock (optionally a single alert type)
  until you unmute it, or snooze a zone (or everything) for a set number of hours.
- Access log and per-slot usage counters.
- One summary sensor per lock. All slot state lives in Python objects and is exposed as
  sensor attributes, so your entity list stays clean.
- UI config flow for setup, plus a Configure dialog to change the lock name, lock entity,
  and slot count after install (the entry reloads in place).
- Custom sidebar panel for managing slots, zones, schedules, and usage.

## Requirements

- Home Assistant 2024.8.0 or newer.
- Z-Wave JS integration configured and running.
- A Z-Wave lock that supports user-code management (tested with Kwikset, Yale, Schlage).

## Installation

### HACS (custom repository)

1. In HACS, open the three-dot menu and choose **Custom repositories**.
2. Add `https://github.com/ccsliinc/ha-smart-lock-manager` with category **Integration**.
3. Search for **Smart Lock Manager**, download it, and restart Home Assistant.

### Manual

```bash
wget https://github.com/ccsliinc/ha-smart-lock-manager/archive/refs/tags/v2026.7.0.zip
unzip v2026.7.0.zip
cp -r ha-smart-lock-manager-2026.7.0/custom_components/smart_lock_manager /config/custom_components/
```

Restart Home Assistant after copying the files.

## Configuration

### Initial setup

Go to **Settings → Devices & Services → Add Integration** and search for **Smart Lock
Manager**. The config flow asks for:

- **Lock entity** — the Z-Wave lock to manage.
- **Lock name** — a friendly name.
- **Slots** — how many code slots to expose (1-50, default 10).

The integration adds a **Smart Lock Manager** item to the sidebar for managing codes.

### Reconfigure after install

To change the lock name, the lock entity, or the slot count later, open the integration
under **Settings → Devices & Services**, click **Configure**, and edit the values. The
entry reloads in place and reconciles the slot collection to the new count. Lowering the
slot count clears the slots above the new limit.

## Working with zones

A zone is the canonical owner of a code set. Member locks mirror the zone's codes; their
own usage counters, last-used timestamps, and access logs stay per-lock.

```yaml
# 1. Create a zone (optionally seed it with unhomed locks).
service: smart_lock_manager.create_zone
data:
  name: "Front Entrances"
  member_lock_entity_ids:
    - lock.front_door
    - lock.back_door

# 2. Add another unhomed lock later (applies the zone's codes to its hardware).
service: smart_lock_manager.add_lock_to_zone
data:
  zone_id: "<zone_id>"
  lock_entity_id: lock.side_door

# 3. Re-push the zone's canonical codes to every member (idempotent).
service: smart_lock_manager.apply_zone_codes
data:
  zone_id: "<zone_id>"
```

Removing a lock (`remove_lock_from_zone`) or deleting the zone (`delete_zone`) wipes the
zone's codes off the affected hardware and returns members to the unhomed pool; the lock
entries themselves are preserved.

Per-zone operational behaviour — business hours, scheduled/idle auto-lock, alert toggles,
and notification channels — is edited with `update_zone_settings`. The `settings` object
is merged per block, so you only supply the blocks you want to change:

```yaml
service: smart_lock_manager.update_zone_settings
data:
  zone_id: "<zone_id>"
  settings:
    business_hours:
      enabled: true
      open_time: "08:30"
      close_time: "17:30"
    alerts:
      outside_hours:
        enabled: true
        severity: "CRIT"
```

## Enabling alerting & auto-lock (opt-in)

The alert and auto-lock engines are **off by default**. With them off, the engines are
never even constructed and production behaviour is unchanged. There are three independent
flags, all default OFF, and turning them on is deliberately a two-step, safe-by-default
process:

- **`enable_engines`** — constructs the engines and runs them in a **safe observe /
  dry-run** posture against your real locks. Alerts are detected and recorded, "would
  notify" intents are rendered, and auto-lock records "would auto-lock" intents — but
  **no notification is sent and no `lock.lock` is issued**.
- **`real_notify`** — independently lets the recorded alerts actually send (email / mobile)
  according to each zone's notify config. Has no effect unless engines are enabled.
- **`real_autolock`** — independently lets the auto-lock engine issue a real `lock.lock`.
  Has no effect unless engines are enabled.

So nothing real happens until you enable the engines AND the matching real-action flag.

### How to set the flags

There are two equivalent sources; a flag is ON if **either** source is truthy.

**1. Flags file (the way on Home Assistant OS).** HA OS does not let you set process
environment variables, so use a JSON file at your config directory:

```json
// /config/smart_lock_manager_flags.json
{
  "enable_engines": true,
  "real_notify": false,
  "real_autolock": false
}
```

The keys are exactly `enable_engines`, `real_notify`, and `real_autolock`. A missing file
means all-false; a malformed file is treated as all-false (and logs one warning). Changes
to the file take effect on the **next prime** — a Home Assistant restart, an integration
reload, or a zone-settings change — not instantly, because the engines are constructed
once at setup.

**2. Environment variables (container / Core installs).** If you can set env vars on the
HA process, use:

- `SLM_ENABLE_ENGINES`
- `SLM_ENABLE_REAL_NOTIFY`
- `SLM_ENABLE_REAL_AUTOLOCK`

Each accepts `1` / `true` / `yes` / `on` (case-insensitive) as truthy.

> Recommended rollout: set `enable_engines` first, watch the recorded alerts and
> "would-notify" / "would-auto-lock" intents in the panel for a while, then flip
> `real_notify` and/or `real_autolock` once you trust them.

## Mute and snooze

```yaml
# Permanently silence one lock (all alert types) until you unmute it.
service: smart_lock_manager.mute_lock_alert
data:
  entity_id: lock.front_door

# Mute just one alert type on one lock.
service: smart_lock_manager.mute_lock_alert
data:
  entity_id: lock.front_door
  alert_type: low_battery

# Re-enable alerts for that lock.
service: smart_lock_manager.unmute_lock_alert
data:
  entity_id: lock.front_door

# Snooze ALL zones for 2 hours (alerts still recorded, just not notified; auto-expires).
service: smart_lock_manager.pause_alerts
data:
  hours: 2

# Snooze a single zone, then clear it early.
service: smart_lock_manager.pause_alerts
data:
  hours: 8
  zone_id: "<zone_id>"

service: smart_lock_manager.resume_alerts
data:
  zone_id: "<zone_id>"
```

A **mute** is sticky and silences a lock's initial alert, repeat nags, and recovery alike.
A **snooze** is time-boxed and only suppresses repeat timer-origin nags — a fresh
state-change alert (or recovery) still notifies. Valid alert types are `jam`,
`low_battery`, `offline`, `outside_hours`, and `sustained_unlock` (or `all`).

## Services

| Service | Description |
| --- | --- |
| `smart_lock_manager.set_code` | Set a PIN on a slot. |
| `smart_lock_manager.set_code_advanced` | Set a PIN with schedule, date range, usage limit, and notify-on-use. |
| `smart_lock_manager.clear_code` | Clear the PIN from a slot. |
| `smart_lock_manager.clear_all_slots` | Clear every slot on a lock. |
| `smart_lock_manager.enable_slot` | Enable a slot. |
| `smart_lock_manager.disable_slot` | Disable a slot, keeping its data. |
| `smart_lock_manager.reset_slot_usage` | Reset a slot's usage counter. |
| `smart_lock_manager.reset_sync` | Clear a slot's sync error state and allow a fresh sync. |
| `smart_lock_manager.resize_slots` | Change the number of slots on a lock. |
| `smart_lock_manager.refresh_codes` | Refresh all slots from the lock. |
| `smart_lock_manager.read_zwave_codes` | Read slots directly from the Z-Wave hardware. |
| `smart_lock_manager.sync_slot_to_zwave` | Sync one slot's state to the Z-Wave lock. |
| `smart_lock_manager.get_usage_stats` | Return usage statistics for all slots. |
| `smart_lock_manager.generate_package` | Generate a Home Assistant package YAML for a lock node. |
| `smart_lock_manager.update_lock_settings` | Update a lock's friendly name or slot count. |
| `smart_lock_manager.create_zone` | Create a zone, optionally seeded with unhomed locks. |
| `smart_lock_manager.delete_zone` | Delete a zone, wiping member codes and unhoming them. |
| `smart_lock_manager.add_lock_to_zone` | Move an unhomed lock into a zone and apply its codes. |
| `smart_lock_manager.remove_lock_from_zone` | Remove a lock from its zone and wipe the zone codes. |
| `smart_lock_manager.apply_zone_codes` | Re-push the zone's canonical codes to all members. |
| `smart_lock_manager.clear_zone_codes` | Clear all zone codes and wipe them off every member. |
| `smart_lock_manager.update_zone` | Rename a zone. |
| `smart_lock_manager.update_zone_settings` | Edit a zone's business hours, auto-lock, alerts, and notify config. |
| `smart_lock_manager.set_sweep_intervals` | Set the engine-wide alert-sweep and nag cadences. |
| `smart_lock_manager.pause_alerts` | Snooze alert notifications (global or per-zone) for N hours. |
| `smart_lock_manager.resume_alerts` | Clear an active snooze. |
| `smart_lock_manager.mute_lock_alert` | Permanently mute one lock (optionally one alert type). |
| `smart_lock_manager.unmute_lock_alert` | Re-enable alerts for a muted lock. |
| `smart_lock_manager.update_global_settings` | Update global settings (update interval, auto-disable, logging). |

See [docs/API.md](docs/API.md) for full parameter reference.

## Examples

Set a delivery code that works weekdays from 9 AM to 5 PM, limited to 10 uses:

```yaml
service: smart_lock_manager.set_code_advanced
target:
  entity_id: lock.front_door
data:
  code_slot: 1
  usercode: "1234"
  code_slot_name: "Delivery"
  allowed_hours: [9, 10, 11, 12, 13, 14, 15, 16, 17]
  allowed_days: [0, 1, 2, 3, 4]
  max_uses: 10
  notify_on_use: true
```

Notify when a slot's use count crosses a threshold, reading the summary sensor attributes:

```yaml
automation:
  - alias: "Front door heavy use alert"
    trigger:
      platform: state
      entity_id: sensor.front_door_smart_lock_manager
    condition:
      - "{{ trigger.to_state.attributes.slot_details.slot_1.use_count > 40 }}"
    action:
      - service: notify.mobile_app
        data:
          message: >
            {{ trigger.to_state.attributes.slot_details.slot_1.user_name }}
            has used the front door more than 40 times
```

## Support

- Bugs and feature requests: [GitHub issues](https://github.com/ccsliinc/ha-smart-lock-manager/issues).
- Questions: GitHub Discussions.

## Support this project

If this saves you time, you can chip in:

- [Buy Me A Coffee](https://www.buymeacoffee.com/ccsliinc)
- [PayPal](https://paypal.me/jsugamele)

## License

MIT. See [LICENSE](LICENSE).
