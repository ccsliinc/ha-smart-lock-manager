#!/usr/bin/env python3
"""Test runner for Smart Lock Manager."""

import subprocess
import sys
from pathlib import Path


def run_command(cmd, description):
    """Run a command and return success status."""
    print(f"\n{'='*50}")
    print(f"Running: {description}")
    print(f"Command: {' '.join(cmd)}")
    print("=" * 50)

    try:
        result = subprocess.run(cmd, check=True, capture_output=True, text=True)
        print("✅ PASSED")
        if result.stdout:
            print(f"Output:\n{result.stdout}")
        return True
    except subprocess.CalledProcessError as e:
        print("❌ FAILED")
        if e.stdout:
            print(f"stdout:\n{e.stdout}")
        if e.stderr:
            print(f"stderr:\n{e.stderr}")
        return False


def main():
    """Main test runner."""
    project_root = Path(__file__).parent
    success_count = 0
    total_tests = 0

    # Test configurations
    test_configs = [
        {
            "cmd": [
                "./venv/bin/python",
                "-m",
                "pytest",
                "tests/test_models_lock.py",
                "-v",
            ],
            "description": "Model Tests (CodeSlot & SmartLockManagerLock)",
        },
        {
            "cmd": [
                "./venv/bin/python",
                "-m",
                "pytest",
                "tests/test_services_lock.py",
                "-v",
            ],
            "description": "Service Layer Tests",
        },
        {
            "cmd": [
                "./venv/bin/python",
                "-m",
                "pytest",
                "tests/test_integration_frontend.py",
                "-v",
            ],
            "description": "Frontend-Backend Integration Tests",
        },
        {
            "cmd": ["./venv/bin/python", "-m", "pytest", "tests/", "-v", "--tb=short"],
            "description": "All Tests (Summary)",
        },
    ]

    # Change to project directory
    original_cwd = Path.cwd()
    try:
        import os

        os.chdir(project_root)

        print(f"🧪 Smart Lock Manager Test Suite")
        print(f"Project root: {project_root}")
        print(f"Python: {sys.executable}")

        # Run each test configuration
        for config in test_configs:
            total_tests += 1
            if run_command(config["cmd"], config["description"]):
                success_count += 1

        # Summary
        print(f"\n{'='*60}")
        print(f"TEST SUMMARY")
        print(f"{'='*60}")
        print(f"✅ Passed: {success_count}")
        print(f"❌ Failed: {total_tests - success_count}")
        print(f"📊 Success Rate: {success_count/total_tests*100:.1f}%")

        if success_count == total_tests:
            print("\n🎉 ALL TESTS PASSED!")
            sys.exit(0)
        else:
            print(f"\n💥 {total_tests - success_count} test suite(s) failed!")
            sys.exit(1)

    finally:
        os.chdir(original_cwd)


if __name__ == "__main__":
    main()
