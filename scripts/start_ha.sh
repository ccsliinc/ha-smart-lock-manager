#!/bin/bash
# Start Home Assistant with Smart Lock Manager in development mode

set -e

echo "🏠 Starting Home Assistant Development Environment"
echo "================================================"

# Check if virtual environment exists
if [ ! -d "venv" ]; then
    echo "❌ Virtual environment not found. Run: python -m venv venv && pip install -r requirements.txt"
    exit 1
fi

# Ensure custom components symlink exists
echo "🔗 Setting up custom component symlinks..."
mkdir -p config/custom_components

# Remove existing symlinks
find config/custom_components -type l -delete

# Create symlink for our component
if [ -d "custom_components/smart_lock_manager" ]; then
    ln -sf "$(pwd)/custom_components/smart_lock_manager" "config/custom_components/smart_lock_manager"
    echo "✅ Created symlink: config/custom_components/smart_lock_manager"
else
    echo "❌ Smart Lock Manager component not found in custom_components/"
    exit 1
fi

# Set environment variables
export PYTHONPATH="$(pwd):$PYTHONPATH"

echo "🚀 Starting Home Assistant..."
echo "📂 Config directory: $(pwd)/config"
echo "🔧 Custom components: $(pwd)/config/custom_components"
echo "🐛 Debug mode: ON"
echo ""
echo "💡 Open http://localhost:8123 in your browser"
echo "📊 Check logs for Smart Lock Manager output"
echo "⚠️  Press Ctrl+C to stop"
echo ""

# Start Home Assistant
./venv/bin/python -m homeassistant --config config --debug