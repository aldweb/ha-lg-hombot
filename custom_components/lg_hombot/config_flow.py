"""Config flow for the LG Hombot integration."""

from __future__ import annotations

import asyncio
from typing import Any

from aiohttp import ClientError
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.config_entries import ConfigFlowResult
from homeassistant.const import CONF_NAME, CONF_URL
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import DOMAIN
from .device import url_unique_id, validate_url
from .schema import USER_DATA_SCHEMA

CONNECT_TIMEOUT_SECONDS = 10


class LgHombotConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Config flow for the LG Hombot integration."""

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for a name and the robot URL, and check the robot answers."""

        errors: dict[str, str] = {}

        if user_input is not None:
            name = user_input[CONF_NAME]
            try:
                url = validate_url(user_input[CONF_URL])
            except ValueError:
                errors["base"] = "invalid_url"
            else:
                await self.async_set_unique_id(url_unique_id(url))
                self._abort_if_unique_id_configured()
                try:
                    session = async_get_clientsession(self.hass)
                    async with asyncio.timeout(CONNECT_TIMEOUT_SECONDS):
                        async with session.get(url + "status.txt") as response:
                            response.raise_for_status()
                            await response.read()
                except (ClientError, TimeoutError, OSError):
                    errors["base"] = "cannot_connect"
                else:
                    return self.async_create_entry(
                        title=name, data={CONF_NAME: name, CONF_URL: url}
                    )

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(USER_DATA_SCHEMA),
            errors=errors,
        )
