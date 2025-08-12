#!/bin/bash
# Test Smart Lock Manager component for syntax and import errors

set -e

echo "🧪 Testing Smart Lock Manager Component"
echo "======================================"

# Check if virtual environment exists
if [ ! -d "venv" ]; then
    echo "❌ Virtual environment not found"
    exit 1
fi

echo "1️⃣ Testing Python syntax..."
find custom_components/smart_lock_manager -name "*.py" -exec ./venv/bin/python -m py_compile {} \;
echo "✅ Syntax check passed"

echo ""
echo "2️⃣ Testing imports..."
./venv/bin/python -c "
try:
    from custom_components.smart_lock_manager import DOMAIN
    from custom_components.smart_lock_manager.const import VERSION
    print(f'✅ Smart Lock Manager v{VERSION} imports successfully')
except ImportError as e:
    print(f'❌ Import error: {e}')
    exit(1)
"

echo ""
echo "3️⃣ Testing manifest..."
if [ -f "custom_components/smart_lock_manager/manifest.json" ]; then
    ./venv/bin/python -c "
import json
with open('custom_components/smart_lock_manager/manifest.json') as f:
    manifest = json.load(f)
    print(f'✅ Manifest valid: {manifest[\"name\"]} v{manifest.get(\"version\", \"unknown\")}')
"
else
    echo "❌ manifest.json not found"
    exit 1
fi

echo ""
echo "✅ All tests passed! Component is ready for Home Assistant."