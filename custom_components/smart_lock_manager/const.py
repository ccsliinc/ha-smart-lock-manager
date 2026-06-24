"""Constants for Smart Lock Manager."""

import logging

_LOGGER = logging.getLogger(__name__)

DOMAIN = "smart_lock_manager"
VERSION = "2026.6.1"
ISSUE_URL = "https://github.com/ccsliinc/ha-smart-lock-manager"
PLATFORMS = ["sensor"]

# hass.data attributes
COORDINATOR = "coordinator"
PRIMARY_LOCK = "primary_lock"

# Action entity type
ALARM_TYPE = "alarm_type"
ACCESS_CONTROL = "access_control"

# Event data constants
ATTR_CODE_SLOT_NAME = "code_slot_name"

# Attributes
ATTR_ALLOWED_DAYS = "allowed_days"
ATTR_ALLOWED_HOURS = "allowed_hours"
ATTR_CODE_SLOT = "code_slot"
ATTR_END_DATE = "end_date"
ATTR_MAX_USES = "max_uses"
ATTR_NODE_ID = "node_id"
ATTR_NOTIFY_ON_USE = "notify_on_use"
ATTR_SLOT_COUNT = "slot_count"
ATTR_START_DATE = "start_date"
ATTR_USER_CODE = "usercode"
ATTR_ENTITY_ID = "entity_id"

# Global settings attributes
ATTR_COORDINATOR_INTERVAL = "coordinator_interval"
ATTR_AUTO_DISABLE_EXPIRED = "auto_disable_expired"
ATTR_SYNC_ON_LOCK_EVENTS = "sync_on_lock_events"
ATTR_DEBUG_LOGGING = "debug_logging"

# Maximum Z-Wave write dispatches for a slot before it is treated as a hard
# sync failure (``sync_error`` is set). Used by the per-lock sync planner and
# the zone API's member-derived sync-status aggregation so both agree on what
# counts as a genuine error vs. an in-flight pending write.
MAX_SYNC_ATTEMPTS = 10

# Services
SERVICE_CLEAR_ALL_SLOTS = "clear_all_slots"
SERVICE_CLEAR_CODE = "clear_code"
SERVICE_DISABLE_SLOT = "disable_slot"
SERVICE_ENABLE_SLOT = "enable_slot"
SERVICE_GENERATE_PACKAGE = "generate_package"
SERVICE_GET_USAGE_STATS = "get_usage_stats"
SERVICE_READ_ZWAVE_CODES = "read_zwave_codes"
SERVICE_REFRESH_CODES = "refresh_codes"
SERVICE_RESET_SLOT_USAGE = "reset_slot_usage"
SERVICE_RESET_SYNC = "reset_sync"
SERVICE_RESIZE_SLOTS = "resize_slots"
SERVICE_SET_CODE = "set_code"
SERVICE_SET_CODE_ADVANCED = "set_code_advanced"
SERVICE_UPDATE_GLOBAL_SETTINGS = "update_global_settings"
SERVICE_UPDATE_LOCK_SETTINGS = "update_lock_settings"
SERVICE_SET_SWEEP_INTERVALS = "set_sweep_intervals"
SERVICE_PAUSE_ALERTS = "pause_alerts"
SERVICE_RESUME_ALERTS = "resume_alerts"
SERVICE_MUTE_LOCK_ALERT = "mute_lock_alert"
SERVICE_UNMUTE_LOCK_ALERT = "unmute_lock_alert"

# Zone-management services (replace retired parent/child services)
SERVICE_CREATE_ZONE = "create_zone"
SERVICE_DELETE_ZONE = "delete_zone"
SERVICE_ADD_LOCK_TO_ZONE = "add_lock_to_zone"
SERVICE_REMOVE_LOCK_FROM_ZONE = "remove_lock_from_zone"
SERVICE_APPLY_ZONE_CODES = "apply_zone_codes"
SERVICE_UPDATE_ZONE = "update_zone"
SERVICE_CLEAR_ZONE_CODES = "clear_zone_codes"
SERVICE_UPDATE_ZONE_SETTINGS = "update_zone_settings"
