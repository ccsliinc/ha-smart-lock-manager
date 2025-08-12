# Smart Lock Manager: Architecture & Methodology Guide

## Overview

This document explains the architectural approach and methodology used in the **Smart Lock Manager** Home Assistant custom component. This approach can be adapted for other HA integrations, including sprinkler management systems.

## Core Architectural Philosophy

### The Problem with Traditional HA Component Architecture

Traditional Home Assistant components follow an **entity-heavy approach**:
- Create multiple sensors/entities for each device property
- Store all state in Home Assistant's state machine
- Access data through HA's entity registry and state system
- Results in 10-20+ entities per device (lock slots, status indicators, etc.)

**Problems with this approach:**
- **Entity pollution**: HA UI becomes cluttered with technical entities
- **Performance overhead**: Every data access goes through HA's state machine
- **Complex state management**: Data scattered across multiple entities
- **Harder debugging**: State distributed across many places

### Our Object-Oriented Solution

We implemented an **object-first approach** that stores data in Python objects while still integrating properly with Home Assistant:

- **Centralized data storage**: One Python object per physical device
- **Selective entity creation**: Only create HA entities that users actually need
- **Template base class**: Consistent interface for all component entities
- **Direct data access**: Objects provide immediate access to device state

## Key Architecture Components

### 1. Base Template Entity (`entity.py`)

```python
class KeymasterTemplateEntity(Entity):
    """Base class for all Smart Lock Manager entities."""
    
    def __init__(self, hass, entry, domain, code_slot, name):
        self._hass = hass
        self._lock = hass.data[DOMAIN][entry.entry_id][PRIMARY_LOCK]  # Direct object access
        self._code_slot = code_slot
        # ... entity setup
```

**Key innovations:**
- **Direct object reference**: `self._lock` provides immediate access to lock data
- **Template pattern**: All entities inherit consistent behavior
- **Smart entity creation**: Only creates entities that provide user value
- **Unified interface**: Common methods across all entity types

### 2. Data Models (`lock.py`)

```python
@dataclass
class KeymasterLock:
    """Represents a physical lock configuration."""
    lock_name: str
    lock_entity_id: str
    alarm_level_or_user_code_entity_id: str
    alarm_type_or_access_control_entity_id: str
    door_sensor_entity_id: str
```

**Design principles:**
- **Dataclasses for structure**: Clean, typed data containers
- **Separation of concerns**: Data separate from behavior
- **Configuration-driven**: Easy to extend and modify
- **Type safety**: Full typing throughout

### 3. Business Logic Layer (`services.py`)

```python
async def set_code(hass: HomeAssistant, service_call: ServiceCall) -> None:
    """Set a user code on the lock."""
    # Business logic here - abstracted from Z-Wave details
    # Works with lock objects directly
    lock = get_lock_from_entity_id(service_call.data[ATTR_ENTITY_ID])
    await lock.set_user_code(slot, code)
```

**Key features:**
- **Service-oriented architecture**: Clear separation of business logic
- **Protocol abstraction**: Same interface for different Z-Wave implementations
- **Object manipulation**: Services work with lock objects, not entities
- **Clean APIs**: Simple interfaces for complex operations

### 4. Configuration Management (`const.py`)

```python
# Centralized configuration
DOMAIN = "smart_lock_manager"
PLATFORMS = ["binary_sensor", "sensor"]

# Action mappings for different protocols
ACTION_MAP = {
    ALARM_TYPE: {18: "Keypad Lock", 19: "Keypad Unlock", ...},
    ACCESS_CONTROL: {1: "Manual Lock", 2: "Manual Unlock", ...}
}
```

**Organizational benefits:**
- **Single source of truth**: All configuration in one place
- **Protocol handling**: Maps between different Z-Wave implementations
- **Easy maintenance**: Changes propagate automatically
- **Clear dependencies**: Explicit about what the component needs

## Methodology for Adapting to Other Domains

### Step 1: Identify Core Data Objects

For a **sprinkler system**, identify your core objects:
```python
@dataclass
class SprinklerZone:
    zone_name: str
    zone_entity_id: str
    duration_minutes: int
    schedule_entity_id: str
    moisture_sensor_entity_id: Optional[str]

@dataclass 
class SprinklerController:
    controller_name: str
    zones: List[SprinklerZone]
    master_valve_entity_id: str
    rain_sensor_entity_id: Optional[str]
```

### Step 2: Create Template Base Class

```python
class SprinklerTemplateEntity(Entity):
    """Base for all sprinkler entities."""
    
    def __init__(self, hass, entry, zone_id, name):
        self._hass = hass
        self._controller = hass.data[DOMAIN][entry.entry_id][PRIMARY_CONTROLLER]
        self._zone = self._controller.zones[zone_id]
        # ... rest of setup
        
    def get_zone_state(self):
        """Direct access to zone data - no HA entity lookup needed."""
        return self._zone.current_state
```

### Step 3: Design Selective Entity Creation

**Only create HA entities users actually need:**

For sprinklers, users might need:
- **Binary sensors**: Zone active status, rain detection
- **Sensors**: Time remaining, daily water usage  
- **Switches**: Manual zone control
- **Services**: Start/stop watering, set schedules

**Don't create entities for:**
- Internal state management
- Configuration data
- Intermediate calculations
- Protocol-specific data

### Step 4: Implement Services for Business Logic

```python
async def start_watering(hass: HomeAssistant, service_call: ServiceCall):
    """Start watering a zone."""
    controller = get_controller(service_call.data[ATTR_ENTITY_ID])
    zone = controller.get_zone(service_call.data[ATTR_ZONE])
    duration = service_call.data.get(ATTR_DURATION, zone.default_duration)
    
    # Business logic in objects, not scattered across entities
    await zone.start_watering(duration)
```

## Implementation Benefits

### Performance Benefits
- **Faster data access**: Direct object access vs. HA state lookups
- **Reduced memory usage**: Fewer entities in HA's registry
- **Better responsiveness**: No entity state propagation delays

### Developer Benefits  
- **Cleaner code**: Object-oriented structure is more intuitive
- **Easier testing**: Can test objects independently of HA
- **Better debugging**: Centralized state is easier to inspect
- **Simpler maintenance**: Changes in one place affect whole system

### User Benefits
- **Cleaner UI**: Only relevant entities appear in HA
- **Better performance**: Fewer entities = faster HA
- **More reliable**: Less complex state management = fewer bugs

## Migration Strategy

### From Entity-Heavy to Object-First

1. **Audit existing entities**: Which ones do users actually need?
2. **Create data models**: Design your core object structure
3. **Build template base**: Create your `TemplateEntity` class
4. **Migrate gradually**: Convert entities one by one
5. **Remove unused entities**: Clean up entity registry

### Testing Strategy

```python
# Test objects directly - no HA needed
def test_zone_scheduling():
    zone = SprinklerZone(name="Front Lawn", duration=20)
    schedule = zone.calculate_next_run()
    assert schedule.hour == 6  # Early morning watering

# Test integration with HA when needed  
async def test_entity_creation(hass):
    entity = SprinklerZoneEntity(hass, config_entry, zone_id=1)
    await entity.async_added_to_hass()
    assert entity.state == "off"
```

## Best Practices Summary

1. **Objects first, entities second**: Store data in objects, create entities only for user interaction
2. **Template inheritance**: Use base classes for consistent behavior
3. **Service-oriented business logic**: Keep complex operations in services
4. **Configuration-driven**: Make behavior configurable, not hard-coded
5. **Type everything**: Use full typing for better maintainability
6. **Test objects independently**: Don't require HA for unit testing
7. **Clean separation**: Data models, business logic, and UI concerns separate

## Conclusion

This object-oriented approach results in cleaner, more performant, and more maintainable Home Assistant components. The methodology scales well to any domain - sprinklers, HVAC, security systems, etc. The key is identifying your core data objects and building a clean abstraction layer that works with HA's patterns while avoiding its performance pitfalls.

For your sprinkler project, focus on:
- Modeling zones and controllers as objects
- Creating a `SprinklerTemplateEntity` base class  
- Implementing business logic in services that work with objects
- Only creating HA entities that users actually need to see/control

This approach will give you a much cleaner and more maintainable sprinkler management system.