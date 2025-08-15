# Development Scripts Guide

## 🚀 Quick Start

```bash
# Test everything works
./scripts/test_component.sh

# Start Home Assistant for development
./scripts/start_ha.sh
```

## 📋 Available Scripts

### `setup_dev.sh` 
**One-time setup for development environment**
- Creates virtual environment and installs dependencies
- Installs pre-commit hooks
- Creates necessary directories
- Run this once when starting development

### `test_component.sh` ⭐ **Always run this first**
**Test component before starting HA**
- Tests Python syntax across all component files
- Verifies imports work correctly
- Validates manifest.json structure
- Catches basic issues before HA startup

### `start_ha.sh` ⭐ **Recommended for development**
**Start Home Assistant with optimized logging and automatic cleanup**
- Uses Home Assistant's native logger configuration
- Shows Smart Lock Manager logs at DEBUG level
- Quiets noisy components (HTTP, websockets, etc.)
- Much more reliable than shell filtering approaches
- Shows setup and config entry logs for debugging integration loading

## 🎯 Development Workflow

1. **First time setup**: `./scripts/setup_dev.sh`
2. **Before each session**: `./scripts/test_component.sh` 
3. **Start development**: `./scripts/start_ha.sh`
4. **Make changes** to component code
5. **Restart HA** to see changes (Ctrl+C, then rerun script)

## 🔧 Logging Configuration

The clean logging approach uses `config/configuration.yaml`:

```yaml
logger:
  default: warning  # Quiet by default
  logs:
    custom_components.smart_lock_manager: debug  # Our component
    homeassistant.config_entries: info          # Setup logs
    homeassistant.loader: info                  # Loading logs
    homeassistant.setup: info                   # Setup logs
```

This gives you:
- **All Smart Lock Manager logs** at debug level
- **Integration loading progress** 
- **Clean, focused output** without noise
- **Native HA logging** (no fragile shell filtering)

## 🐛 Troubleshooting

**Component won't load?**
1. Run `./scripts/test_component.sh` first
2. Check the startup logs for error messages
3. Verify manifest.json has all required fields

**Can't see your logs?**
- Smart Lock Manager logs appear with `[custom_components.smart_lock_manager]` prefix
- Loading issues show under `[homeassistant.loader]`
- Setup issues show under `[homeassistant.config_entries]`

**Need to reset environment?**
- Delete `venv/` folder and rerun `./scripts/setup_dev.sh`
- Delete `config/home-assistant_v2.db*` files to reset HA state

## 💡 Pro Tips

- **Use the Home Assistant UI** at http://localhost:8123 to configure integrations
- **Check Settings → Integrations** to see if Smart Lock Manager appears
- **Look for the component** in Settings → System → Logs for detailed debug info
- **Restart HA completely** after code changes (component reloading isn't reliable)

## 🎨 What You'll See

With clean logging, expect output like:
```
INFO [homeassistant.loader] Loaded smart_lock_manager from custom_components.smart_lock_manager
INFO [homeassistant.setup] Setting up smart_lock_manager
DEBUG [custom_components.smart_lock_manager] Smart Lock Manager Version 1.0.0 starting up
```

Much cleaner than before!