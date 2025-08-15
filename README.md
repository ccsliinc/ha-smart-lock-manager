# Smart Lock Manager

A revolutionary Home Assistant custom component for advanced Z-Wave lock management with zero sensor pollution, time-based access control, and professional UI.

## 🌟 Key Features

- **🚫 Zero Sensor Pollution**: Single summary sensor per lock vs 40+ sensors in traditional components
- **🎯 Backend-Driven UI**: All display logic calculated in backend, frontend purely presentational  
- **⏰ Advanced Scheduling**: Time-based access control with hour/day restrictions and date ranges
- **📊 Usage Tracking**: Smart counters with automatic disabling after max uses
- **🔗 Lock Hierarchy**: Parent-child lock relationships with automatic synchronization
- **🎨 Professional Custom Panel**: Material design interface with real-time updates
- **🏗️ Object-Oriented Architecture**: Clean dataclasses with rich methods and validation

## Installation

### HACS (Recommended)

1. Open HACS in Home Assistant
2. Go to "Integrations"
3. Click the "+" button
4. Search for "Smart Lock Manager"
5. Install the integration
6. Restart Home Assistant

### Manual Installation

1. Copy the `custom_components/smart_lock_manager` folder to your Home Assistant `custom_components` directory
2. Restart Home Assistant

## Configuration

After installation, the integration can be configured through the Home Assistant UI:

1. Go to Settings > Devices & Services
2. Click "Add Integration"
3. Search for "Smart Lock Manager"
4. Select your Z-Wave lock entity
5. Configure slot count and advanced settings
6. Access the custom panel from the sidebar

## 🎮 Usage

### Custom Panel Interface
Navigate to **Smart Lock Manager** in the Home Assistant sidebar to access:

- **Real-time Lock Status**: Connection status and current activity
- **Visual Slot Grid**: Color-coded 10-slot overview showing status at a glance
- **Advanced Code Management**: Modal interface with full scheduling capabilities
- **Usage Analytics**: Per-user statistics and access patterns
- **Bulk Operations**: Import/export, templates, and bulk operations

### Advanced Service Examples

```yaml
# Set a code with time restrictions and usage limits
service: smart_lock_manager.set_code_advanced
target:
  entity_id: lock.front_door
data:
  code_slot: 1
  usercode: "1234"
  code_slot_name: "Delivery Person"
  allowed_hours: [9, 10, 11, 12, 13, 14, 15, 16, 17]  # 9 AM - 5 PM
  allowed_days: [0, 1, 2, 3, 4]  # Monday-Friday
  max_uses: 10
  notify_on_use: true

# Disable a slot (preserves data, removes from physical lock)
service: smart_lock_manager.disable_slot
target:
  entity_id: lock.front_door
data:
  code_slot: 1
```

## 🏗️ Architecture

### Zero Sensor Pollution Design
Unlike traditional components that create 40+ sensors per lock (4 sensors × 10 slots), Smart Lock Manager uses:

- **Single Summary Sensor**: `sensor.smart_lock_manager_[lock_name]` with rich attributes
- **Object-Oriented Storage**: All data lives in `SmartLockManagerLock` Python objects
- **Backend-Driven UI**: All display logic calculated in `sensor.py`, frontend purely presentational

### Modular Service Architecture
```
services/
├── lock_services.py      # Core lock operations
├── slot_services.py      # Slot management  
├── zwave_services.py     # Z-Wave integration
├── management_services.py # Advanced management
└── system_services.py    # System operations
```

## 🛠️ Development

### Prerequisites
- Python 3.11+
- Home Assistant development environment
- Z-Wave JS integration
- pytest for testing
- pre-commit for code quality

### Setup Development Environment

```bash
# Clone the repository
git clone <repository-url>
cd lock_manager

# Create virtual environment
python -m venv venv
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Install pre-commit hooks
pre-commit install

# Start Home Assistant with clean logging
./scripts/start_ha_clean.sh
```

### Running Tests

```bash
# Run all tests
./venv/bin/pytest

# Run tests with coverage
pytest --cov=custom_components.smart_lock_manager

# Run specific test modules
pytest tests/test_models_lock.py
pytest tests/test_services_lock.py
```

## 🚀 What Makes This Different

### Traditional Lock Manager Components:
❌ Creates 40+ sensors per lock (4 sensors × 10 slots)  
❌ Frontend contains business logic  
❌ Monolithic service architecture  
❌ Basic time restrictions  
❌ Entity pollution in Home Assistant  

### Smart Lock Manager:
✅ **Single sensor per lock** with rich attributes  
✅ **Backend-driven UI** with no frontend logic  
✅ **Modular service architecture** with clean separation  
✅ **Advanced scheduling** with hours, days, date ranges, usage limits  
✅ **Zero entity pollution** - clean Home Assistant interface  

## 🔧 Rich Automation Integration

The single summary sensor exposes comprehensive attributes for powerful automations:

```yaml
# Example automation using rich sensor attributes
automation:
  - alias: "Notify on High Usage User"
    trigger:
      platform: state
      entity_id: sensor.front_door_smart_lock_manager
    condition:
      - "{{ trigger.to_state.attributes.slot_details.slot_1.use_count > 40 }}"
    action:
      - service: notify.mobile_app
        data:
          message: "Heavy user {{ trigger.to_state.attributes.slot_details.slot_1.user_name }} accessed front door"
```

## 📄 Documentation

- **[Architecture Diagram](architecture_diagram.md)**: Complete technical architecture overview
- **[CLAUDE.md](CLAUDE.md)**: Development guidance and component details  
- **[HACS Integration](hacs.json)**: Home Assistant Community Store configuration

## 🤝 Support

- **Issues**: [GitHub Issue Tracker](https://github.com/jsugamele/smart_lock_manager/issues)
- **Discussions**: Use GitHub Discussions for questions and community support
- **Contributing**: Pull requests welcome! Please read contribution guidelines first

## 📋 Requirements

- Home Assistant 2023.1+
- Z-Wave JS integration
- Compatible Z-Wave lock (tested with Yale, Schlage, Kwikset)

---

**Smart Lock Manager** - Revolutionizing Home Assistant lock management with zero sensor pollution and professional-grade features.