#!/usr/bin/env python3
"""Test script to verify the development environment is working."""

import sys
from pathlib import Path


def test_imports():
    """Test that all required modules can be imported."""
    try:
        import homeassistant.const

        print(
            f"✓ Home Assistant {homeassistant.const.__version__} imported successfully"
        )
    except ImportError as e:
        print(f"✗ Failed to import Home Assistant: {e}")
        return False

    try:
        import pytest

        print(f"✓ pytest imported successfully")
    except ImportError as e:
        print(f"✗ Failed to import pytest: {e}")
        return False

    try:
        import black

        print(f"✓ black imported successfully")
    except ImportError as e:
        print(f"✗ Failed to import black: {e}")
        return False

    return True


def test_directory_structure():
    """Test that the directory structure is correct."""
    required_dirs = ["custom_components", "config", "tests", "venv"]

    required_files = [
        "requirements.txt",
        "pyproject.toml",
        ".gitignore",
        ".pre-commit-config.yaml",
        "hacs.json",
        "README.md",
        "dev_start.py",
    ]

    for directory in required_dirs:
        if not Path(directory).is_dir():
            print(f"✗ Missing directory: {directory}")
            return False
        print(f"✓ Directory exists: {directory}")

    for file in required_files:
        if not Path(file).is_file():
            print(f"✗ Missing file: {file}")
            return False
        print(f"✓ File exists: {file}")

    return True


def main():
    """Run all tests."""
    print("Testing Home Assistant Custom Component Development Environment")
    print("=" * 60)

    print("\n1. Testing Python imports...")
    imports_ok = test_imports()

    print("\n2. Testing directory structure...")
    structure_ok = test_directory_structure()

    print("\n" + "=" * 60)
    if imports_ok and structure_ok:
        print("✓ All tests passed! Environment is ready for development.")
        print("\nNext steps:")
        print("1. Tell Claude what kind of plugin you want to create")
        print("2. Start Home Assistant with: python dev_start.py")
        print("3. Run tests with: ./venv/bin/pytest")
        return 0
    else:
        print("✗ Some tests failed. Please check the output above.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
