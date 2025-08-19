# Smart Lock Manager Frontend

This directory contains the modular frontend build system for the Smart Lock Manager component.

## Architecture

### Modular Structure
- **src/main.js** - Entry point and component registration
- **src/components/** - Reusable UI components
- **src/modules/** - Business logic modules
- **src/templates/** - HTML template functions
- **src/utils/** - Utility functions

### Build System
- **Rollup** - Module bundler optimized for ES modules
- **ESLint** - Code linting and style enforcement
- **Prettier** - Code formatting
- **Terser** - Production minification

## Development

```bash
# Install dependencies
npm install

# Development build with watch mode
npm run dev

# Production build
npm run build:prod

# Lint code
npm run lint

# Format code
npm run format

# Clean build artifacts
npm run clean
```

## Build Modes

### Development Mode
- Source maps enabled
- No minification
- Console logs preserved
- Fast rebuilds

### Production Mode
- Minified output
- Source maps disabled
- Console logs removed
- Optimized for size

## File Structure

```
src/
├── main.js                    # Entry point
├── components/
│   ├── SmartLockPanel.js     # Main panel component
│   ├── SlotModal.js          # Slot configuration modal
│   ├── SettingsModal.js      # Lock settings modal
│   └── LoadingSpinner.js     # Loading indicators
├── modules/
│   ├── DataManager.js        # State and data management
│   ├── ServiceClient.js      # Home Assistant API client
│   ├── LockHierarchy.js      # Parent-child lock logic
│   └── FormValidator.js      # Form validation logic
├── templates/
│   ├── PanelTemplate.js      # Main panel HTML
│   ├── ModalTemplates.js     # Modal HTML templates
│   └── SlotGridTemplate.js   # Slot grid rendering
└── utils/
    ├── DateUtils.js          # Date formatting utilities
    ├── DOMUtils.js           # DOM manipulation helpers
    └── Constants.js          # Shared constants
```

## Integration

The build system automatically copies the compiled bundle to the correct location for Home Assistant integration:

- **Source**: `dist/smart-lock-manager-panel.js`
- **Target**: `../../../smart-lock-manager-panel.js`

The Home Assistant component loads this file via the panel registration system.