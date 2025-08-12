#!/bin/bash
# Debug Smart Lock Manager component with focused logging

set -e

echo "🐛 Smart Lock Manager Debug Mode"
echo "==============================="

# Check if HA is already running
if pgrep -f "homeassistant" > /dev/null; then
    echo "⚠️  Home Assistant is already running. Stop it first or this will conflict."
    echo "   You can stop it with: pkill -f homeassistant"
    read -p "Continue anyway? (y/N): " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        exit 1
    fi
fi

# Setup symlinks
echo "🔗 Setting up custom component symlinks..."
mkdir -p config/custom_components
find config/custom_components -type l -delete

if [ -d "custom_components/smart_lock_manager" ]; then
    ln -sf "$(pwd)/custom_components/smart_lock_manager" "config/custom_components/smart_lock_manager"
    echo "✅ Created symlink"
else
    echo "❌ Smart Lock Manager component not found"
    exit 1
fi

export PYTHONPATH="$(pwd):$PYTHONPATH"

echo "🚀 Starting with maximum debug info for Smart Lock Manager..."
echo ""

# Start with very specific filtering for our component
./venv/bin/python -m homeassistant --config config --debug 2>&1 | \
    grep -E "(smart_lock_manager|Smart Lock Manager|custom_components\.smart_lock_manager|ERROR|CRITICAL)" | \
    while IFS= read -r line; do
        timestamp=$(date '+%H:%M:%S')
        if echo "$line" | grep -q "ERROR\|CRITICAL"; then
            echo -e "[$timestamp] \033[31m❌ $line\033[0m"
        elif echo "$line" | grep -q "smart_lock_manager\|Smart Lock Manager"; then
            echo -e "[$timestamp] \033[32m🔐 $line\033[0m"
        else
            echo -e "[$timestamp] $line"
        fi
    done