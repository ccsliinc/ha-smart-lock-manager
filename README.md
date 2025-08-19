# Smart Lock Manager for Home Assistant

**Advanced Z-Wave lock management with revolutionary object-oriented architecture, time-based access control, and zero sensor pollution.**

[![HACS Custom](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://github.com/ccsliinc/ha-smart-lock-manager)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Home Assistant](https://img.shields.io/badge/Home%20Assistant-2024.8+-blue.svg)](https://www.home-assistant.io/)
[![Version](https://img.shields.io/badge/Version-2025.1.0-green.svg)](#)

[![Security Scan](https://img.shields.io/badge/Security%20Scan-Passing-green.svg)](https://bandit.readthedocs.io/)
[![Code Quality](https://img.shields.io/badge/Code%20Quality-A+-brightgreen.svg)](#)
[![Test Coverage](https://img.shields.io/badge/Test%20Coverage-Comprehensive-brightgreen.svg)](#)

---

## 🚀 **Support This Project**

If Smart Lock Manager saves you time and makes your smart home better, consider supporting its development:

[![PayPal](https://img.shields.io/badge/PayPal-Donate-blue.svg?logo=paypal)](https://paypal.me/jsugamele)
[![Buy Me A Coffee](https://img.shields.io/badge/Buy%20Me%20A%20Coffee-Support-orange.svg?logo=buy-me-a-coffee)](https://www.buymeacoffee.com/ccsliinc)

*Your support helps maintain and improve Smart Lock Manager for the entire community!*

---

## ✨ **Revolutionary Features**

### 🔧 **Zero Sensor Pollution Architecture**
- **ONE** summary sensor per lock (vs 40+ in traditional components)
- All data stored in Python objects, not cluttering your entity list
- Rich attributes accessible for advanced automations

### 🏗️ **Advanced Object-Oriented Design**
- `SmartLockManagerLock` and `CodeSlot` dataclasses
- Backend-driven UI with zero frontend logic
- Professional custom panel with Material Design

### ⏰ **Time-Based Access Control**
- **Allowed Hours:** Restrict access to specific times (e.g., 9 AM - 5 PM)
- **Allowed Days:** Weekend/weekday restrictions (Monday=0, Sunday=6)
- **Date Ranges:** Start and end dates for temporary access
- **Usage Limits:** Auto-disable after X uses
- **Smart Validation:** Real-time checking with automatic disabling

### 🏢 **Lock Hierarchy Management**
- **Parent-Child Relationships:** Main locks control multiple child locks
- **Automatic Synchronization:** Changes to parent sync to all children
- **Centralized Management:** Manage entire lock groups from one interface
- **🔗 Lock Hierarchy**: Parent-child lock relationships with automatic synchronization
- **🎨 Professional Custom Panel**: Material design interface with real-time updates
- **🏗️ Object-Oriented Architecture**: Clean dataclasses with rich methods and validation

## 📦 Installation

### Method 1: HACS (Recommended)

**📋 Requirements:**
- Home Assistant 2024.8.0 or newer
- HACS (Home Assistant Community Store) installed
- Z-Wave JS integration configured with lock entities

**🔧 Installation Steps:**

1. **Add Custom Repository** (if not in HACS default):
   - Open HACS in Home Assistant
   - Go to "Integrations" 
   - Click the three dots menu → "Custom repositories"
   - Add: `https://github.com/ccsliinc/ha-smart-lock-manager`
   - Category: "Integration"

2. **Install Integration**:
   - Search for "Smart Lock Manager" in HACS
   - Click "Download"
   - Restart Home Assistant

3. **Configure Integration**:
   - Go to Settings → Devices & Services
   - Click "Add Integration"
   - Search for "Smart Lock Manager"
   - Follow the configuration wizard

### Method 2: Manual Installation

```bash
# Download latest release
wget https://github.com/ccsliinc/ha-smart-lock-manager/archive/refs/tags/v2025.1.0.zip
unzip v2025.1.0.zip

# Copy to Home Assistant
cp -r ha-smart-lock-manager-2025.1.0/custom_components/smart_lock_manager /config/custom_components/

# Restart Home Assistant
```

### Method 3: Git Clone (For Developers)

```bash
cd /config/custom_components
git clone https://github.com/ccsliinc/ha-smart-lock-manager.git
mv ha-smart-lock-manager/custom_components/smart_lock_manager ./
rm -rf ha-smart-lock-manager
```

## ⚙️ Configuration

### Initial Setup Wizard

1. **Add Integration:**
   - Settings → Devices & Services → "Add Integration"
   - Search: "Smart Lock Manager"

2. **Select Lock Entity:**
   - Choose your Z-Wave lock from the dropdown
   - Supported: Z-Wave JS locks with user code management

3. **Configure Settings:**
   - **Lock Name**: Friendly name for the lock
   - **Slot Count**: Number of user code slots (typically 1-30)
   - **Start Slot**: First slot number (usually 1)

4. **Access Panel:**
   - Navigate to "Smart Lock Manager" in the sidebar
   - Complete setup with the visual interface

### Advanced Configuration

**🔧 Lock Hierarchy (Optional):**
- Set up parent-child relationships for multiple locks
- Sync codes automatically across lock groups
- Centralized management from main lock

**⏰ Global Settings (Optional):**
- Auto-disable expired codes
- Sync on lock events
- Debug logging level

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
- **[API Documentation](docs/API.md)**: Complete service and automation reference
- **[HACS Integration](hacs.json)**: Home Assistant Community Store configuration

## 🤝 Support

- **Issues**: [GitHub Issue Tracker](https://github.com/ccsliinc/ha-smart-lock-manager/issues)
- **Discussions**: Use GitHub Discussions for questions and community support
- **Contributing**: Pull requests welcome! Please read contribution guidelines first

## 📋 Requirements

- Home Assistant 2023.1+
- Z-Wave JS integration
- Compatible Z-Wave lock (tested with Yale, Schlage, Kwikset)

---

**Smart Lock Manager** - Revolutionizing Home Assistant lock management with zero sensor pollution and professional-grade features.
