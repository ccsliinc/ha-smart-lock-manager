"""Constants for Smart Lock Manager."""

from homeassistant.components.lock import LockState

DOMAIN = "smart_lock_manager"
VERSION = "1.0.0"
ISSUE_URL = "https://github.com/jsugamele/smart_lock_manager"
PLATFORMS = ["binary_sensor", "sensor"]
ZWAVE_NETWORK = "zwave_network"
MANAGER = "manager"

# hass.data attributes
CHILD_LOCKS = "child_locks"
COORDINATOR = "coordinator"
PRIMARY_LOCK = "primary_lock"
UNSUB_LISTENERS = "unsub_listeners"

# Action entity type
ALARM_TYPE = "alarm_type"
ACCESS_CONTROL = "access_control"

# Events
EVENT_SMART_LOCK_MANAGER_LOCK_STATE_CHANGED = "smart_lock_manager_lock_state_changed"

# Event data constants
ATTR_ACTION_CODE = "action_code"
ATTR_ACTION_TEXT = "action_text"
ATTR_CODE_SLOT_NAME = "code_slot_name"

# Attributes
ATTR_ALLOWED_DAYS = "allowed_days"
ATTR_ALLOWED_HOURS = "allowed_hours"
ATTR_CODE_SLOT = "code_slot"
ATTR_END_DATE = "end_date"
ATTR_MAX_USES = "max_uses"
ATTR_NAME = "lockname"
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

# Configuration Properties
CONF_ALARM_LEVEL = "alarm_level"
CONF_ALARM_LEVEL_OR_USER_CODE_ENTITY_ID = "alarm_level_or_user_code_entity_id"
CONF_ALARM_TYPE = "alarm_type"
CONF_ALARM_TYPE_OR_ACCESS_CONTROL_ENTITY_ID = "alarm_type_or_access_control_entity_id"
CONF_CHILD_LOCKS = "child_locks"
CONF_CHILD_LOCKS_FILE = "child_locks_file"
CONF_ENTITY_ID = "entity_id"
CONF_GENERATE = "generate_package"
CONF_HIDE_PINS = "hide_pins"
CONF_PATH = "packages_path"
CONF_LOCK_ENTITY_ID = "lock_entity_id"
CONF_LOCK_NAME = "lock_name"
CONF_SENSOR_NAME = "sensor_name"
CONF_SLOTS = "slots"
CONF_START = "start_from"

# Defaults
DEFAULT_CODE_SLOTS = 10
DEFAULT_DOOR_SENSOR = "binary_sensor.fake"
DEFAULT_GENERATE = True
DEFAULT_HIDE_PINS = False
DEFAULT_PACKAGES_PATH = "/packages/smart_lock_manager/"
DEFAULT_PATH = "/packages/smart_lock_manager/"
DEFAULT_START = 1

# Services
SERVICE_CLEAR_CODE = "clear_code"
SERVICE_DISABLE_SLOT = "disable_slot"
SERVICE_ENABLE_SLOT = "enable_slot"
SERVICE_GENERATE_PACKAGE = "generate_package"
SERVICE_GET_USAGE_STATS = "get_usage_stats"
SERVICE_READ_ZWAVE_CODES = "read_zwave_codes"
SERVICE_REFRESH_CODES = "refresh_codes"
SERVICE_RESET_SLOT_USAGE = "reset_slot_usage"
SERVICE_RESIZE_SLOTS = "resize_slots"
SERVICE_SET_CODE = "set_code"
SERVICE_SET_CODE_ADVANCED = "set_code_advanced"
SERVICE_SYNC_CHILD_LOCKS = "sync_child_locks"
SERVICE_UPDATE_GLOBAL_SETTINGS = "update_global_settings"
SERVICE_UPDATE_LOCK_SETTINGS = "update_lock_settings"

# Misc
LOCK_STATE = [LockState.LOCKED, LockState.UNLOCKED]

# Action maps for handling alarm types and access control
ACCESS_CONTROL = "access_control"
ACTION_MAP = {
    ALARM_TYPE: {
        18: "Keypad Lock",
        19: "Keypad Unlock",
        21: "Manual Lock",
        22: "Manual Unlock",
        24: "RF Lock",
        25: "RF Unlock",
        26: "Auto Lock",
        27: "Auto Unlock",
        162: "Lock Jammed",
        9: "Deadbolt Jammed",
    },
    ACCESS_CONTROL: {
        1: "Manual Lock",
        2: "Manual Unlock",
        3: "RF Lock",
        4: "RF Unlock",
        5: "Keypad Lock",
        6: "Keypad Unlock",
        7: "Manual not fully locked",
        8: "RF not fully locked",
        9: "Auto Lock",
        10: "Auto Unlock",
        11: "Lock Jammed",
    },
}

LOCK_STATE_MAP = {
    ALARM_TYPE: {LockState.LOCKED: 24, LockState.UNLOCKED: 25},
    ACCESS_CONTROL: {LockState.LOCKED: 3, LockState.UNLOCKED: 4},
}

import logging

_LOGGER = logging.getLogger(__name__)
