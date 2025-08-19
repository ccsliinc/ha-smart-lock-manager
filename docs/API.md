# Smart Lock Manager API Documentation

This document provides comprehensive documentation for all services, entities, and automation capabilities provided by Smart Lock Manager.

## Table of Contents

- [Services](#services)
- [Entities](#entities)
- [Events](#events)
- [Attributes](#attributes)
- [Automation Examples](#automation-examples)

## Services

Smart Lock Manager provides comprehensive lock and slot management through Home Assistant services.

### Core Lock Services

#### `smart_lock_manager.set_code_advanced`

Set a user code with advanced scheduling and restrictions.

**Parameters:**
- `entity_id` (required): Target lock entity ID
- `code_slot` (required): Slot number (1-30)
- `usercode` (required): 4-8 digit PIN code
- `code_slot_name` (required): Display name for the user
- `start_date` (optional): Access start date/time (ISO format)
- `end_date` (optional): Access end date/time (ISO format)
- `allowed_hours` (optional): List of allowed hours [0-23]
- `allowed_days` (optional): List of allowed days [0-6] (Monday=0)
- `max_uses` (optional): Maximum usage count (-1 = unlimited)
- `notify_on_use` (optional): Enable usage notifications (default: false)

**Example:**
```yaml
service: smart_lock_manager.set_code_advanced
target:
  entity_id: lock.front_door
data:
  code_slot: 1
  usercode: "1234"
  code_slot_name: "Delivery Person"
  allowed_hours: [9, 10, 11, 12, 13, 14, 15, 16, 17]  # 9 AM - 5 PM
  allowed_days: [0, 1, 2, 3, 4]  # Monday-Friday
  start_date: "2025-01-01T00:00:00"
  end_date: "2025-12-31T23:59:59"
  max_uses: 50
  notify_on_use: true
```

#### `smart_lock_manager.clear_code`

Remove a user code from a specific slot.

**Parameters:**
- `entity_id` (required): Target lock entity ID
- `code_slot` (required): Slot number to clear

**Example:**
```yaml
service: smart_lock_manager.clear_code
target:
  entity_id: lock.front_door
data:
  code_slot: 1
```

### Slot Management Services

#### `smart_lock_manager.enable_slot`

Enable a previously disabled slot (preserves PIN and settings).

**Parameters:**
- `entity_id` (required): Target lock entity ID
- `code_slot` (required): Slot number to enable

#### `smart_lock_manager.disable_slot`

Disable a slot (removes from lock but preserves data).

**Parameters:**
- `entity_id` (required): Target lock entity ID
- `code_slot` (required): Slot number to disable

#### `smart_lock_manager.reset_slot_usage`

Reset the usage counter for a slot.

**Parameters:**
- `entity_id` (required): Target lock entity ID
- `code_slot` (required): Slot number to reset

#### `smart_lock_manager.resize_slots`

Change the total number of available slots.

**Parameters:**
- `entity_id` (required): Target lock entity ID
- `slot_count` (required): New total slot count

### Z-Wave Integration Services

#### `smart_lock_manager.read_codes`

Read all codes directly from the Z-Wave lock.

**Parameters:**
- `entity_id` (required): Target lock entity ID

#### `smart_lock_manager.sync_to_zwave`

Force synchronization of all codes to the Z-Wave lock.

**Parameters:**
- `entity_id` (required): Target lock entity ID

### Advanced Management Services

#### `smart_lock_manager.sync_child_locks`

Synchronize codes from parent lock to all child locks.

**Parameters:**
- `entity_id` (required): Parent lock entity ID

#### `smart_lock_manager.get_usage_stats`

Retrieve comprehensive usage statistics.

**Parameters:**
- `entity_id` (required): Target lock entity ID

### System Services

#### `smart_lock_manager.update_global_settings`

Update global component settings.

**Parameters:**
- `auto_disable_expired` (optional): Auto-disable expired codes (boolean)
- `sync_on_lock_events` (optional): Sync when lock events occur (boolean)
- `debug_logging` (optional): Enable debug logging (boolean)

## Entities

### Sensor: `sensor.smart_lock_manager_[lock_name]`

Main summary sensor with comprehensive attributes.

**State Values:**
- `active` - Lock has active code slots
- `inactive` - No active code slots
- `unavailable` - Lock connection issues

**Key Attributes:**
- `integration`: Always "smart_lock_manager"
- `lock_entity_id`: Original lock entity ID
- `lock_name`: Friendly lock name
- `total_slots`: Total available slots
- `active_codes_count`: Number of currently active codes
- `valid_codes_count`: Codes valid right now (time-based)
- `slot_details`: Detailed information for each slot
- `usage_stats`: Usage analytics and statistics

## Events

### `smart_lock_manager_codes_read`

Fired when Z-Wave codes are successfully read.

**Event Data:**
- `entity_id`: Lock entity that was read
- `codes`: Dictionary of slot numbers and code data
- `total_found`: Number of codes found
- `timestamp`: When the read occurred

### `smart_lock_manager_lock_state_changed`

Fired when lock state changes are detected.

**Event Data:**
- `entity_id`: Lock entity ID
- `action_code`: Z-Wave action code
- `action_text`: Human-readable action description
- `code_slot_name`: User name (if applicable)

## Attributes

### Slot Detail Attributes

Each slot in the `slot_details` attribute contains:

- `slot_number`: Slot position (1-30)
- `user_name`: Display name for the user
- `pin_code`: Masked PIN code ("****")
- `is_active`: Whether slot is currently active
- `use_count`: Number of times used
- `max_uses`: Maximum allowed uses (-1 = unlimited)
- `created_at`: When slot was created
- `last_used_at`: Last usage timestamp
- `start_date`: Access start date/time
- `end_date`: Access end date/time
- `allowed_hours`: List of allowed hours
- `allowed_days`: List of allowed days
- `notify_on_use`: Whether notifications are enabled
- `is_valid_now`: Whether code is valid right now
- `display_title`: Frontend display title
- `status`: Current status object with name, label, color, description

### Usage Statistics Attributes

The `usage_stats` attribute contains:

- `total_uses`: Total usage count across all slots
- `active_slots`: Number of currently active slots
- `expired_slots`: Number of expired slots
- `most_used_slot`: Slot number with highest usage
- `least_used_slot`: Slot number with lowest usage
- `average_uses_per_slot`: Average usage per active slot
- `last_activity`: Timestamp of last code usage
- `codes_expiring_soon`: List of slots expiring within 7 days

## Automation Examples

### Basic Usage Notifications

```yaml
automation:
  - alias: "Smart Lock Code Used"
    trigger:
      platform: event
      event_type: smart_lock_manager_lock_state_changed
    condition:
      condition: template
      value_template: "{{ trigger.event.data.action_text == 'Manual Unlock' }}"
    action:
      service: notify.mobile_app
      data:
        title: "🔓 Front Door Unlocked"
        message: "{{ trigger.event.data.code_slot_name }} used code slot {{ trigger.event.data.code_slot }}"
```

### High Usage Alert

```yaml
automation:
  - alias: "High Usage Code Alert"
    trigger:
      platform: state
      entity_id: sensor.front_door_smart_lock_manager
    condition:
      condition: template
      value_template: >
        {% set slots = trigger.to_state.attributes.slot_details %}
        {% for slot_num, slot in slots.items() %}
          {% if slot.use_count > 40 %}
            true
          {% endif %}
        {% endfor %}
    action:
      service: notify.admin_notifications
      data:
        message: "Code slot has high usage - consider reviewing access"
```

### Expired Code Cleanup

```yaml
automation:
  - alias: "Clean Up Expired Codes"
    trigger:
      platform: time
      at: "02:00:00"  # Daily at 2 AM
    action:
      - service: python_script.cleanup_expired_codes
        data:
          lock_entity: "lock.front_door"
      - service: notify.admin_notifications
        data:
          message: "Daily expired code cleanup completed"
```

### Lock Synchronization

```yaml
automation:
  - alias: "Sync Child Locks When Parent Changes"
    trigger:
      platform: state
      entity_id: sensor.main_lock_smart_lock_manager
      attribute: active_codes_count
    action:
      service: smart_lock_manager.sync_child_locks
      target:
        entity_id: lock.main_entrance
```

### Weekend Access Control

```yaml
automation:
  - alias: "Enable Weekend Access"
    trigger:
      platform: time
      at: "18:00:00"  # Friday 6 PM
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
        allowed_days: [5, 6]  # Saturday, Sunday
        end_date: "{{ (now() + timedelta(days=3)).strftime('%Y-%m-%dT23:59:59') }}"
```

### Advanced Template Usage

```yaml
# Get all slots expiring in next 7 days
template:
  - sensor:
      name: "Codes Expiring Soon"
      state: >
        {% set slots = states.sensor.front_door_smart_lock_manager.attributes.slot_details %}
        {% set expiring = [] %}
        {% for slot_num, slot in slots.items() %}
          {% if slot.end_date %}
            {% set end_date = strptime(slot.end_date, '%Y-%m-%dT%H:%M:%S') %}
            {% if end_date < (now() + timedelta(days=7)) %}
              {% set expiring = expiring + [slot.user_name] %}
            {% endif %}
          {% endif %}
        {% endfor %}
        {{ expiring | length }}
      attributes:
        expiring_users: >
          {% set slots = states.sensor.front_door_smart_lock_manager.attributes.slot_details %}
          {% set expiring = [] %}
          {% for slot_num, slot in slots.items() %}
            {% if slot.end_date %}
              {% set end_date = strptime(slot.end_date, '%Y-%m-%dT%H:%M:%S') %}
              {% if end_date < (now() + timedelta(days=7)) %}
                {% set expiring = expiring + [slot.user_name] %}
              {% endif %}
            {% endif %}
          {% endfor %}
          {{ expiring }}
```

## Error Handling

### Service Call Validation

All services validate input parameters and return appropriate errors:

- **Invalid PIN Code**: Must be 4-8 digits, numeric only
- **Invalid Slot Number**: Must be within configured range
- **Invalid Date Format**: Must be ISO format (YYYY-MM-DDTHH:MM:SS)
- **Lock Not Found**: Entity ID must exist and be accessible
- **Z-Wave Communication**: Automatic retry with exponential backoff

### Automation Error Handling

```yaml
automation:
  - alias: "Safe Code Setting"
    trigger:
      platform: event
      event_type: call_service
    action:
      - service: smart_lock_manager.set_code_advanced
        target:
          entity_id: lock.front_door
        data:
          code_slot: 1
          usercode: "1234"
          code_slot_name: "Test User"
        continue_on_error: true
      - service: notify.admin_notifications
        data:
          message: >
            {% if wait.trigger %}
              Code set successfully
            {% else %}
              Failed to set code - check logs
            {% endif %}
```

## Performance Considerations

### Batch Operations

For multiple slot operations, use individual service calls rather than loops:

```yaml
# Preferred: Individual calls
script:
  setup_delivery_codes:
    sequence:
      - service: smart_lock_manager.set_code_advanced
        target:
          entity_id: lock.front_door
        data:
          code_slot: 1
          usercode: "1111"
          code_slot_name: "UPS"
      - service: smart_lock_manager.set_code_advanced
        target:
          entity_id: lock.front_door
        data:
          code_slot: 2
          usercode: "2222"
          code_slot_name: "FedEx"
```

### Caching and Updates

- Sensor attributes update automatically when changes occur
- Z-Wave synchronization is performed asynchronously
- Use `smart_lock_manager.read_codes` sparingly (every few hours max)
- Child lock synchronization is optimized for bulk operations

This documentation covers all public APIs and common usage patterns for Smart Lock Manager. For additional examples and advanced configurations, see the [main README](../README.md) and [examples directory](../examples/).