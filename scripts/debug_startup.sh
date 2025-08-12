#!/bin/bash
# Debug Home Assistant startup with better filtering

set -e

echo "🐛 Debugging Home Assistant Startup"
echo "=================================="

# Setup symlinks
mkdir -p config/custom_components
find config/custom_components -type l -delete

if [ -d "custom_components/smart_lock_manager" ]; then
    ln -sf "$(pwd)/custom_components/smart_lock_manager" "config/custom_components/smart_lock_manager"
    echo "✅ Symlink created"
fi

export PYTHONPATH="$(pwd):$PYTHONPATH"

echo "🚀 Starting HA and showing first 50 lines of output..."
echo ""

# Run HA and show initial output, then filter
./venv/bin/python -m homeassistant --config config --debug 2>&1 | \
    (
        # Show first 20 lines unfiltered to see startup
        head -20
        echo ""
        echo "--- Switching to filtered mode ---"
        echo ""
        # Then filter the rest
        grep -E "(smart_lock_manager|Smart Lock Manager|ERROR|WARNING|CRITICAL|custom_components)" --line-buffered | head -20
    )