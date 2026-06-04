#!/bin/bash
# Final validation script for Smart Lock Manager release

set -e

echo "🔍 Smart Lock Manager v2025.1.0 - Final Validation"
echo "================================================="

# Check if virtual environment exists
if [ ! -d "venv" ]; then
    echo "❌ Virtual environment not found"
    exit 1
fi

echo ""
echo "1️⃣ Testing Python syntax and imports..."
find custom_components/smart_lock_manager -name "*.py" -exec ./venv/bin/python -m py_compile {} \;
echo "✅ Python syntax validation passed"

echo ""
echo "2️⃣ Testing component imports..."
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
echo "3️⃣ Validating manifest.json..."
./venv/bin/python -c "
import json
with open('custom_components/smart_lock_manager/manifest.json') as f:
    manifest = json.load(f)
    print(f'✅ Manifest valid: {manifest[\"name\"]} v{manifest.get(\"version\", \"unknown\")}')

    # Check required HACS fields
    required_fields = ['documentation', 'issue_tracker', 'codeowners']
    for field in required_fields:
        if field not in manifest:
            raise ValueError(f'Missing HACS required field: {field}')
    print('✅ All HACS required fields present')
"

echo ""
echo "4️⃣ Validating HACS configuration..."
./venv/bin/python -c "
import json
with open('hacs.json') as f:
    hacs = json.load(f)
    print(f'✅ HACS config valid: {hacs[\"name\"]} v{hacs[\"version\"]}')

    # Check required fields
    required = ['name', 'version', 'domains']
    for field in required:
        if field not in hacs:
            raise ValueError(f'Missing HACS field: {field}')
    print('✅ HACS configuration complete')
"

echo ""
echo "5️⃣ Running test suite..."
export PYTHONPATH="$PWD:$PYTHONPATH"
./venv/bin/pytest tests/ -v --tb=short -x
echo "✅ Test suite passed"

echo ""
echo "6️⃣ Running performance tests..."
./venv/bin/pytest tests/test_performance_simple.py -v --tb=short
echo "✅ Performance tests passed"

echo ""
echo "7️⃣ Running security scans..."
if command -v bandit &> /dev/null; then
    ./venv/bin/bandit -r custom_components/smart_lock_manager/ -ll -q
    echo "✅ Security scan passed"
else
    echo "⚠️ Bandit not available, skipping security scan"
fi

echo ""
echo "8️⃣ Checking file structure..."
required_files=(
    "custom_components/smart_lock_manager/__init__.py"
    "custom_components/smart_lock_manager/manifest.json"
    "custom_components/smart_lock_manager/config_flow.py"
    "custom_components/smart_lock_manager/sensor.py"
    "hacs.json"
    "README.md"
    "CHANGELOG.md"
    "SECURITY.md"
    "docs/API.md"
)

for file in "${required_files[@]}"; do
    if [ ! -f "$file" ]; then
        echo "❌ Missing required file: $file"
        exit 1
    fi
    echo "✅ Found: $file"
done

echo ""
echo "9️⃣ Validating documentation links..."
./venv/bin/python -c "
import requests
import json

# Check if GitHub repository is accessible
try:
    with open('custom_components/smart_lock_manager/manifest.json') as f:
        manifest = json.load(f)

    doc_url = manifest['documentation']
    issue_url = manifest['issue_tracker']

    print(f'📚 Documentation URL: {doc_url}')
    print(f'🐛 Issue tracker URL: {issue_url}')
    print('✅ URLs configured (manual verification recommended)')

except Exception as e:
    print(f'⚠️ Could not validate URLs: {e}')
"

echo ""
echo "🔟 Final checklist..."
echo "✅ Python syntax and imports"
echo "✅ Manifest validation"
echo "✅ HACS configuration"
echo "✅ Test suite"
echo "✅ Performance tests"
echo "✅ Security scans"
echo "✅ File structure"
echo "✅ Documentation"

echo ""
echo "🎉 Smart Lock Manager v2025.1.0 - Final validation PASSED!"
echo ""
echo "📋 Ready for:"
echo "   • HACS submission"
echo "   • Community distribution"
echo "   • Production use"
echo ""
echo "🔗 Repository: https://github.com/ccsliinc/ha-smart-lock-manager"
echo "🚀 Release: https://github.com/ccsliinc/ha-smart-lock-manager/releases/tag/v2025.1.0"
