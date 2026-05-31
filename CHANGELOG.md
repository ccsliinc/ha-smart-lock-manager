# Changelog

All notable changes to Smart Lock Manager will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [2025.1.4] - 2026-05-31 - Fix access-log unsub registry pollution crash

### Fixed
- **`set_code_advanced`/`clear_code` no longer crash with
  `AttributeError: 'functools.partial' object has no attribute 'get'`**: the
  2025.1.2 access-log feature stored the `zwave_js_notification` listener's
  unsub callback (a `functools.partial`) inside `hass.data[DOMAIN]`, which is
  the per-config-entry registry that many loops iterate expecting only entry
  dicts. When a loop reached the stray `_access_log_unsub` key it called
  `.get(PRIMARY_LOCK)` on the partial and raised. The unsub is now stored in a
  separate `smart_lock_manager_runtime` namespace
  (`hass.data["smart_lock_manager_runtime"]["access_log_unsub"]`) so it never
  pollutes the entry registry. Setup and unload were both updated; teardown
  still unsubscribes correctly on last-entry unload.
- **Defensive registry-iteration guards**: the two remaining unguarded loops in
  `lock_services.py` (`set_code_advanced`, `clear_code`) now `continue` on any
  non-dict `entry_data`, so a stray non-dict entry can never crash these paths
  again. All other registry loops were already guarded.

## [2025.1.3] - 2026-05-30 - Metadata-Only Edits & Per-Lock Access Log

### Fixed
- **Username/metadata-only edits no longer trigger a Z-Wave re-write**: the
  panel's `saveSlotSettings()` now detects when the PIN is unchanged from the
  stored value and skips the chained `sync_slot_to_zwave` call, so editing only
  a slot's username/scheduling no longer re-issues a `set_lock_usercode` (which
  could surface a transient Kwikset error). The backend `set_code_advanced`
  service was hardened to match: when the incoming PIN equals the slot's stored
  PIN it performs an in-place metadata update (user_name/dates/hours/days/
  max_uses/notify) without clearing `is_synced`, so no spurious physical write
  occurs even if a caller redundantly re-sends the existing PIN.

### Added
- **Per-lock access-log attribution**: `add_access_log_entry()` now records
  `lock_name` (friendly name), `lock_entity_id`, and `role` ("parent"/"child",
  derived from `parent_lock_id`) on every entry so events carry their source
  door. The summary sensor surfaces these fields automatically.
- **Parent cards aggregate child-lock events**: the panel `renderAccessLog()`
  merges a parent lock's own log with its child locks' logs into one
  time-sorted timeline and badges each row with the originating door
  ("Front North" vs "Rear Entrance"). Standalone locks show no badge. Legacy
  entries lacking `lock_name` fall back gracefully to the card's lock name.

## [2025.1.2] - 2026-05-28 - Access Log with User Attribution

### Added
- **Lock/unlock access log**: SLM now records physical lock events (lock,
  unlock, jam) to a per-lock, persistent `access_log`. Keypad events resolve
  the Z-Wave `parameters.userId` to the slot's `user_name` (falling back to
  `slot N`); manual/RF/auto events are logged with a source label and no user.
- Global `zwave_js_notification` event listener registered in
  `async_setup_entry` (one subscription serves all locks; the handler resolves
  the target lock by matching `node_id` to each managed lock's Z-Wave node).
  The unsubscribe callback is torn down cleanly in `async_unload_entry`.
- `SmartLockManagerLock.add_access_log_entry()` — bounded append helper that
  retains only the most recent `ACCESS_LOG_MAX_ENTRIES` (100) entries so
  `.storage` cannot grow unbounded. The log is serialized in `to_dict()` and
  restored on startup.
- `map_access_control_event()` — pure, testable mapping of Kwikset Access
  Control event codes (1/2 manual, 3/4 RF, 5/6 keypad+userId, 9 auto, 11 jam)
  to `{action, source}`.
- `access_log` surfaced on the summary sensor's `extra_state_attributes`
  (most-recent-first, latest 25; full 100 kept in storage).
- **Panel Access Log section**: collapsible per-lock card rendering the event
  table (local-time timestamp, lock/unlock/jam icon, and attribution such as
  "Joe (slot 1)", "Thumbturn", "App/Remote", or "Auto-lock").

### Security
- Access-log entries store only `user_name` and slot number — **never** PIN
  codes.

## [2025.1.1] - 2026-05-27 - Kwikset Prefix-Collision Guard

### Fixed
- **Kwikset silent-drop bug**: Kwikset Z-Wave deadbolts (918 and most 9xx-series)
  silently discard `set_lock_usercode` writes when the new PIN shares its first
  4 digits with any existing code on the lock. The device ACKs the write but
  never enables the code, so `is_synced` stays false and the coordinator burns
  retries forever.

### Added
- `find_prefix_conflict()` helper in `models/lock.py` — pure, testable function
  that returns the conflicting slot (or `None`) for a candidate PIN.
- Per-lock `code_collision_prefix_length` setting (default `4`; set `0` to disable).
- `validation_rejections` counter on `CodeSlot` — increments on pre-write
  rejections so they are distinguishable from real Z-Wave `sync_attempts`.
- Pre-write validation hooks in `lock_services.set_code`, `set_code_advanced`,
  and `zwave_services.sync_slot_to_zwave` (the auto-sync coordinator path).
- Conflict reason is written to `slot.sync_error` so the UI surfaces it.
- Service calls raise `HomeAssistantError` with a clear message naming the
  conflicting slot, its user name, and the offending prefix. PIN values are
  never logged in full — only the prefix.

### Tests
- Six new unit tests in `tests/test_models_lock.py::TestPrefixConflict` cover:
  basic conflict, no conflict, same-slot update, inactive-slot ignore,
  empty/None PIN, and short-PIN edge cases.

## [2025.1.0] - 2025-08-19 - First Public Release

### 🎉 **MAJOR MILESTONE: First Public Release**

This release represents the complete transformation of Smart Lock Manager from a development prototype into a production-ready, security-hardened, professionally-architected Home Assistant component ready for public use and HACS submission.

### 🏗️ **Phase 1: Critical Foundation**

#### Added
- **Professional pyproject.toml**: Complete project metadata with build system, security tools, and dependency management
- **Production .gitignore**: Comprehensive exclusions for build artifacts, security scans, and development files
- **Clean Project Structure**: Removed IDE configs, cache files, and development artifacts

#### Changed
- **Development Documentation**: Development instructions moved to proper documentation structure
- **File Organization**: Eliminated duplicate directories and temporary files

### 🏆 **Phase 2: HACS Preparation & Versioning**

#### Added
- **HACS Compliance**: Complete hacs.json with proper domains, ZIP release configuration
- **CalVer Versioning**: Implemented 2025.1.0 versioning across all components
- **Manifest Enhancement**: Added zwave_js dependency, integration_type, and HACS required fields

#### Changed
- **Version System**: Migrated from SemVer to CalVer (2025.1.0 format)
- **Documentation URLs**: Updated to proper GitHub repository structure
- **Integration Metadata**: Enhanced for professional distribution

### 🔒 **Phase 3: Security & Quality Infrastructure**

#### Added
- **Bandit Security Scanning**: Python security vulnerability detection
- **Safety Dependency Scanning**: Third-party vulnerability monitoring
- **Vulture Dead Code Detection**: Code quality and optimization analysis
- **Enhanced Pre-commit Hooks**: Comprehensive validation with security checks
- **PIN Validation Security**: Prevents logging of sensitive data
- **Architecture Validation**: TODO comment checking and keymaster reference removal

#### Security Scan Results
- ✅ **Bandit**: Only 1 low-severity issue (2,871 lines scanned)
- ✅ **Safety**: Zero vulnerabilities in 221 dependencies
- ✅ **Code Quality**: A+ grade with minimal issues

### 🏗️ **Phase 4: Revolutionary Frontend Architecture**

#### Added
- **Professional Build System**: Rollup-based with ESLint, Prettier, and Terser
- **Modular Source Structure**: Split 3,229-line monolith into 9 focused modules:
  - `ServiceClient.js`: Home Assistant API communication
  - `DataManager.js`: State and data management
  - `FormValidator.js`: Input validation and user feedback
  - `Constants.js`, `DateUtils.js`, `DOMUtils.js`: Utility modules
- **Development Workflow**: npm scripts for build, dev, lint, and format
- **Production Optimization**: Minification, source maps, and cache busting

#### Removed
- **Debug Interfaces**: ~600 lines of debug code and test functionality removed
- **Console Logging**: All debug console statements cleaned for production
- **Development UI**: Toggle buttons and debug panels eliminated

#### Changed
- **Architecture**: From monolithic to modular with clean separation of concerns
- **Build Process**: From manual to automated with professional tooling

### 📊 **Phase 5: Comprehensive Testing Infrastructure**

#### Added
- **60+ New Test Cases** across 4 comprehensive test suites:
  - `test_sensor.py`: Complete sensor functionality testing
  - `test_config_flow.py`: Configuration flow validation
  - `test_services_slot.py`: Slot management service testing
  - `test_security.py`: Security-focused test cases with injection attack prevention
- **Coverage Reporting**: pytest-cov integration with HTML reports
- **Security Testing**: Input validation, PIN sanitization, and access control testing

#### Changed
- **Test Coverage**: Dramatically improved from basic to comprehensive
- **Quality Assurance**: Professional testing practices with security focus

### 📚 **Phase 6: Professional Documentation**

#### Added
- **Comprehensive README**: Production installation instructions with HACS integration
- **API Documentation**: Complete service and automation reference in `docs/API.md`
- **Security Badges**: Visual indicators for security scan status and code quality
- **Installation Methods**: HACS, manual, and developer installation options
- **Version Badges**: Current version and compatibility indicators

#### Changed
- **Documentation Structure**: From development-focused to user-focused
- **Installation Instructions**: Professional multi-method approach
- **API Reference**: Complete service catalog with examples

### 🛠️ **Technical Improvements**

#### Dependencies
- **Security Tools**: bandit>=1.7.0, safety>=2.0.0, vulture>=2.0.0
- **Testing**: pytest-cov>=4.0.0 for coverage reporting
- **Build Tools**: Complete frontend development stack

#### Configuration
- **Pre-commit Hooks**: 8 comprehensive validation checks
- **Build Configuration**: Rollup with environment-specific optimizations
- **Coverage Settings**: 80-90% coverage targets with exclusion rules

### 🔧 **Compatibility & Requirements**

#### System Requirements
- **Home Assistant**: 2024.8.0+ (tested and validated)
- **Python**: 3.11+ (type hints and modern features)
- **Z-Wave**: Z-Wave JS integration required
- **HACS**: Community Store compatibility

#### Browser Support
- **Modern Browsers**: ES6+ support required for frontend
- **Mobile Responsive**: Touch-friendly interface design
- **Accessibility**: ARIA compliance and keyboard navigation

### 🚀 **Migration & Upgrade Notes**

#### For New Users
- Follow HACS installation instructions
- Use configuration wizard for setup
- Access custom panel from sidebar

#### For Existing Users
- **No Breaking Changes**: Full backward compatibility maintained
- **Automatic Migration**: Existing configurations preserved
- **Enhanced Features**: New capabilities available immediately

### 🔮 **Future Roadmap**

This release establishes the foundation for:
- **Enhanced Security**: Ongoing vulnerability monitoring
- **Advanced Features**: Additional scheduling options
- **Performance Optimization**: Further speed improvements
- **Community Growth**: HACS default repository inclusion

### 📊 **Release Statistics**

- **Files Changed**: 25 core files modified
- **Lines Added**: 2,816 new lines of code and documentation
- **Lines Removed**: 401 lines of debug and legacy code
- **Test Cases**: 60+ comprehensive test scenarios
- **Security Issues**: Minimal (1 low-severity finding)
- **Dependencies**: All secure and up-to-date

This release represents **6 months of intensive development** and **complete architectural transformation**, delivering a world-class Home Assistant integration ready for widespread adoption.

---

### 🎯 **Previous Development History**

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
- [GitHub Repository](https://github.com/ccsliinc/ha-smart-lock-manager)
- [Issue Tracker](https://github.com/ccsliinc/ha-smart-lock-manager/issues)
- [Documentation](https://github.com/ccsliinc/ha-smart-lock-manager)
