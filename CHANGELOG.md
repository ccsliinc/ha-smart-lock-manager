# Changelog

All notable changes to Smart Lock Manager will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- Comprehensive pull request templates and guidelines
- GitHub Actions workflow for automated PR validation
- Enhanced pre-commit hooks with security and architecture checks
- CHANGELOG.md file for tracking project changes
- PayPal and Buy Me A Coffee donate buttons in README

### Changed
- Updated LICENSE to MIT with Commercial Restriction
- Enhanced README with comprehensive project documentation
- Improved developer attribution throughout codebase

### Fixed
- Backend friendly name functionality working correctly
- Data persistence for friendly names across HA restarts
- Comprehensive slot data restoration from storage

## [1.0.0] - 2025-08-15

### Added
- Revolutionary zero sensor pollution architecture
- Advanced time-based access control with schedules
- Lock hierarchy management (parent-child relationships)
- Professional custom panel with Material Design
- Backend-driven UI with zero frontend logic
- Modular service architecture across 5 service modules
- Rich automation integration with comprehensive attributes
- Usage tracking and analytics
- Smart PIN code validation and synchronization
- Real-time Z-Wave status monitoring

### Features
- **Core Architecture**
  - Single summary sensor per lock (vs 40+ in traditional components)
  - Object-oriented data storage in Python classes
  - Backend-calculated display fields (colors, status, titles)
  - Persistent data storage with Home Assistant Store

- **Advanced Scheduling**
  - Allowed hours (e.g., 9 AM - 5 PM access)
  - Allowed days (weekday/weekend restrictions)
  - Date ranges for temporary access
  - Usage limits with automatic disabling
  - Smart validation with `is_valid_now()` checking

- **Lock Management**
  - Parent-child lock relationships
  - Automatic code synchronization
  - Centralized management interface
  - Friendly name support for custom display names

- **Services**
  - `smart_lock_manager.set_code_advanced` - Full scheduling capabilities
  - `smart_lock_manager.enable_slot` / `disable_slot` - Manual control
  - `smart_lock_manager.reset_slot_usage` - Usage counter resets
  - `smart_lock_manager.resize_slots` - Dynamic slot management
  - `smart_lock_manager.sync_child_locks` - Hierarchy synchronization
  - `smart_lock_manager.get_usage_stats` - Analytics and reporting

- **Custom Panel**
  - Real-time lock status dashboard
  - Visual 10-slot grid with color-coded status
  - Advanced code management modal
  - Usage analytics and patterns
  - Bulk operations support

- **Security & Quality**
  - PIN code masking in logs
  - Input validation and sanitization
  - Error handling and recovery
  - Comprehensive test coverage
  - Pre-commit hooks for code quality

### Technical Achievements
- **Zero Entity Pollution**: Eliminates sensor spam in Home Assistant
- **Backend-Driven UI**: All logic in Python, frontend purely presentational
- **Modular Services**: Clean separation across lock/slot/zwave/management/system
- **Professional Structure**: Follows Home Assistant integration best practices
- **Rich Attributes**: Single sensor exposes all data for complex automations

### Requirements
- Home Assistant 2023.1+
- Z-Wave JS integration
- Compatible Z-Wave lock (Yale, Schlage, Kwikset tested)

---

## Version History

### Architecture Evolution

**v1.0.0**: Revolutionary object-oriented architecture
- Introduced zero sensor pollution concept
- Backend-driven UI philosophy
- Modular service layer
- Professional custom panel

### Contributing

When adding entries to this changelog:
1. Add new entries under [Unreleased]
2. Use standard categories: Added, Changed, Deprecated, Removed, Fixed, Security
3. Include brief but clear descriptions
4. Link to issues/PRs when applicable
5. Move entries to versioned sections on releases

### Links
- [GitHub Repository](https://github.com/jsugamele/smart_lock_manager)
- [Issue Tracker](https://github.com/jsugamele/smart_lock_manager/issues)
- [Documentation](https://github.com/jsugamele/smart_lock_manager)