"""Constants for Smart Lock Manager."""

from homeassistant.const import STATE_LOCKED, STATE_UNLOCKED

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
ATTR_CODE_SLOT = "code_slot"
ATTR_NAME = "lockname"
ATTR_NODE_ID = "node_id"
ATTR_USER_CODE = "usercode"

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
SERVICE_REFRESH_CODES = "refresh_codes"
SERVICE_SET_CODE = "set_code"
SERVICE_GENERATE_PACKAGE = "generate_package"

# Misc
LOCK_STATE = [STATE_LOCKED, STATE_UNLOCKED]

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
    ALARM_TYPE: {STATE_LOCKED: 24, STATE_UNLOCKED: 25},
    ACCESS_CONTROL: {STATE_LOCKED: 3, STATE_UNLOCKED: 4},
}

import logging

_LOGGER = logging.getLogger(__name__)
