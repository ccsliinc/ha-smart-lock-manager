"""Config flow for Smart Lock Manager."""

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResult

from .const import DOMAIN


class SmartLockManagerConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Smart Lock Manager."""

    VERSION = 1

    async def async_step_user(self, user_input=None) -> FlowResult:
        """Handle the initial step."""
        errors = {}

        if user_input is not None:
            # Validate input
            lock_name = user_input.get("lock_name", "").strip()
            if not lock_name:
                errors["lock_name"] = "name_required"
            else:
                # Create the config entry
                return self.async_create_entry(
                    title=lock_name,
                    data={
                        "lock_name": lock_name,
                        "lock_entity_id": user_input.get("lock_entity_id", ""),
                        "slots": user_input.get("slots", 10),
                    },
                )

        # Show the form
        data_schema = vol.Schema(
            {
                vol.Required("lock_name", default="Smart Lock"): str,
                vol.Optional("lock_entity_id", default=""): str,
                vol.Optional("slots", default=10): vol.All(
                    int, vol.Range(min=1, max=50)
                ),
            }
        )

        return self.async_show_form(
            step_id="user",
            data_schema=data_schema,
            errors=errors,
            description_placeholders={"component": "Smart Lock Manager"},
        )


class SmartLockManagerOptionsFlow(config_entries.OptionsFlow):
    """Handle options flow for Smart Lock Manager."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        """Initialize options flow."""
        self.config_entry = config_entry

    async def async_step_init(self, user_input=None) -> FlowResult:
        """Manage the options."""
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Optional(
                        "slots", default=self.config_entry.options.get("slots", 10)
                    ): vol.All(int, vol.Range(min=1, max=50)),
                }
            ),
        )

    @staticmethod
    @config_entries.callback
    def async_get_options_flow(config_entry: config_entries.ConfigEntry):
        """Create the options flow."""
        return SmartLockManagerOptionsFlow(config_entry)
