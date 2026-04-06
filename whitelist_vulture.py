# Vulture whitelist — intentionally unused code in HA integration context.
# Home Assistant calls these by convention (platform setup, property overrides,
# entity attributes). Removing them would break HA's integration contract.

# custom_components/smart_lock_manager/const.py
CONF_PATH = None
CONF_LOCK_ENTITY_ID = None
CONF_LOCK_NAME = None
CONF_SENSOR_NAME = None
CONF_SLOTS = None
CONF_START = None
DEFAULT_CODE_SLOTS = None
DEFAULT_DOOR_SENSOR = None
DEFAULT_GENERATE = None
DEFAULT_HIDE_PINS = None
DEFAULT_PACKAGES_PATH = None
DEFAULT_PATH = None
DEFAULT_START = None
LOCK_STATE = None
ACTION_MAP = None
LOCK_STATE_MAP = None

# custom_components/smart_lock_manager/frontend/panel.py
PANEL_URL = None
PANEL_CONFIG_PANEL_DOMAIN = None

# custom_components/smart_lock_manager/models/lock.py
alarm_level_or_user_code_entity_id = None
alarm_type_or_access_control_entity_id = None
door_sensor_entity_id = None
zwave_data = None


def get_slot_info(self):  # noqa: E704
    pass


def increment_slot_usage(self):  # noqa: E704
    pass


# custom_components/smart_lock_manager/sensor.py
def async_setup_entry(hass, entry, async_add_entities):  # noqa: E704
    pass


_attr_unique_id = None
_attr_icon = None


def extra_state_attributes(self):  # noqa: E704
    pass


def _get_slot_status_text(self):  # noqa: E704
    pass


def _get_slot_status_color(self):  # noqa: E704
    pass


def _get_slot_status_reason(self):  # noqa: E704
    pass


def device_info(self):  # noqa: E704
    pass


def available(self):  # noqa: E704
    pass


# custom_components/smart_lock_manager/services/system_services.py
update_interval = None

# custom_components/smart_lock_manager/services/zwave_services.py
last_synced = None
