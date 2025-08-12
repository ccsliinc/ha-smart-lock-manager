# Development Scripts Guide

## 🚀 Quick Start

```bash
# Test everything works
./scripts/test_component.sh

# Start Home Assistant (full logs)
./scripts/start_ha.sh

# Start with filtered logs (recommended)
./scripts/start_ha_filtered.sh
```

## 📋 Available Scripts

### `setup_dev.sh`
- Sets up the complete development environment
- Creates virtual environment, installs dependencies
- Installs pre-commit hooks
- One-time setup script

### `test_component.sh` 
- Tests Python syntax across all component files
- Verifies imports work correctly
- Validates manifest.json
- **Run this first** to catch basic issues

### `start_ha.sh`
- Starts Home Assistant with full debug logging
- Sets up symlinks automatically  
- Shows ALL log output (can be overwhelming)

### `start_ha_filtered.sh` ⭐ **Recommended**
- Starts HA with filtered, colored logs
- Shows only: Loader, Smart Lock Manager, Config, Errors, Warnings
- **Best for development** - easier to spot issues

### `debug_component.sh`
- Shows ONLY Smart Lock Manager and error logs
- Most focused debugging experience
- Great for troubleshooting specific component issues

### `watch_logs.sh`
- Watches HA logs in real-time (separate terminal)
- Useful when HA is already running
- Filtered and color-coded output

## 🎨 Log Color Coding

- 🔴 **Red**: Errors and Critical issues
- 🟡 **Yellow**: Warnings
- 🟢 **Green**: Smart Lock Manager logs
- 🔵 **Cyan**: Setup and configuration logs
- ⚪ **White**: General information

## 💡 Development Workflow

1. **First time setup**: `./scripts/setup_dev.sh`
2. **Test component**: `./scripts/test_component.sh` 
3. **Start development**: `./scripts/start_ha_filtered.sh`
4. **In another terminal**: `./scripts/watch_logs.sh` (optional)
5. **Make changes** to component code
6. **Restart HA** to see changes (Ctrl+C, then rerun script)

## 🐛 Troubleshooting

**Component won't load?**
- Run `./scripts/test_component.sh` first
- Check for Python syntax errors
- Verify all imports are available

**Can't see your changes?**
- Restart Home Assistant completely
- Check symlinks: `ls -la config/custom_components/`
- Verify you're editing the right files

**Logs too noisy?**
- Use `./scripts/debug_component.sh` for minimal output
- Use `./scripts/start_ha_filtered.sh` for balanced view

**Need to reset environment?**
- Delete `venv/` folder and rerun `./scripts/setup_dev.sh`
- Remove `config/` folder to reset HA configuration