"""Config flow for Smart Lock Manager."""

from typing import Any, Dict, Optional

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers import selector

from .const import DOMAIN


class SmartLockManagerConfigFlow(  # type: ignore[call-arg]
    config_entries.ConfigFlow, domain=DOMAIN
):
    """Handle a config flow for Smart Lock Manager."""

    VERSION = 1

    async def async_step_user(
        self, user_input: Optional[Dict[str, Any]] = None
    ) -> FlowResult:
        """Handle the initial step."""
        errors = {}

        if user_input is not None:
            # Validate input
            lock_name = user_input.get("lock_name", "").strip()
            lock_entity_id = user_input.get("lock_entity_id", "").strip()

            if not lock_name:
                errors["lock_name"] = "name_required"
            elif not lock_entity_id:
                errors["lock_entity_id"] = "entity_required"
            else:
                # Check if this entity is already configured
                await self.async_set_unique_id(lock_entity_id)
                self._abort_if_unique_id_configured()

                # Create the config entry
                return self.async_create_entry(
                    title=lock_name,
                    data={
                        "lock_name": lock_name,
                        "lock_entity_id": lock_entity_id,
                        "slots": user_input.get("slots", 10),
                    },
                )

        # Show the form
        data_schema = vol.Schema(
            {
                vol.Required("lock_name", default="Smart Lock"): str,
                vol.Required("lock_entity_id"): selector.EntitySelector(
                    selector.EntitySelectorConfig(
                        domain="lock",
                    )
                ),
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

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> "SmartLockManagerOptionsFlow":
        """Create the options flow handler for an existing entry."""
        return SmartLockManagerOptionsFlow()


class SmartLockManagerOptionsFlow(config_entries.OptionsFlow):
    """Handle the options (reconfigure) flow for Smart Lock Manager.

    Reconfigures the real device topology: ``lock_name``, ``lock_entity_id``,
    and ``slots``. Topology is persisted to ``entry.data`` (the canonical
    "what the device is"), so ``async_setup_entry`` picks up the new values on
    the reload triggered by the update listener.

    NOTE: newer Home Assistant exposes ``OptionsFlow.config_entry`` as a
    read-only property — assigning ``self.config_entry`` here raises
    ``AttributeError``. We never assign it; ``self.config_entry`` is provided
    by the framework.
    """

    async def async_step_init(
        self, user_input: Optional[Dict[str, Any]] = None
    ) -> FlowResult:
        """Manage the options."""
        entry = self.config_entry
        errors: Dict[str, str] = {}

        if user_input is not None:
            lock_name = user_input.get("lock_name", "").strip()
            lock_entity_id = user_input.get("lock_entity_id", "").strip()
            slots = user_input.get("slots", entry.data.get("slots", 10))

            if not lock_name:
                errors["lock_name"] = "name_required"
            elif not lock_entity_id:
                errors["lock_entity_id"] = "entity_required"
            else:
                # Persist topology to entry.data (canonical). The update
                # listener registered in async_setup_entry reloads the entry,
                # so async_setup_entry reads these new values and reconciles
                # the slot collection to the new count.
                new_data = {
                    **entry.data,
                    "lock_name": lock_name,
                    "lock_entity_id": lock_entity_id,
                    "slots": slots,
                }
                self.hass.config_entries.async_update_entry(entry, data=new_data)

                # Nothing to store in entry.options — return empty options.
                return self.async_create_entry(title="", data={})

        # Pre-fill current values from entry.data so editing is non-destructive.
        data_schema = vol.Schema(
            {
                vol.Required(
                    "lock_name",
                    default=entry.data.get("lock_name", "Smart Lock"),
                ): str,
                vol.Required(
                    "lock_entity_id",
                    default=entry.data.get("lock_entity_id", ""),
                ): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="lock")
                ),
                vol.Optional(
                    "slots",
                    default=entry.data.get("slots", 10),
                ): vol.All(int, vol.Range(min=1, max=50)),
            }
        )

        return self.async_show_form(
            step_id="init",
            data_schema=data_schema,
            errors=errors,
        )
