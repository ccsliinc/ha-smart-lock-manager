# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**Smart Lock Manager** is a Home Assistant custom component for advanced Z-Wave lock management. This project is based on the original keymaster integration but features a cleaner object-oriented architecture that stores data in Python objects rather than creating numerous Home Assistant sensors.

## Key Architecture Features

- **Object-Oriented Design**: Uses `SmartLockManagerLock` objects and `SmartLockManagerTemplateEntity` base classes
- **Clean Data Management**: Stores state in Python objects instead of polluting HA with excessive sensors
- **Advanced PIN Management**: Time-based access, user limits, scheduling, and automation integration
- **Z-Wave Integration**: Supports both OpenZWave and Z-Wave JS

## Development Setup

```bash
# Activate virtual environment
source venv/bin/activate  # or ./venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Start Home Assistant for development/testing
python dev_start.py

# Run tests
./venv/bin/pytest

# Code quality tools
black custom_components/
flake8 custom_components/
mypy custom_components/
```

## Project Structure

```
custom_components/smart_lock_manager/
├── __init__.py              # Main integration setup
├── manifest.json           # Component metadata
├── const.py                # Constants and configuration
├── config_flow.py          # Configuration UI
├── entity.py               # Base entity classes (KEY ARCHITECTURE)
├── lock.py                 # Lock data classes  
├── binary_sensor.py        # Binary sensors (pin sync, active status)
├── sensor.py               # Sensors (code slots, connection status)
├── services.py             # Services (set/clear codes, refresh, etc.)
├── helpers.py              # Utility functions
├── smart_lock_manager*.yaml # Template files for package generation
└── translations/           # UI translations
```

## Key Development Commands

```bash
# Test environment
python test_environment.py

# Start HA with component
python dev_start.py

# Run specific tests
./venv/bin/pytest tests/test_config_flow.py

# Code formatting
pre-commit run --all-files
```

## Architecture Philosophy & Innovation

### Core Innovation: Object-Oriented Data Management
The fundamental architectural innovation is the **`KeymasterTemplateEntity`** base class in `entity.py` that stores and manages lock data within Python objects instead of creating numerous Home Assistant sensors. This approach:

- **Reduces sensor pollution**: Instead of 10+ sensors per lock slot, data lives in objects
- **Improves performance**: Direct object access vs. HA state machine queries
- **Cleaner code**: Centralized data management with clear inheritance hierarchy
- **Better maintainability**: Single source of truth for lock state and configuration

### Key Architecture Components

1. **`entity.py`** - The heart of the OOP approach:
   - `KeymasterTemplateEntity`: Base class for all entities
   - Handles state management, entity registration, and data access
   - Provides consistent interface across all component entities

2. **`lock.py`** - Data models:
   - `KeymasterLock`: Dataclass representing physical lock configuration
   - Clean separation between data and behavior

3. **`const.py`** - Configuration and constants:
   - Centralized configuration management
   - Action mappings for different Z-Wave implementations
   - Event constants for automation integration

4. **`services.py`** - Business logic:
   - Lock code management (set, clear, refresh)
   - Package file generation for HA configuration
   - Z-Wave integration abstraction

### Development Principles

- **Object-Oriented First**: Data lives in objects, not HA entities when possible
- **Clean Separation**: Models, views, services clearly separated
- **Configuration-Driven**: Template-based approach for extensibility
- **Z-Wave Agnostic**: Abstraction layer supports multiple Z-Wave integrations

### Modern HA Patterns Used

- Config flow for GUI-based setup
- DataUpdateCoordinator for efficient data management
- Service registration for external integrations
- Event-driven architecture for automation
- Proper async/await patterns throughout

## Common Development Tasks

```bash
# Run linting and fix code quality
pre-commit run --all-files

# Test specific functionality
./venv/bin/pytest tests/test_entity.py -v

# Debug with live HA instance
python dev_start.py  # Check logs in HA UI

# Add new lock entity type
# 1. Add to PLATFORMS in const.py
# 2. Create platform file (e.g., switch.py)  
# 3. Inherit from KeymasterTemplateEntity
# 4. Register in __init__.py async_setup_entry

# Update Z-Wave integration
# See services.py for Z-Wave JS vs OpenZWave handling patterns
```

## Testing Strategy

- **Unit tests**: Focus on entity logic and data management
- **Integration tests**: Test with mock Z-Wave devices
- **Live testing**: Use dev_start.py with real Z-Wave network
- **Code quality**: Pre-commit hooks enforce standards

The architecture prioritizes clean object-oriented design over Home Assistant's typical entity-heavy approach, resulting in more maintainable and performant lock management.