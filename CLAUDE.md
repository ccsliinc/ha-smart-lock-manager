# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**Smart Lock Manager** is a Home Assistant custom component for advanced Z-Wave lock management with time-based access control, usage tracking, and lock hierarchy management. This project features a revolutionary object-oriented architecture that stores all data in Python objects rather than creating numerous Home Assistant sensors.

## Key Architecture Features

- **Zero Sensor Pollution**: Single summary sensor per lock vs 40+ sensors in traditional components
- **Backend-Driven UI**: All display logic calculated in backend sensor.py, frontend purely presentational
- **Pure Object-Oriented Design**: Uses `SmartLockManagerLock` and `CodeSlot` dataclasses with zero sensor pollution
- **Modular Service Architecture**: Clean separation across 5 service modules (lock, slot, zwave, management, system)
- **Advanced Scheduling**: Time-based access control with allowed hours, days, and date ranges
- **Usage Tracking**: Smart counters with automatic disabling after max uses
- **Lock Hierarchy**: Parent-child lock relationships with automatic synchronization
- **Professional Custom Panel**: Backend-driven UI with real-time updates and material design
- **Rich Automation Integration**: Single summary sensor with comprehensive attributes

## Development Setup

```bash
# Activate virtual environment
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Start Home Assistant for development/testing
./scripts/start_ha_clean.sh

# Run tests
./venv/bin/pytest

# Code quality tools
pre-commit run --all-files
```

## Project Structure

```
custom_components/smart_lock_manager/
├── __init__.py              # Main integration entry point
├── manifest.json           # Component metadata
├── const.py                # Constants and service definitions
├── config_flow.py          # Configuration UI
├── sensor.py               # Backend-driven summary sensor with rich attributes
├── strings.json            # UI strings
├── icon.png                # Component icon
├── models/                 # Object-oriented data models
│   ├── __init__.py
│   └── lock.py             # SmartLockManagerLock and CodeSlot dataclasses
├── services/               # Modular service layer (NEW)
│   ├── __init__.py
│   ├── lock_services.py    # Lock operations (set_code, clear_code, set_code_advanced)
│   ├── slot_services.py    # Slot management (enable, disable, reset, resize)
│   ├── zwave_services.py   # Z-Wave integration (read_codes, sync_to_zwave)
│   ├── management_services.py # Advanced management (sync_child_locks, usage_stats)
│   └── system_services.py  # System operations (generate_package, global_settings)
├── storage/                # Data persistence layer
│   ├── __init__.py
│   └── lock_storage.py     # Storage operations
├── api/                    # HTTP API endpoints
│   ├── __init__.py
│   └── http.py             # HTTP views for frontend assets
├── frontend/               # Custom panel components
│   ├── __init__.py
│   ├── panel.py            # Custom panel registration
│   └── dist/
│       └── smart-lock-manager-panel.js  # Backend-driven panel UI
└── translations/           # UI translations
    └── en.json             # English translations
```

## Recent Architectural Improvements (2025)

### Backend-Driven UI Architecture
The frontend has been completely refactored to be purely presentational:

- **All Display Logic in Backend**: `sensor.py` calculates `display_title`, `slot_status`, `status_color` 
- **No Frontend Logic**: Frontend only consumes backend-calculated fields
- **Real-time Updates**: Backend calculates state, frontend displays immediately
- **Consistent Behavior**: All UI logic centralized in one place

### Modular Service Layer
The monolithic service implementation has been extracted into clean modules:

- **`lock_services.py`**: Core lock operations (set_code, clear_code, advanced)
- **`slot_services.py`**: Slot management (enable, disable, reset, resize)
- **`zwave_services.py`**: Z-Wave integration and synchronization
- **`management_services.py`**: Advanced features (child locks, usage stats)
- **`system_services.py`**: System operations (package generation, global settings)

### UI/UX Improvements
- **Fixed Slot Titles**: "Slot 1: Test User" instead of "Slot 1: 1" 
- **Updated Color System**: Grey (disabled) → Blue (outside hours) → Red (sync error) → Green (synchronized)
- **Modal Auto-close**: Modals automatically close and refresh frontend after service calls
- **Status Priority System**: Clear hierarchy for slot status display

## Advanced Features

### Time-Based Access Control
```python
# CodeSlot with advanced scheduling
@dataclass
class CodeSlot:
    start_date: Optional[datetime] = None     # When access begins
    end_date: Optional[datetime] = None       # When access expires  
    allowed_hours: Optional[List[int]] = None # Hours 0-23 when allowed
    allowed_days: Optional[List[int]] = None  # Days 0-6 (Monday=0)
    max_uses: int = -1                        # Usage limit (-1=unlimited)
    use_count: int = 0                        # Current usage count
    notify_on_use: bool = False               # Trigger notifications
```

### Smart Validation
- **`is_valid_now()`** - Check if slot should be active based on current time and rules
- **`should_disable()`** - Check if slot should be auto-disabled (expired/max uses reached)
- **`increment_usage()`** - Track usage and auto-disable when limits reached

### Lock Hierarchy
```python
@dataclass
class SmartLockManagerLock:
    is_main_lock: bool = True                 # Main lock controls children
    parent_lock_id: Optional[str] = None      # Child locks reference parent
    child_lock_ids: List[str] = []            # Main lock tracks children
    
    def sync_to_child_locks(self, child_locks) # Sync codes to children
```

## Advanced Services

### Code Management
- **`smart_lock_manager.set_code_advanced`** - Set codes with full scheduling
- **`smart_lock_manager.enable_slot`** / **`disable_slot`** - Manual slot control
- **`smart_lock_manager.reset_slot_usage`** - Reset usage counters

### Lock Management  
- **`smart_lock_manager.resize_slots`** - Change slot count (auto-clears higher slots)
- **`smart_lock_manager.sync_child_locks`** - Sync main lock to children
- **`smart_lock_manager.get_usage_stats`** - Comprehensive usage analytics

### Service Example
```yaml
# Set a code that works weekdays 9-5, max 10 uses, with notifications
service: smart_lock_manager.set_code_advanced
target:
  entity_id: lock.front_door
data:
  code_slot: 1
  usercode: "1234"
  code_slot_name: "Delivery Person"
  allowed_hours: [9, 10, 11, 12, 13, 14, 15, 16, 17]
  allowed_days: [0, 1, 2, 3, 4]  # Monday-Friday
  max_uses: 10
  notify_on_use: true
```

## Custom Panel Features

- **Lock Status Dashboard** - Real-time connection and activity status
- **Visual Slot Grid** - Color-coded 10-slot grid showing status at a glance
- **Advanced Code Management** - Modal with scheduling, limits, and notifications
- **Usage Analytics** - Per-user statistics and access patterns
- **Bulk Operations** - Import/export, templates, bulk resets

## Rich Automation Integration

### Summary Sensor Attributes
```python
# Exposed via sensor.smart_lock_manager_*
{
    "active_codes_count": 3,           # Currently active slots
    "valid_codes_count": 2,            # Valid right now (time-based)
    "usage_stats": {
        "total_uses": 47,
        "most_used_slot": 1,
        "expired_slots": 1
    },
    "slot_details": {
        "slot_1": {
            "user_name": "John Doe",
            "is_valid_now": True,
            "use_count": 15,
            "max_uses": 50,
            "allowed_hours": [9, 10, 11, 12, 13]
        }
    }
}
```

### Automation Examples
```yaml
# Notify when high-usage user enters
- alias: "High Usage User Alert"
  trigger:
    platform: state
    entity_id: sensor.front_door_smart_lock_manager
  condition:
    - "{{ trigger.to_state.attributes.slot_details.slot_1.use_count > 40 }}"
  action:
    - service: notify.mobile_app
      data:
        message: "Heavy user John accessed front door"

# Auto-sync child locks when main lock changes
- alias: "Sync Child Locks"
  trigger:
    platform: state
    entity_id: sensor.main_lock_smart_lock_manager
    attribute: active_codes_count
  action:
    - service: smart_lock_manager.sync_child_locks
      target:
        entity_id: lock.main_entrance
```

## Development Commands

```bash
# Start HA with clean logging
./scripts/start_ha_clean.sh

# Create test lock entities
./scripts/create_fake_locks.py

# Check entity status
./scripts/check_entities.py

# Development workflow
pre-commit run --all-files
./venv/bin/pytest
```

## Architecture Innovation

### Zero Sensor Pollution
Unlike traditional HA components that create 40+ sensors per lock (4 sensors × 10 slots), Smart Lock Manager uses a single summary sensor with rich attributes. All data lives in `SmartLockManagerLock` Python objects.

### Advanced Object Methods
```python
# Smart lock management
lock.set_code(slot, pin, name, start_date, end_date, hours, days, max_uses)
lock.get_valid_slots_now()  # Real-time validity checking
lock.check_and_update_slot_validity()  # Auto-disable expired slots
lock.resize_slots(8)  # Dynamic slot management
lock.get_usage_statistics()  # Analytics

# Smart slot validation
slot.is_valid_now()  # Check time/usage rules
slot.should_disable()  # Check if should auto-disable
slot.increment_usage()  # Track usage and auto-disable
```

### Professional Panel Architecture
- **Lit-based UI** (planned upgrade from vanilla JS)
- **Material Design Components** for consistent HA theming
- **Real-time WebSocket Updates** for live status changes
- **Mobile-Responsive Design** for tablets and phones

## Testing Strategy

- **Object-Oriented Unit Tests**: Focus on `CodeSlot` and `SmartLockManagerLock` logic
- **Service Integration Tests**: Test all advanced services with mock locks  
- **Panel UI Tests**: Test frontend functionality and service calls
- **Live Z-Wave Testing**: Use template locks and real Z-Wave networks
- **Automation Testing**: Verify rich attribute exposure for templates

The architecture represents a revolutionary approach to Home Assistant integrations, prioritizing clean object-oriented design, advanced scheduling capabilities, and professional UI over traditional sensor-heavy implementations.

- Any information on syntax or code for Home Assistant, python use the context7 mcp
- long lived access token for home assistant, "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiIzNjc5ZDMwZWI3OTU0MjA4OGMwOTI3YTI4NGVhNDI0NSIsImlhdCI6MTc1NTE0NjcwNSwiZXhwIjoyMDcwNTA2NzA1fQ.ZIZZBw8H2fgQeHhorrFn2f5DvBWGwnOnAu8MVByiSyo"
- the zwave lock is real and connected to a working zwavejs