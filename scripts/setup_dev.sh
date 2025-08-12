#!/bin/bash
# Set up development environment for Smart Lock Manager

set -e

echo "⚙️  Setting up Smart Lock Manager Development Environment"
echo "======================================================"

# Create virtual environment if it doesn't exist
if [ ! -d "venv" ]; then
    echo "1️⃣ Creating virtual environment..."
    python3 -m venv venv
    echo "✅ Virtual environment created"
else
    echo "1️⃣ Virtual environment already exists"
fi

# Install dependencies
echo "2️⃣ Installing dependencies..."
./venv/bin/pip install --upgrade pip
./venv/bin/pip install -r requirements.txt
echo "✅ Dependencies installed"

# Install pre-commit hooks
echo "3️⃣ Installing pre-commit hooks..."
./venv/bin/pre-commit install
echo "✅ Pre-commit hooks installed"

# Create config directories
echo "4️⃣ Creating configuration directories..."
mkdir -p config/custom_components
mkdir -p tests
echo "✅ Directories created"

# Test the environment
echo "5️⃣ Testing environment..."
./venv/bin/python test_environment.py
echo ""
echo "🎉 Development environment is ready!"
echo ""
echo "Next steps:"
echo "• Run './scripts/test_component.sh' to test the component"
echo "• Run './scripts/start_ha.sh' to start Home Assistant"
echo "• Open http://localhost:8123 in your browser"