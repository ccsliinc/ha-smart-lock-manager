"""Voluptuous service schemas for Smart Lock Manager.

Extracted from ``services/registration.py`` (behavior-preserving) to keep both
modules under the 500-line limit. Schemas are identical to the originals.
``registration`` imports them back and the package root re-exports
``SET_SWEEP_INTERVALS_SCHEMA`` for frozen public callers/tests.
"""

import voluptuous as vol
from homeassistant.const import ATTR_ENTITY_ID
from homeassistant.helpers import config_validation as cv

from ..const import (
    ATTR_ALLOWED_DAYS,
    ATTR_ALLOWED_HOURS,
    ATTR_AUTO_DISABLE_EXPIRED,
    ATTR_CODE_SLOT,
    ATTR_CODE_SLOT_NAME,
    ATTR_COORDINATOR_INTERVAL,
    ATTR_DEBUG_LOGGING,
    ATTR_END_DATE,
    ATTR_MAX_USES,
    ATTR_NODE_ID,
    ATTR_NOTIFY_ON_USE,
    ATTR_SLOT_COUNT,
    ATTR_START_DATE,
    ATTR_SYNC_ON_LOCK_EVENTS,
    ATTR_USER_CODE,
)
from ..storage.global_settings import ATTR_NAG_INTERVAL_MINUTES

# Service schemas
CLEAR_CODE_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_ENTITY_ID): cv.entity_id,
        vol.Required(ATTR_CODE_SLOT): vol.Coerce(int),
    }
)

SET_CODE_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_ENTITY_ID): cv.entity_id,
        vol.Required(ATTR_CODE_SLOT): vol.Coerce(int),
        vol.Required(ATTR_USER_CODE): cv.string,
        vol.Optional(ATTR_CODE_SLOT_NAME): cv.string,
    }
)

REFRESH_CODES_SCHEMA = vol.Schema({vol.Required(ATTR_ENTITY_ID): cv.entity_id})

GENERATE_PACKAGE_SCHEMA = vol.Schema({vol.Required(ATTR_NODE_ID): cv.string})

# Advanced service schemas
SET_CODE_ADVANCED_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_ENTITY_ID): cv.entity_id,
        vol.Required(ATTR_CODE_SLOT): vol.Coerce(int),
        vol.Required(ATTR_USER_CODE): cv.string,
        vol.Optional(ATTR_CODE_SLOT_NAME): cv.string,
        vol.Optional(ATTR_START_DATE): cv.datetime,
        vol.Optional(ATTR_END_DATE): cv.datetime,
        vol.Optional(ATTR_ALLOWED_HOURS): [int],
        vol.Optional(ATTR_ALLOWED_DAYS): [int],
        vol.Optional(ATTR_MAX_USES, default=-1): int,
        vol.Optional(ATTR_NOTIFY_ON_USE, default=False): cv.boolean,
    }
)

ENABLE_DISABLE_SLOT_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_ENTITY_ID): cv.entity_id,
        vol.Required(ATTR_CODE_SLOT): vol.Coerce(int),
    }
)

RESET_SLOT_USAGE_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_ENTITY_ID): cv.entity_id,
        vol.Required(ATTR_CODE_SLOT): vol.Coerce(int),
    }
)

RESIZE_SLOTS_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_ENTITY_ID): cv.entity_id,
        vol.Required(ATTR_SLOT_COUNT): vol.Coerce(int),
    }
)

GET_USAGE_STATS_SCHEMA = vol.Schema({vol.Required(ATTR_ENTITY_ID): cv.entity_id})

CLEAR_ALL_SLOTS_SCHEMA = vol.Schema({vol.Required(ATTR_ENTITY_ID): cv.entity_id})

UPDATE_LOCK_SETTINGS_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_ENTITY_ID): cv.entity_id,
        vol.Optional("friendly_name"): str,
        vol.Optional("slot_count"): vol.All(int, vol.Range(min=1, max=50)),
    }
)

# Zone-management service schemas.
CREATE_ZONE_SCHEMA = vol.Schema(
    {
        vol.Required("name"): str,
        vol.Optional("member_lock_entity_ids"): [cv.entity_id],
    }
)

DELETE_ZONE_SCHEMA = vol.Schema({vol.Required("zone_id"): str})

APPLY_ZONE_CODES_SCHEMA = vol.Schema({vol.Required("zone_id"): str})

CLEAR_ZONE_CODES_SCHEMA = vol.Schema({vol.Required("zone_id"): str})

UPDATE_ZONE_SCHEMA = vol.Schema(
    {
        vol.Required("zone_id"): str,
        vol.Required("name"): str,
    }
)

ZONE_MEMBER_SCHEMA = vol.Schema(
    {
        vol.Required("zone_id"): str,
        vol.Required("lock_entity_id"): cv.entity_id,
    }
)

# The settings payload is a free-form nested dict of config blocks; the service
# merges per-block and the model rebuilds tolerantly, so deep validation lives
# in models.zone_settings rather than the voluptuous schema.
UPDATE_ZONE_SETTINGS_SCHEMA = vol.Schema(
    {
        vol.Required("zone_id"): str,
        vol.Required("settings"): dict,
    }
)

READ_ZWAVE_CODES_SCHEMA = vol.Schema({vol.Required(ATTR_ENTITY_ID): cv.entity_id})

UPDATE_GLOBAL_SETTINGS_SCHEMA = vol.Schema(
    {
        vol.Optional(ATTR_COORDINATOR_INTERVAL): vol.All(
            int, vol.In([30, 60, 120, 300])
        ),
        vol.Optional(ATTR_AUTO_DISABLE_EXPIRED): bool,
        vol.Optional(ATTR_SYNC_ON_LOCK_EVENTS): bool,
        vol.Optional(ATTR_DEBUG_LOGGING): bool,
    }
)

# At least one cadence must be supplied; each is a positive int in [1, 1440].
SET_SWEEP_INTERVALS_SCHEMA = vol.Schema(
    vol.All(
        {
            vol.Optional("outside_hours_sweep_minutes"): vol.All(
                vol.Coerce(int), vol.Range(min=1, max=1440)
            ),
            vol.Optional("health_sweep_minutes"): vol.All(
                vol.Coerce(int), vol.Range(min=1, max=1440)
            ),
            vol.Optional(ATTR_NAG_INTERVAL_MINUTES): vol.All(
                vol.Coerce(int), vol.Range(min=1, max=1440)
            ),
        },
        cv.has_at_least_one_key(
            "outside_hours_sweep_minutes",
            "health_sweep_minutes",
            ATTR_NAG_INTERVAL_MINUTES,
        ),
    )
)

# Pause/resume the alert-snooze. ``hours`` is a float in [0.25, 24]; ``zone_id``
# is optional (omit to snooze ALL zones globally).
PAUSE_ALERTS_SCHEMA = vol.Schema(
    {
        vol.Required("hours"): vol.All(vol.Coerce(float), vol.Range(min=0.25, max=24)),
        vol.Optional("zone_id"): cv.string,
    }
)
RESUME_ALERTS_SCHEMA = vol.Schema({vol.Optional("zone_id"): cv.string})

# Mute/unmute a per-member alert. ``entity_id`` is the RAW member entity id
# string (the mute store keys on it directly, NOT a validated cv.entity_id).
# ``alert_type`` defaults to ``"all"`` (every type for that member).
MUTE_LOCK_ALERT_SCHEMA = vol.Schema(
    {
        vol.Required("entity_id"): cv.string,
        vol.Optional("alert_type", default="all"): cv.string,
    }
)
UNMUTE_LOCK_ALERT_SCHEMA = vol.Schema(
    {
        vol.Required("entity_id"): cv.string,
        vol.Optional("alert_type", default="all"): cv.string,
    }
)
