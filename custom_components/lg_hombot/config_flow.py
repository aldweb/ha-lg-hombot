from homeassistant import config_entries
from homeassistant.const import CONF_NAME, CONF_URL
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers.aiohttp_client import async_get_clientsession

import voluptuous as vol
from typing import Any


from .const import DOMAIN
from .schema import USER_DATA_SCHEMA


class LgHombotConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Config flow for the LG Hombot integration."""

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        errors = {}
        name = ""
        url = ""

        if user_input is not None:
            try:
                name = user_input[CONF_NAME]
                url = user_input[CONF_URL]

                session = async_get_clientsession(self.hass)
                response = await session.get(url)
                await response.read()
            except Exception:
                errors = {"base": "cannot_connect"}
            else:
                return self.async_create_entry(title=name, data={CONF_NAME: name, CONF_URL: url})

        schema = vol.Schema(USER_DATA_SCHEMA)
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)
