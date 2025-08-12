#!/usr/bin/env python3
"""Development script to start Home Assistant with custom component."""

import os
import subprocess
import sys
from pathlib import Path


def main():
    """Start Home Assistant in development mode."""
    # Set environment variables for development
    os.environ["PYTHONPATH"] = str(Path(__file__).parent)

    # Create custom_components directory in config if it doesn't exist
    config_custom_components = Path("config/custom_components")
    config_custom_components.mkdir(exist_ok=True)

    # Create symlink from config/custom_components to our development directory
    project_custom_components = Path("custom_components")

    # Remove existing symlink if it exists
    for item in config_custom_components.iterdir():
        if item.is_symlink():
            item.unlink()

    # Create symlinks for each component in development
    if project_custom_components.exists():
        for component in project_custom_components.iterdir():
            if component.is_dir() and component.name != "__pycache__":
                symlink_path = config_custom_components / component.name
                if not symlink_path.exists():
                    symlink_path.symlink_to(component.absolute())
                    print(f"Created symlink: {symlink_path} -> {component.absolute()}")

    # Start Home Assistant
    cmd = [sys.executable, "-m", "homeassistant", "--config", "config", "--debug"]

    print("Starting Home Assistant in development mode...")
    print(f"Config directory: {Path('config').absolute()}")
    print(f"Custom components: {config_custom_components.absolute()}")
    print(f"Command: {' '.join(cmd)}")
    print("\nPress Ctrl+C to stop")

    try:
        subprocess.run(cmd, check=True)
    except KeyboardInterrupt:
        print("\nStopping Home Assistant...")
    except subprocess.CalledProcessError as e:
        print(f"Error starting Home Assistant: {e}")
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
