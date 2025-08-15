"""Storage module for Smart Lock Manager."""

from .lock_storage import load_lock_data, save_lock_data

__all__ = ["save_lock_data", "load_lock_data"]
