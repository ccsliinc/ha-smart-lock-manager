#!/bin/bash
# Start Home Assistant with minimal filtering to see what's happening

set -e

echo "🏠 Starting Home Assistant (Simple Debug)"
echo "========================================"

# Setup symlinks
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

echo "🚀 Starting Home Assistant with ALL output..."
echo "💡 This will show everything - look for Smart Lock Manager mentions"
echo "⚠️  Press Ctrl+C to stop"
echo ""

# Start HA without any filtering first
./venv/bin/python -m homeassistant --config config --debug