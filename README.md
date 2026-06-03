# Smart Lock Manager for Home Assistant

Manage PIN codes on Z-Wave smart locks (Kwikset, Yale, Schlage, and other locks that
expose user-code management through Z-Wave JS). Set per-slot codes, restrict them by
time of day, day of week, and date range, cap them by number of uses, group locks into
parent-child hierarchies, and keep an access log. All of it runs off a single summary
sensor per lock instead of dozens of entities.

[![HACS Custom](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://github.com/ccsliinc/ha-smart-lock-manager)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Home Assistant](https://img.shields.io/badge/Home%20Assistant-2024.8+-blue.svg)](https://www.home-assistant.io/)
[![Version](https://img.shields.io/badge/Version-2025.1.5-green.svg)](#)
[![Buy Me A Coffee](https://img.shields.io/badge/Buy%20Me%20A%20Coffee-Support-orange.svg?logo=buy-me-a-coffee)](https://www.buymeacoffee.com/ccsliinc)
[![PayPal](https://img.shields.io/badge/PayPal-Donate-blue.svg?logo=paypal)](https://paypal.me/jsugamele)

## Features

- Per-slot PIN codes, named, with up to 50 slots per lock.
- Scheduled access: allowed hours (0-23), allowed days (Mon=0 to Sun=6), and start/end
  date ranges. Codes outside their window are disabled and re-enabled automatically.
- Usage limits: cap a code at a fixed number of uses, then auto-disable it.
- Lock groups: designate a main lock and sync its codes to child locks.
- Access log and per-slot usage counters.
- One summary sensor per lock. All slot state lives in Python objects and is exposed as
  sensor attributes, so your entity list stays clean.
- UI config flow for setup, plus a Configure dialog to change the lock name, lock entity,
  and slot count after install (the entry reloads in place).
- Custom sidebar panel for managing slots, schedules, and usage.

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
wget https://github.com/ccsliinc/ha-smart-lock-manager/archive/refs/tags/v2025.1.5.zip
unzip v2025.1.5.zip
cp -r ha-smart-lock-manager-2025.1.5/custom_components/smart_lock_manager /config/custom_components/
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
| `smart_lock_manager.sync_child_locks` | Sync a parent lock's codes to its child locks. |
| `smart_lock_manager.remove_child_lock` | Remove a child lock from its parent. |
| `smart_lock_manager.get_usage_stats` | Return usage statistics for all slots. |
| `smart_lock_manager.generate_package` | Generate a Home Assistant package YAML for a lock node. |
| `smart_lock_manager.update_lock_settings` | Update a lock's name, slot count, or hierarchy. |
| `smart_lock_manager.update_global_settings` | Update global settings (update interval, auto-disable, logging). |

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
