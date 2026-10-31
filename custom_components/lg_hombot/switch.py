"""Repeat switch for the LG HomBot."""

from __future__ import annotations

from collections.abc import Callable
import logging
from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .status import (
    HomBotCommandError,
    HomBotStatusError,
    async_fetch_status,
    async_send_command,
    listen_status,
)

_LOGGER = logging.getLogger(__name__)

REPEAT_COMMAND_ON = '{"COMMAND":{"REPEAT":"true"}}'
REPEAT_COMMAND_OFF = '{"COMMAND":{"REPEAT":"false"}}'


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Repeat switch on the HomBot device."""

    url = config_entry.data.get("url")
    name = config_entry.data.get("name")
    async_add_entities([HombotRepeatSwitch(hass, name, url)])


class HombotRepeatSwitch(SwitchEntity):
    """Repeat mode, read from status.txt and set with an explicit command.

    The switch does not poll. It reads status.txt when it is added, again
    after a Repeat command, and whenever the vacuum update publishes a
    status. Without those vacuum updates there is no periodic refresh.
    """

    _attr_has_entity_name = True
    _attr_name = "Repeat"
    _attr_should_poll = False
    _attr_available = False
    _attr_is_on: bool | None = None

    def __init__(self, hass: HomeAssistant, name: str, url: str) -> None:
        """Initialize the switch."""

        self.hass = hass
        self._name = name
        self._url = url
        self._attr_unique_id = f"hombot_{url}_repeat"
        self._remove_listener: Callable[[], None] | None = None

    @property
    def device_info(self) -> DeviceInfo:
        """Attach Repeat to the same device as the vacuum entity."""

        return DeviceInfo(
            identifiers={(DOMAIN, f"hombot_{self._url}")},
            name=self._name,
            manufacturer="LG",
            model="Hombot",
            configuration_url=self._url,
        )

    async def async_added_to_hass(self) -> None:
        """Follow vacuum status updates and read the current Repeat value."""

        await super().async_added_to_hass()
        self._remove_listener = listen_status(self.hass, self._url, self._on_status)
        await self._async_read_status()

    async def async_will_remove_from_hass(self) -> None:
        """Stop receiving vacuum status updates."""

        if self._remove_listener is not None:
            self._remove_listener()
            self._remove_listener = None
        await super().async_will_remove_from_hass()

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Enable Repeat, then read status.txt again."""

        await self._async_set_repeat(REPEAT_COMMAND_ON)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Disable Repeat, then read status.txt again."""

        await self._async_set_repeat(REPEAT_COMMAND_OFF)

    async def _async_set_repeat(self, command: str) -> None:
        """Send one explicit Repeat command and apply only a fresh status."""

        session = async_get_clientsession(self.hass)
        try:
            await async_send_command(session, self._url, command, require_ok=True)
        except HomBotCommandError as err:
            _LOGGER.warning("HomBot repeat command failed: %s", err)
            raise HomeAssistantError("HomBot repeat command failed") from err
        except Exception as err:  # noqa: BLE001 - report the service call as failed
            _LOGGER.exception("Unexpected HomBot repeat command error")
            raise HomeAssistantError("HomBot repeat command failed") from err
        await self._async_read_status(raise_on_error=True)

    async def _async_read_status(self, *, raise_on_error: bool = False) -> None:
        """Apply Repeat only when status.txt can be read.

        The read during entity setup logs and leaves the current state.
        The read after a Repeat command fails the service call instead,
        still without applying a state from the command alone.
        """

        session = async_get_clientsession(self.hass)
        try:
            attributes = await async_fetch_status(session, self._url)
        except HomBotStatusError as err:
            _LOGGER.warning("HomBot repeat status read failed: %s", err)
            if raise_on_error:
                raise HomeAssistantError("HomBot repeat status read failed") from err
            return
        except Exception as err:  # noqa: BLE001 - keep a setup read off other entities
            _LOGGER.exception("Unexpected HomBot repeat status error")
            if raise_on_error:
                raise HomeAssistantError("HomBot repeat status read failed") from err
            return
        self._apply_repeat(attributes)
        self.async_write_ha_state()

    def _on_status(self, attributes: dict[str, str]) -> None:
        """Apply a status.txt result from the vacuum poll."""

        self._apply_repeat(attributes)
        self.async_write_ha_state()

    def _apply_repeat(self, attributes: dict[str, str]) -> None:
        """Map the exact JSON_REPEAT value. Anything else is unavailable."""

        if "JSON_REPEAT" not in attributes:
            self._attr_available = False
            return
        value = attributes["JSON_REPEAT"]
        if value == "true":
            self._attr_available = True
            self._attr_is_on = True
            return
        if value == "false":
            self._attr_available = True
            self._attr_is_on = False
            return
        self._attr_available = False
