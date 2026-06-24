"""Storage module for Smart Lock Manager."""

from .alert_storage import load_alert_log, save_alert_log
from .global_settings import (
    get_cached_global_settings,
    load_global_settings,
    save_global_settings,
)
from .lock_storage import load_lock_data, save_lock_data
from .muted import (
    clear_mute,
    get_cached_muted,
    is_muted,
    load_muted,
    muted_state_for_api,
    save_muted,
    set_mute,
)
from .snooze import (
    clear_global_snooze,
    clear_zone_snooze,
    get_cached_snooze,
    global_snooze_active,
    load_snooze,
    save_snooze,
    set_global_snooze,
    set_zone_snooze,
    snooze_active,
    snooze_state_for_api,
    zone_snooze_active,
)
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
    "load_snooze",
    "save_snooze",
    "get_cached_snooze",
    "global_snooze_active",
    "zone_snooze_active",
    "snooze_active",
    "set_global_snooze",
    "set_zone_snooze",
    "clear_global_snooze",
    "clear_zone_snooze",
    "snooze_state_for_api",
    "load_muted",
    "save_muted",
    "get_cached_muted",
    "is_muted",
    "set_mute",
    "clear_mute",
    "muted_state_for_api",
]
