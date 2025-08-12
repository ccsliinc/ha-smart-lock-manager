#!/bin/bash
# Watch Home Assistant logs in real-time with filtering

echo "📊 Watching Home Assistant Logs"
echo "==============================="

# Check if HA is running
if ! pgrep -f "homeassistant" > /dev/null; then
    echo "⚠️  Home Assistant is not running."
    echo "   Start it with: ./scripts/start_ha.sh"
    exit 1
fi

echo "🔍 Filtering for: Smart Lock Manager, Errors, Config, and Setup"
echo "⚠️  Press Ctrl+C to stop watching"
echo ""

# Watch the HA log file if it exists, otherwise tail system logs
if [ -f "config/home-assistant.log" ]; then
    tail -f config/home-assistant.log | grep -E \
        "(smart_lock_manager|Smart Lock Manager|ERROR|WARNING|CRITICAL|config_entries|setup)" | \
        while IFS= read -r line; do
            timestamp=$(date '+%H:%M:%S')
            if echo "$line" | grep -q "ERROR\|CRITICAL"; then
                echo -e "[$timestamp] \033[31m❌ $line\033[0m"
            elif echo "$line" | grep -q "WARNING"; then
                echo -e "[$timestamp] \033[33m⚠️  $line\033[0m"
            elif echo "$line" | grep -q "smart_lock_manager\|Smart Lock Manager"; then
                echo -e "[$timestamp] \033[32m🔐 $line\033[0m"
            elif echo "$line" | grep -q "config_entries\|setup"; then
                echo -e "[$timestamp] \033[36m⚙️  $line\033[0m"
            else
                echo -e "[$timestamp] $line"
            fi
        done
else
    echo "📁 Log file not found at config/home-assistant.log"
    echo "   Using live output filtering instead..."
    echo "   (Run this after starting HA with ./scripts/start_ha.sh)"
fi