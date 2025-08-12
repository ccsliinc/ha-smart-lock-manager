# Lock Manager

A Home Assistant custom component for advanced lock management.

## Installation

### HACS (Recommended)

1. Open HACS in Home Assistant
2. Go to "Integrations"
3. Click the "+" button
4. Search for "Lock Manager"
5. Install the integration
6. Restart Home Assistant

### Manual Installation

1. Copy the `custom_components/lock_manager` folder to your Home Assistant `custom_components` directory
2. Restart Home Assistant

## Configuration

After installation, the integration can be configured through the Home Assistant UI:

1. Go to Configuration > Integrations
2. Click the "+" button
3. Search for "Lock Manager"
4. Follow the configuration steps

## Development

This project uses:
- Python 3.11+
- Home Assistant development environment
- pytest for testing
- pre-commit for code quality

### Setup Development Environment

```bash
# Clone the repository
git clone <repository-url>
cd lock_manager

# Create virtual environment
python -m venv venv
source venv/bin/activate  # On macOS/Linux
# or
venv\Scripts\activate  # On Windows

# Install dependencies
pip install -r requirements.txt

# Install pre-commit hooks
pre-commit install

# Start Home Assistant in development mode
python dev_start.py
```

### Running Tests

```bash
# Run all tests
pytest

# Run tests with coverage
pytest --cov=custom_components.lock_manager

# Run specific test file
pytest tests/test_config_flow.py
```

## Features

- Advanced lock management capabilities
- Integration with Home Assistant automation system
- Support for multiple lock types
- Comprehensive logging and debugging

## Support

For issues and feature requests, please use the GitHub issue tracker.