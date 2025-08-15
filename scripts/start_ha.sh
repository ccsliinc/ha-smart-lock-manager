#!/bin/bash
# Start Home Assistant with development setup and clean logging

set -e

echo "🏠 Starting Home Assistant Development"
echo "========================================="

# Kill any existing Home Assistant processes first
echo "🧹 Cleaning up any existing Home Assistant processes..."
pkill -f "homeassistant" 2>/dev/null && echo "  ✓ Stopped existing homeassistant process" || echo "  No homeassistant processes found"
pkill -f "hass" 2>/dev/null && echo "  ✓ Stopped existing hass process" || echo "  No hass processes found"

# Wait a moment for processes to fully terminate
echo "  Waiting for processes to terminate..."
sleep 2

echo "✅ Cleanup complete"
echo ""

# Check virtual environment
if [ ! -d "venv" ]; then
    echo "❌ Virtual environment not found. Run: ./scripts/setup_dev.sh"
    exit 1
fi

# Setup symlinks
echo "🔗 Setting up custom component symlinks..."
mkdir -p config/custom_components
find config/custom_components -type l -delete 2>/dev/null || true

if [ -d "custom_components/smart_lock_manager" ]; then
    ln -sf "$(pwd)/custom_components/smart_lock_manager" "config/custom_components/smart_lock_manager"
    echo "✅ Created symlink for Smart Lock Manager"
else
    echo "❌ Smart Lock Manager component not found"
    exit 1
fi

export PYTHONPATH="$(pwd):$PYTHONPATH"

echo ""
echo "🚀 Starting Home Assistant..."
echo "📊 Using logger configuration for clean output"
echo "💡 Open http://localhost:8123 in your browser"
echo "🔍 Look for 'Smart Lock Manager' messages in the logs"
echo "⚠️  Press Ctrl+C to stop"
echo ""

# Just run HA - let the logger config handle filtering
./venv/bin/python -m homeassistant --config config --debug