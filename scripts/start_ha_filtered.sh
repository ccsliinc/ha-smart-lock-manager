#!/bin/bash
# Start Home Assistant with filtered logging for development

set -e

echo "🏠 Starting Home Assistant with Filtered Logging"
echo "==============================================="

# Check if virtual environment exists
if [ ! -d "venv" ]; then
    echo "❌ Virtual environment not found. Run: ./scripts/setup_dev.sh"
    exit 1
fi

# Setup symlinks (same as start_ha.sh)
echo "🔗 Setting up custom component symlinks..."
mkdir -p config/custom_components
find config/custom_components -type l -delete

if [ -d "custom_components/smart_lock_manager" ]; then
    ln -sf "$(pwd)/custom_components/smart_lock_manager" "config/custom_components/smart_lock_manager"
    echo "✅ Created symlink: config/custom_components/smart_lock_manager"
else
    echo "❌ Smart Lock Manager component not found"
    exit 1
fi

export PYTHONPATH="$(pwd):$PYTHONPATH"

echo "🚀 Starting Home Assistant with filtered logs..."
echo "📊 Showing only: Loader, Smart Lock Manager, Config, Errors, and Warnings"
echo "💡 Open http://localhost:8123 in your browser"
echo "⚠️  Press Ctrl+C to stop"
echo ""

# Start HA and filter logs for relevant information
./venv/bin/python -m homeassistant --config config --debug 2>&1 | grep -E \
    "(INFO|WARNING|ERROR|CRITICAL)" | grep -E \
    "(homeassistant.loader|smart_lock_manager|config_entries|setup|custom_components|platform|integration|ERROR|WARNING|CRITICAL)" | \
    while IFS= read -r line; do
        # Color coding for different log levels
        if echo "$line" | grep -q "ERROR\|CRITICAL"; then
            echo -e "\033[31m$line\033[0m"  # Red for errors
        elif echo "$line" | grep -q "WARNING"; then
            echo -e "\033[33m$line\033[0m"  # Yellow for warnings
        elif echo "$line" | grep -q "smart_lock_manager"; then
            echo -e "\033[32m$line\033[0m"  # Green for our component
        elif echo "$line" | grep -q "config_entries\|setup"; then
            echo -e "\033[36m$line\033[0m"  # Cyan for setup
        else
            echo "$line"  # Normal for everything else
        fi
    done