# Smart Lock Manager API Documentation

This document documents every service, entity, and automation capability provided by
Smart Lock Manager. Every service listed here exists in
`custom_components/smart_lock_manager/services.yaml`.

## Table of Contents

- [Services](#services)
  - [Slot & code services](#slot--code-services)
  - [Z-Wave services](#z-wave-services)
  - [Zone services](#zone-services)
  - [Alerting & auto-lock](#alerting--auto-lock)
  - [Mute & snooze](#mute--snooze)
  - [System services](#system-services)
- [Enabling the engines](#enabling-the-engines)
- [Entities](#entities)
- [Events & automation triggers](#events--automation-triggers)
- [Attributes](#attributes)
- [Automation Examples](#automation-examples)

## Services

All services are in the `smart_lock_manager` domain. Slot/code services target a `lock`
entity; zone services take a `zone_id` string.

### Slot & code services

#### `set_code`

Set a user code on a slot.

- `entity_id` (required) — lock entity.
- `code_slot` (required) — slot number (1-50).
- `usercode` (required) — PIN to set.
- `code_slot_name` (optional) — friendly name for the slot.

#### `set_code_advanced`

Set a user code with scheduling and usage restrictions.

- `entity_id` (required) — lock entity.
- `code_slot` (required) — slot number (1-50).
- `usercode` (required) — PIN to set.
- `code_slot_name` (optional) — friendly name.
- `start_date` (optional) — datetime when the code becomes active.
- `end_date` (optional) — datetime when the code expires.
- `allowed_hours` (optional) — list of hours 0-23 when the code is allowed.
- `allowed_days` (optional) — list of days 0-6 (0=Mon, 6=Sun).
- `max_uses` (optional, default -1) — usage cap (-1 = unlimited).
- `notify_on_use` (optional, default false) — notify when the code is used.

```yaml
service: smart_lock_manager.set_code_advanced
target:
  entity_id: lock.front_door
data:
  code_slot: 1
  usercode: "1234"
  code_slot_name: "Delivery Person"
  allowed_hours: [9, 10, 11, 12, 13, 14, 15, 16, 17]
  allowed_days: [0, 1, 2, 3, 4]
  max_uses: 50
  notify_on_use: true
```

#### `clear_code`

Clear a user code from a slot.

- `entity_id` (required) — lock entity.
- `code_slot` (required) — slot number to clear.

#### `clear_all_slots`

Clear every code slot on a lock.

- `entity_id` (required) — lock entity.

#### `enable_slot` / `disable_slot`

Enable or disable a slot (disable preserves the slot's data).

- `entity_id` (required) — lock entity.
- `code_slot` (required) — slot number.

#### `reset_slot_usage`

Reset the usage counter for a slot.

- `entity_id` (required) — lock entity.
- `code_slot` (required) — slot number.

#### `reset_sync`

Reset a slot's sync state, clearing the error and allowing a fresh sync attempt. Targets
the lock via the standard `target:` selector.

- `target.entity_id` (required) — lock entity.
- `code_slot` (required) — slot number.

#### `resize_slots`

Change the number of available code slots on a lock.

- `entity_id` (required) — lock entity.
- `slot_count` (required) — new slot count (1-50).

#### `get_usage_stats`

Retrieve usage statistics for all slots on a lock.

- `entity_id` (required) — lock entity.

#### `update_lock_settings`

Update a lock's settings.

- `entity_id` (required) — lock entity.
- `friendly_name` (optional) — display name.
- `slot_count` (optional) — number of code slots (1-50).

### Z-Wave services

#### `refresh_codes`

Refresh all code slots from the lock hardware.

- `entity_id` (required) — lock entity.

#### `read_zwave_codes`

Read all code slots directly from the Z-Wave hardware.

- `entity_id` (required) — lock entity.

#### `sync_slot_to_zwave`

Sync a specific slot's state to the Z-Wave hardware.

- `entity_id` (required) — lock entity.
- `code_slot` (required) — slot number.
- `action` (optional, default `auto`) — one of `auto`, `enable`, `disable`.

#### `generate_package`

Generate a Home Assistant package YAML for a lock node.

- `node_id` (required) — the Z-Wave node ID of the lock.

### Zone services

A zone owns the canonical code-slot set; every member lock obeys it. Each lock belongs to
exactly one zone, and locks not yet in a zone sit in the unhomed pool.

#### `create_zone`

Create a new zone, optionally pre-populated with unhomed member locks.

- `name` (required) — display name.
- `member_lock_entity_ids` (optional) — list of unhomed lock entity IDs to add.

#### `delete_zone`

Delete a zone, wiping its codes off every member's hardware and returning members to the
unhomed pool. Lock entries are preserved.

- `zone_id` (required) — zone to delete.

#### `add_lock_to_zone`

Move an unhomed lock into a zone and apply the zone's codes. Rejected if the lock already
belongs to another zone.

- `zone_id` (required) — target zone.
- `lock_entity_id` (required) — unhomed lock to add.

#### `remove_lock_from_zone`

Remove a lock from its zone, wiping the zone's codes off the lock and returning it to the
unhomed pool.

- `zone_id` (required) — zone the lock currently belongs to.
- `lock_entity_id` (required) — member lock to remove.

#### `apply_zone_codes`

Re-push the zone's canonical code set to all member locks (idempotent).

- `zone_id` (required) — zone whose codes should be re-applied.

#### `clear_zone_codes`

Clear every code slot on a zone and wipe those codes off every member's hardware. Members
remain in the zone.

- `zone_id` (required) — zone whose codes should be cleared.

#### `update_zone`

Update a zone's display name.

- `zone_id` (required) — zone to update.
- `name` (required) — new display name.

#### `update_zone_settings`

Edit a zone's operational settings. The `settings` object is merged **per block** — only
the blocks you supply are changed; unspecified blocks are preserved.

- `zone_id` (required) — zone to update.
- `settings` (required) — nested object with any subset of these blocks:
  - `business_hours` — `enabled`, `open_time`, `close_time`, `days` (0-6),
    `use_workday_sensor`, `workday_entity`.
  - `scheduled_auto_lock` — `enabled`, `time`, `days`, `max_attempts`, `settle_seconds`,
    `verify_boltstatus`.
  - `idle_auto_lock` — `enabled`, `minutes`, `sun_aware`, `night_minutes`, `day_minutes`.
  - `alerts` — toggles + thresholds for `outside_hours`, `sustained_unlock`, `jam`,
    `low_battery`, `offline`.
  - `notify` — `email` (`enabled`, `recipients_override`) and `mobile` (`enabled`,
    `targets`).

```yaml
service: smart_lock_manager.update_zone_settings
data:
  zone_id: "<zone_id>"
  settings:
    business_hours:
      enabled: true
      open_time: "08:30"
      close_time: "17:30"
      days: [0, 1, 2, 3, 4]
    alerts:
      outside_hours: { enabled: true, severity: "CRIT" }
      low_battery: { enabled: true, threshold: 20 }
    notify:
      email: { enabled: true }
```

### Alerting & auto-lock

The alert and auto-lock engines are **off by default** and only act once explicitly
enabled — see [Enabling the engines](#enabling-the-engines). The services below tune the
engine cadences.

#### `set_sweep_intervals`

Set the engine-wide periodic alert-sweep and nag cadences (minutes). Changes reschedule
the sweeps live without a Home Assistant restart.

- `outside_hours_sweep_minutes` (optional, 1-1440) — minutes between outside-hours
  boundary sweeps (catches doors left unlocked past close).
- `health_sweep_minutes` (optional, 1-1440) — minutes between jam / low-battery / offline
  health sweeps.
- `nag_interval_minutes` (optional, 1-1440) — minimum minutes between repeated
  timer-origin re-alerts (nags) of the same ongoing episode. State-change alerts and
  recoveries are never throttled.

### Mute & snooze

#### `mute_lock_alert`

Permanently suppress alerts for one lock (optionally one alert type) until unmuted. Not
time-based; silences initial alert, nags, and recovery alike.

- `entity_id` (required) — member lock entity id.
- `alert_type` (optional) — `jam`, `low_battery`, `offline`, `outside_hours`,
  `sustained_unlock`, or `all` (default) for every type.

#### `unmute_lock_alert`

Re-enable alerts for a previously muted lock.

- `entity_id` (required) — member lock entity id.
- `alert_type` (optional) — type to unmute, or `all` (default) to clear every mute.

#### `pause_alerts`

Temporarily suppress alert notifications (alerts are still recorded). Auto-expires. Only
suppresses repeat timer-origin nags; a fresh state-change alert/recovery still notifies.

- `hours` (required, 0.25-24) — how long to snooze.
- `zone_id` (optional) — zone to snooze; omit to snooze ALL zones globally.

#### `resume_alerts`

Clear an active alert snooze (global or per-zone).

- `zone_id` (optional) — zone to resume; omit to clear the global snooze.

### System services

#### `update_global_settings`

Update global component settings.

- `coordinator_interval` (optional) — data update interval: `30`, `60`, `120`, or `300`
  seconds.
- `auto_disable_expired` (optional) — auto-disable expired code slots (boolean).
- `sync_on_lock_events` (optional) — trigger sync when lock state changes (boolean).
- `debug_logging` (optional) — enable debug-level logging (boolean).

## Enabling the engines

The alert / auto-lock / notification engines are gated by three independent flags, all
default OFF. With all off, the engines are never constructed.

| Flag | Flags-file key | Env var | Effect |
| --- | --- | --- | --- |
| Enable engines | `enable_engines` | `SLM_ENABLE_ENGINES` | Construct engines in safe observe / dry-run mode — detect + record alerts, render "would-notify" / "would-auto-lock" intents, send nothing, lock nothing. |
| Real notify | `real_notify` | `SLM_ENABLE_REAL_NOTIFY` | Let recorded alerts actually send (email / mobile). No effect unless engines are enabled. |
| Real auto-lock | `real_autolock` | `SLM_ENABLE_REAL_AUTOLOCK` | Let auto-lock issue a real `lock.lock`. No effect unless engines are enabled. |

A flag is ON if **either** its env var **or** its flags-file key is truthy.

**Flags file (Home Assistant OS).** HA OS cannot set process env vars, so use:

```json
// /config/smart_lock_manager_flags.json
{ "enable_engines": true, "real_notify": false, "real_autolock": false }
```

Env vars accept `1` / `true` / `yes` / `on` (case-insensitive). File changes take effect
on the next prime (HA restart, integration reload, or a zone-settings change), since the
engines are constructed once at setup.

## Entities

### Sensor: `sensor.smart_lock_manager_[lock_name]`

Main summary sensor with comprehensive attributes.

**State Values:**

- `active` — lock has active code slots.
- `inactive` — no active code slots.
- `unavailable` — lock connection issues.

**Key Attributes:**

- `integration`: Always `smart_lock_manager`.
- `lock_entity_id`: Original lock entity ID.
- `lock_name`: Friendly lock name.
- `total_slots`: Total available slots.
- `active_codes_count`: Number of currently active codes.
- `valid_codes_count`: Codes valid right now (time-based).
- `slot_details`: Detailed information for each slot.
- `usage_stats`: Usage analytics and statistics.
- `access_log`: Recent physical lock/unlock/jam events with user attribution.

## Events & automation triggers

Smart Lock Manager does **not** expose a documented, stable public event API for
automations. Build automations off these two stable surfaces instead:

- **The underlying lock entity.** Trigger on the `lock.*` entity's standard Home Assistant
  state (`locked` / `unlocked` / `jammed` / `unavailable`) — the normal, supported way to
  react to physical lock activity.
- **The summary sensor's attributes.** Trigger on
  `sensor.smart_lock_manager_[lock_name]` and read its attributes (see
  [Attributes](#attributes)). The `access_log` attribute holds recent physical
  lock/unlock/jam events with user attribution, and per-slot detail lives in
  `slot_details`.

> The integration does fire internal `hass.bus` signals (e.g.
> `smart_lock_manager_zone_settings_updated` and other zone/coordinator/system refresh
> events) so the custom panel can refresh live. These are **internal, unstable refresh
> signals — not a supported automation API.** Do not key automations off them; their names
> and payloads may change without notice.

## Attributes

### Slot Detail Attributes

Each slot in `slot_details` contains:

- `slot_number`, `user_name`, `pin_code` (masked `****`), `is_active`, `use_count`,
  `max_uses` (-1 = unlimited), `created_at`, `last_used`, `start_date`, `end_date`,
  `allowed_hours`, `allowed_days`, `notify_on_use`, `is_valid_now`, `display_title`, and
  a `status` object (name, label, color, description).

### Usage Statistics Attributes

The `usage_stats` attribute contains aggregate counters such as `total_uses`,
`active_slots`, `expired_slots`, `most_used_slot`, `least_used_slot`,
`average_uses_per_slot`, `last_activity`, and `codes_expiring_soon`.

## Automation Examples

### Notify on a physical unlock

Trigger on the underlying lock entity's standard state change. For richer context (who/how),
read the summary sensor's `access_log` attribute.

```yaml
automation:
  - alias: "Smart Lock Unlocked"
    trigger:
      platform: state
      entity_id: lock.front_door
      to: "unlocked"
    action:
      service: notify.mobile_app
      data:
        title: "Front Door Unlocked"
        message: >-
          Front door unlocked.
          Recent activity: {{ state_attr('sensor.smart_lock_manager_front_door', 'access_log') }}
```

### High-usage alert

```yaml
automation:
  - alias: "High Usage Code Alert"
    trigger:
      platform: state
      entity_id: sensor.front_door_smart_lock_manager
    condition:
      - "{{ trigger.to_state.attributes.slot_details.slot_1.use_count > 40 }}"
    action:
      service: notify.admin_notifications
      data:
        message: "Code slot 1 has high usage - consider reviewing access"
```

### Re-apply zone codes after editing the canonical set

```yaml
automation:
  - alias: "Re-apply zone codes nightly"
    trigger:
      platform: time
      at: "03:00:00"
    action:
      service: smart_lock_manager.apply_zone_codes
      data:
        zone_id: "<zone_id>"
```

### Weekend access control

```yaml
automation:
  - alias: "Enable Weekend Access"
    trigger:
      platform: time
      at: "18:00:00"
    condition:
      condition: time
      weekday:
        - fri
    action:
      service: smart_lock_manager.set_code_advanced
      target:
        entity_id: lock.front_door
      data:
        code_slot: 10
        usercode: "9999"
        code_slot_name: "Weekend Guest"
        allowed_days: [5, 6]
        end_date: "{{ (now() + timedelta(days=3)).strftime('%Y-%m-%dT23:59:59') }}"
```

## Error Handling

All services validate their input and raise `HomeAssistantError` with a clear message on
failure — for example a PIN that collides with an existing code's vendor prefix, an
out-of-range slot number, a lock that isn't found, or adding a lock that already belongs
to another zone. PIN values are never logged in full.

This documentation covers all public services and common usage patterns. For an overview,
see the [main README](../README.md).
