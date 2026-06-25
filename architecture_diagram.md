# Smart Lock Manager Architecture

> **Note (v2026.7.0):** The detailed ASCII diagram below predates the **zone refactor**
> and the **opt-in engine layers**. It is kept for its accurate frontend → HTTP API →
> service → model → coordinator → sensor → Z-Wave flow, but the following has changed:
>
> - **Parent/child is gone.** A `Zone` (`models/zone.py`) is now the canonical owner of
>   the code-slot set; member locks mirror it. Each lock belongs to exactly one zone;
>   unhomed locks sit in a pool. The `management_services.sync_child_locks` box below is
>   replaced by the **zone services** (`zone_services.py`: create / delete / add / remove
>   / apply / clear / update, plus `zone_settings_service.py` for `update_zone_settings`).
> - **New opt-in engine layer** sits beside the service layer: `alert_engine.py`
>   (OBSERVE-only health detection + recording), `auto_lock.py` (scheduled + idle), and
>   the `notifications*` dispatcher. All three are gated by `gating.py` and are NOT
>   constructed unless `is_dev_mock() OR engines_enabled()`. Three flags
>   (`enable_engines` / `real_notify` / `real_autolock`) from
>   `/config/smart_lock_manager_flags.json` or `SLM_ENABLE_*` env vars control them.
> - **New storage:** `zone_storage.py`, `muted.py` (per-lock mute), `snooze.py`
>   (global/per-zone snooze), `alert_storage.py` (rolling alert log).

## Overview
Smart Lock Manager is a Home Assistant custom component that features an object-oriented
architecture with zero sensor pollution, a zone-based canonical code model, opt-in
alerting / auto-lock engines, and a professional custom panel interface.

## Architectural Principles

### 1. **Zero Sensor Pollution Architecture**
- **Single Summary Sensor**: Creates only ONE sensor per lock with rich attributes
- **Object-Oriented Data Storage**: All data lives in Python `SmartLockManagerLock` objects
- **No Entity Spam**: Eliminates 40+ sensors per lock (4 sensors × 10 slots) used by traditional components

### 2. **Backend-Driven UI**
- **No Frontend Logic**: All business logic calculated in backend sensor.py
- **Pure Presentation Layer**: Frontend only displays backend-calculated display fields
- **Real-time Updates**: Backend calculates colors, status text, and display titles

### 3. **Advanced Scheduling System**
- **Time-Based Access Control**: Hour/day restrictions, date ranges
- **Usage Limits**: Max uses with automatic disabling
- **Smart Validation**: Real-time validity checking with `is_valid_now()` methods

## Architecture Flow Diagram

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│                           SMART LOCK MANAGER INTEGRATION                        │
└─────────────────────────────────────────────────────────────────────────────────┘

┌─────────────────┐    ┌─────────────────┐    ┌─────────────────┐
│   USER BROWSER  │    │  HOME ASSISTANT │    │   Z-WAVE LOCK   │
│                 │    │     CORE        │    │    HARDWARE     │
└─────────────────┘    └─────────────────┘    └─────────────────┘
         │                       │                       │
         ▼                       ▼                       ▼

┌─────────────────────────────────────────────────────────────────────────────────┐
│                              FRONTEND LAYER                                     │
├─────────────────────────────────────────────────────────────────────────────────┤
│  ┌─────────────────────────────────────────────────────────────────────────┐   │
│  │                     CUSTOM PANEL UI                                     │   │
│  │  /frontend/dist/smart-lock-manager-panel.js                            │   │
│  │                                                                         │   │
│  │  ┌───────────┐  ┌───────────┐  ┌───────────┐  ┌───────────────────┐  │   │
│  │  │Lock Status│  │Slot Grid  │  │Advanced   │  │Usage Analytics    │  │   │
│  │  │Dashboard  │  │(10 slots) │  │Code Mgmt  │  │& Bulk Operations  │  │   │
│  │  └───────────┘  └───────────┘  └───────────┘  └───────────────────┘  │   │
│  │                                                                         │   │
│  │  NEW Color Priority System:                                            │   │
│  │  🟢 Green = Synchronized    🔵 Blue = Outside Hours                   │   │
│  │  🔴 Red = Sync Error       ⚪ Gray = Disabled/Empty                  │   │
│  └─────────────────────────────────────────────────────────────────────────┘   │
│                                     │                                           │
│                        ┌─────────────────────────────┐                        │
│                        │     WebSocket Updates        │                        │
│                        │   (Real-time sync status)    │                        │
│                        └─────────────────────────────────┘                        │
└─────────────────────────────────────────────────────────────────────────────────┘
                                     │
                                     ▼ HTTP API Calls
┌─────────────────────────────────────────────────────────────────────────────────┐
│                               HTTP API LAYER                                    │
├─────────────────────────────────────────────────────────────────────────────────┤
│  ┌─────────────────────────────────────────────────────────────────────────┐   │
│  │                         /api/http.py                                    │   │
│  │                                                                         │   │
│  │  GET  /api/smart_lock_manager/locks     → List all locks               │   │
│  │  GET  /api/smart_lock_manager/lock/{id} → Get lock details             │   │
│  │  POST /api/smart_lock_manager/service   → Execute services             │   │
│  │                                                                         │   │
│  └─────────────────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────────────────┘
                                     │
                                     ▼ Service Calls
┌─────────────────────────────────────────────────────────────────────────────────┐
│                           MODULAR SERVICE LAYER                                 │
├─────────────────────────────────────────────────────────────────────────────────┤
│  ┌─────────────────────────────────────────────────────────────────────────┐   │
│  │                      /services/ (Modular Architecture)                 │   │
│  │                                                                         │   │
│  │  ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────────────┐ │   │
│  │  │ lock_services   │  │ slot_services   │  │ zwave_services          │ │   │
│  │  │                 │  │                 │  │                         │ │   │
│  │  │ - set_code()    │  │ - enable_slot() │  │ - read_zwave_codes()    │ │   │
│  │  │ - clear_code()  │  │ - disable_slot()│  │ - sync_slot_to_zwave()  │ │   │
│  │  │ - set_code_     │  │ - reset_usage() │  │ - refresh_codes()       │ │   │
│  │  │   advanced()    │  │ - resize_slots()│  │                         │ │   │
│  │  └─────────────────┘  └─────────────────┘  └─────────────────────────┘ │   │
│  │                                                                         │   │
│  │  ┌─────────────────┐  ┌─────────────────┐                             │   │
│  │  │management_      │  │ system_services │  ┌─────────────────────────┐│   │
│  │  │services         │  │                 │  │ zone_services           ││   │
│  │  │                 │  │ - generate_     │  │ + zone_settings_service ││   │
│  │  │ - get_usage_    │  │   package()     │  │                         ││   │
│  │  │   stats()       │  │ - update_global │  │ - create/delete_zone()  ││   │
│  │  │ - update_lock_  │  │   _settings()   │  │ - add/remove_lock()     ││   │
│  │  │   settings()    │  │ - pause/resume  │  │ - apply/clear_codes()   ││   │
│  │  │ - clear_all_    │  │ - mute/unmute   │  │ - update_zone_settings()││   │
│  │  │   slots()       │  │ - sweep_intervals│ └─────────────────────────┘│   │
│  │  └─────────────────┘  └─────────────────┘                             │   │
│  └─────────────────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────────────────┘
                                     │
                                     ▼ Model Operations
┌─────────────────────────────────────────────────────────────────────────────────┐
│                               DATA LAYER                                        │
├─────────────────────────────────────────────────────────────────────────────────┤
│  ┌─────────────────────────────────────────────────────────────────────────┐   │
│  │                      /models/lock.py                                    │   │
│  │                                                                         │   │
│  │  ┌─────────────────────────────────────────────────────────────────┐   │   │
│  │  │                    SmartLockManagerLock                         │   │   │
│  │  │                                                                 │   │   │
│  │  │  Properties: lock_name, entity_id, slots, settings             │   │   │
│  │  │  Methods:    set_code(), get_valid_slots_now(),                │   │   │
│  │  │              check_and_update_slot_validity()                  │   │   │
│  │  │                                                                 │   │   │
│  │  │  ┌─────────────────────────────────────────────────────────┐   │   │   │
│  │  │  │                    CodeSlot (1-10)                      │   │   │   │
│  │  │  │                                                         │   │   │   │
│  │  │  │  Properties: pin_code, user_name, is_active            │   │   │   │
│  │  │  │             allowed_days, allowed_hours                │   │   │   │
│  │  │  │             start_date, end_date, max_uses              │   │   │   │
│  │  │  │                                                         │   │   │   │
│  │  │  │  Methods:    is_valid_now(), should_disable(),          │   │   │   │
│  │  │  │             increment_usage()                          │   │   │   │
│  │  │  └─────────────────────────────────────────────────────────┘   │   │   │
│  │  └─────────────────────────────────────────────────────────────────┘   │   │
│  └─────────────────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────────────────┘
                                     │
                                     ▼ Sensor Updates
┌─────────────────────────────────────────────────────────────────────────────────┐
│                            COORDINATION LAYER                                   │
├─────────────────────────────────────────────────────────────────────────────────┤
│  ┌─────────────────────────────────────────────────────────────────────────┐   │
│  │              DataUpdateCoordinator (30-second intervals)                │   │
│  │                                                                         │   │
│  │  ┌─────────────────┐    ┌─────────────────┐    ┌─────────────────┐    │   │
│  │  │ Real-time Sync  │    │ Auto-disable    │    │ Z-Wave Status   │    │   │
│  │  │ Status Updates  │    │ Expired Slots   │    │ Monitoring      │    │   │
│  │  └─────────────────┘    └─────────────────┘    └─────────────────┘    │   │
│  └─────────────────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────────────────┘
                                     │
                                     ▼ Attribute Updates
┌─────────────────────────────────────────────────────────────────────────────────┐
│                              SENSOR LAYER                                       │
├─────────────────────────────────────────────────────────────────────────────────┤
│  ┌─────────────────────────────────────────────────────────────────────────┐   │
│  │                        /sensor.py                                       │   │
│  │                                                                         │   │
│  │  sensor.smart_lock_manager_front_door                                   │   │
│  │                                                                         │   │
│  │  Rich Attributes for Automations:                                       │   │
│  │  {                                                                      │   │
│  │    "active_codes_count": 3,                                            │   │
│  │    "valid_codes_count": 2,                                             │   │
│  │    "slot_details": {                                                   │   │
│  │      "slot_1": {                                                       │   │
│  │        "user_name": "John Doe",                                        │   │
│  │        "is_active": true,                                              │   │
│  │        "is_valid_now": true,                                           │   │
│  │        "use_count": 15,                                                │   │
│  │        "allowed_days": [0,1,2,3,4]                                     │   │
│  │      }                                                                 │   │
│  │    }                                                                   │   │
│  │  }                                                                     │   │
│  └─────────────────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────────────────┘
                                     │
                                     ▼ Z-Wave Commands
┌─────────────────────────────────────────────────────────────────────────────────┐
│                              Z-WAVE LAYER                                       │
├─────────────────────────────────────────────────────────────────────────────────┤
│  ┌─────────────────────────────────────────────────────────────────────────┐   │
│  │                    Z-Wave JS Integration                                │   │
│  │                                                                         │   │
│  │  ┌───────────────────┐    ┌───────────────────┐    ┌───────────────┐  │   │
│  │  │ get_usercode_     │    │ set_usercode()    │    │ clear_        │  │   │
│  │  │ from_node()       │    │                   │    │ usercode()    │  │   │
│  │  │                   │    │ - Send PIN to     │    │               │  │   │
│  │  │ - Read actual     │    │   physical lock   │    │ - Remove PIN  │  │   │
│  │  │   PINs from lock  │    │ - Update slot     │    │   from lock   │  │   │
│  │  └───────────────────┘    └───────────────────┘    └───────────────┘  │   │
│  └─────────────────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────────────────┘
                                     │
                                     ▼ Physical Operations
┌─────────────────────────────────────────────────────────────────────────────────┐
│                            PHYSICAL LOCK                                        │
├─────────────────────────────────────────────────────────────────────────────────┤
│  ┌─────────────────────────────────────────────────────────────────────────┐   │
│  │                        Z-Wave Lock Device                                │   │
│  │                                                                         │   │
│  │  Physical lock stores 10 user codes in slots 1-10                      │   │
│  │  Reports lock/unlock events with slot numbers                           │   │
│  │  Accepts PIN programming commands via Z-Wave                            │   │
│  │                                                                         │   │
│  │  Lock States: LOCKED, UNLOCKED, JAMMED, UNKNOWN                        │   │
│  │  Events: Manual, Keypad, RF, Auto lock/unlock                          │   │
│  └─────────────────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────────────────┐
│                                DATA FLOW                                        │
├─────────────────────────────────────────────────────────────────────────────────┤
│                                                                                 │
│  USER ACTION (Frontend) → HTTP API → Service Call → Model Update →             │
│  Coordinator Refresh → Sensor Attributes → Frontend Update                     │
│                                                                                 │
│  PHYSICAL LOCK EVENT → Z-Wave JS → Event Handler → Model Update →              │
│  Coordinator Refresh → Sensor Attributes → Frontend Update                     │
│                                                                                 │
│  SCHEDULED TASK (30s) → Coordinator → Model Validation → Auto-disable →       │
│  Sensor Update → Frontend Sync Status                                          │
│                                                                                 │
└─────────────────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────────────────┐
│                             RECENT IMPROVEMENTS                                 │
├─────────────────────────────────────────────────────────────────────────────────┤
│                                                                                 │
│  ✅ COMPLETED: Backend-Driven UI Architecture                                  │
│     - Moved ALL display logic to backend sensor.py                            │
│     - Frontend is now purely presentational                                   │
│     - Added display_title, slot_status, status_color fields                   │
│                                                                                 │
│  ✅ COMPLETED: Service Layer Modularization                                    │
│     - Extracted monolithic __init__.py into modular /services/ directory      │
│     - Clean separation: lock, slot, zwave, management, system services        │
│                                                                                 │
│  ✅ COMPLETED: File Structure Cleanup                                          │
│     - Removed 13 obsolete files from old keymaster implementation             │
│     - Clean, professional directory structure following HA best practices     │
│                                                                                 │
│  ✅ COMPLETED: UI/UX Improvements                                              │
│     - Fixed "Slot 1: 1" → "Slot 1: Test User" display format                 │
│     - Updated color priority system (Grey→Blue→Red→Green)                     │
│     - Added modal auto-close and frontend refresh                             │
│                                                                                 │
│  🟢 COMPLETE: Object-Oriented Architecture                                     │
│     Zero sensor pollution - all data in Python objects                         │
│     Single summary sensor with rich attributes                                 │
│                                                                                 │
│  🟢 COMPLETE: Advanced Time-Based Access Control                               │
│     allowed_days, allowed_hours, date ranges, usage limits                     │
│                                                                                 │
└─────────────────────────────────────────────────────────────────────────────────┘
```

## Key Integration Points to Review Next:

### 1. **Service Layer Refactoring**
- **Current Issue**: 971-line `__init__.py` contains all service logic
- **Target**: Extract to modular `services/` directory
- **Priority**: High - will improve maintainability

### 2. **Real-Time Z-Wave Synchronization**
- **Current**: 30-second coordinator updates
- **Enhancement**: Event-driven sync on lock state changes
- **Priority**: Medium - improve responsiveness

### 3. **Frontend Panel Enhancement**
- **Current**: Vanilla JavaScript implementation
- **Target**: Lit-based components with Material Design
- **Priority**: Low - functional but could be modernized

### 4. **Automation Integration**
- **Current**: Single sensor with rich attributes
- **Enhancement**: Additional template sensors for complex automations
- **Priority**: Low - current approach is working well

The architecture now represents a mature, production-ready Home Assistant custom component with industry-best practices implemented throughout.

## Current File Structure

```
custom_components/smart_lock_manager/
├── __init__.py                 # Main integration entry point
├── manifest.json              # Component metadata
├── config_flow.py             # Configuration UI
├── const.py                   # Constants and definitions
├── sensor.py                  # Summary sensor (backend-driven UI)
├── strings.json               # UI strings
├── icon.png                   # Component icon
├── models/                    # Object-oriented data models
│   ├── __init__.py
│   └── lock.py               # SmartLockManagerLock & CodeSlot classes
├── services/                  # Modular service layer (NEW)
│   ├── __init__.py
│   ├── lock_services.py      # Lock operations
│   ├── slot_services.py      # Slot management
│   ├── zwave_services.py     # Z-Wave integration
│   ├── management_services.py # Advanced management
│   └── system_services.py    # System operations
├── storage/                   # Data persistence layer
│   ├── __init__.py
│   └── lock_storage.py       # Storage operations
├── api/                       # HTTP API endpoints
│   ├── __init__.py
│   └── http.py               # HTTP views for frontend
├── frontend/                  # Custom panel components
│   ├── __init__.py
│   ├── panel.py              # Panel registration
│   └── dist/
│       └── smart-lock-manager-panel.js  # Panel UI (backend-driven)
└── translations/              # Internationalization
    └── en.json               # English translations
```

## Key Architectural Achievements

1. **Zero Sensor Pollution**: Single sensor per lock vs 40+ sensors in traditional components
2. **Backend-Driven UI**: All display logic calculated in sensor.py, frontend purely presentational
3. **Modular Services**: Clean separation of concerns across 5 service modules
4. **Object-Oriented Core**: Data lives in Python classes, not Home Assistant entities
5. **Professional Structure**: Follows Home Assistant best practices and conventions
