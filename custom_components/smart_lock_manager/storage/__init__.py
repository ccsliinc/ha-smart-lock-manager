"""Storage module for Smart Lock Manager."""

from .alert_storage import load_alert_log, save_alert_log
from .global_settings import (
    get_cached_global_settings,
    load_global_settings,
    save_global_settings,
)
from .lock_storage import load_lock_data, save_lock_data
from .zone_storage import (
    delete_zone_storage,
    load_all_zones,
    load_migration_marker,
    save_migration_marker,
    save_zone,
)

__all__ = [
    "save_lock_data",
    "load_lock_data",
    "save_zone",
    "delete_zone_storage",
    "load_all_zones",
    "load_migration_marker",
    "save_migration_marker",
    "load_alert_log",
    "save_alert_log",
    "load_global_settings",
    "save_global_settings",
    "get_cached_global_settings",
]
