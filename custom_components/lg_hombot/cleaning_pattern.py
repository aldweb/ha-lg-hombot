"""Cleaning pattern select for the LG HomBot."""

from __future__ import annotations

from collections.abc import Callable
import logging
from typing import Any

from homeassistant.components.select import SelectEntity
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.entity import DeviceInfo

from .const import DOMAIN
from .status import (
    HomBotCommandError,
    HomBotStatusError,
    async_fetch_status,
    async_send_command,
    listen_status,
)

_LOGGER = logging.getLogger(__name__)

PATTERN_ZIGZAG = "Zigzag"
PATTERN_CELL = "Cell by cell"
PATTERN_COMMAND_ZIGZAG = '{"COMMAND":{"CLEAN_MODE":"CLEAN_ZZ"}}'
PATTERN_COMMAND_CELL = '{"COMMAND":{"CLEAN_MODE":"CLEAN_SB"}}'

_COMMAND_BY_OPTION = {
    PATTERN_ZIGZAG: PATTERN_COMMAND_ZIGZAG,
    PATTERN_CELL: PATTERN_COMMAND_CELL,
}
_OPTION_BY_MODE = {
    "ZZ": PATTERN_ZIGZAG,
    "SB": PATTERN_CELL,
}


class HombotCleaningPatternSelect(SelectEntity):
    """Cleaning pattern, read from status.txt and set with an explicit command.

    The select does not poll. It reads status.txt when it is added, again
    after a pattern command, and whenever the vacuum update publishes a
    status. Without those vacuum updates there is no periodic refresh.
    """

    _attr_has_entity_name = True
    _attr_name = "Cleaning pattern"
    _attr_should_poll = False
    _attr_available = False
    _attr_current_option: str | None = None
    _attr_options = [PATTERN_ZIGZAG, PATTERN_CELL]

    def __init__(self, hass: Any, name: str, url: str) -> None:
        """Initialize the select. No pattern is confirmed yet."""

        self.hass = hass
        self._name = name
        self._url = url
        self._attr_unique_id = f"hombot_{url}_cleaning_pattern"
        self._attr_options = [PATTERN_ZIGZAG, PATTERN_CELL]
        self._remove_listener: Callable[[], None] | None = None

    @property
    def device_info(self) -> DeviceInfo:
        """Attach the pattern select to the same device as the vacuum."""

        return DeviceInfo(
            identifiers={(DOMAIN, f"hombot_{self._url}")},
            name=self._name,
            manufacturer="LG",
            model="Hombot",
            configuration_url=self._url,
        )

    async def async_get_last_state(self):
        """Do not restore a pattern. The next status read supplies it."""

        return None

    async def async_added_to_hass(self) -> None:
        """Follow vacuum status updates and read the current pattern."""

        await super().async_added_to_hass()
        self._remove_listener = listen_status(self.hass, self._url, self._on_status)
        await self._async_read_status()

    async def async_will_remove_from_hass(self) -> None:
        """Stop receiving vacuum status updates."""

        if self._remove_listener is not None:
            self._remove_listener()
            self._remove_listener = None
        await super().async_will_remove_from_hass()

    async def async_select_option(self, option: str) -> None:
        """Send one pattern command, then apply only a fresh status.

        An option outside the two patterns sends nothing. A pattern that
        is already confirmed sends nothing. An unavailable select still
        sends the chosen pattern, because no mode is confirmed yet.
        """

        command = _COMMAND_BY_OPTION.get(option)
        if command is None:
            return
        if self._attr_available and self._attr_current_option == option:
            return
        await self._async_set_pattern(command)

    async def _async_set_pattern(self, command: str) -> None:
        """Send one explicit pattern command and apply only a fresh status."""

        session = async_get_clientsession(self.hass)
        try:
            await async_send_command(session, self._url, command, require_ok=True)
        except HomBotCommandError as err:
            _LOGGER.warning("HomBot cleaning pattern command failed: %s", err)
            raise HomeAssistantError("HomBot cleaning pattern command failed") from err
        except Exception as err:  # noqa: BLE001 - report the service call as failed
            _LOGGER.exception("Unexpected HomBot cleaning pattern command error")
            raise HomeAssistantError("HomBot cleaning pattern command failed") from err
        await self._async_read_status(raise_on_error=True)

    async def _async_read_status(self, *, raise_on_error: bool = False) -> None:
        """Apply the pattern only when status.txt can be read.

        The read during entity setup logs and leaves the current state.
        The read after a pattern command fails the service call instead,
        still without applying a state from the command alone.
        """

        session = async_get_clientsession(self.hass)
        try:
            attributes = await async_fetch_status(session, self._url)
        except HomBotStatusError as err:
            _LOGGER.warning("HomBot cleaning pattern status read failed: %s", err)
            if raise_on_error:
                raise HomeAssistantError(
                    "HomBot cleaning pattern status read failed"
                ) from err
            return
        except Exception as err:  # noqa: BLE001 - keep a setup read off other entities
            _LOGGER.exception("Unexpected HomBot cleaning pattern status error")
            if raise_on_error:
                raise HomeAssistantError(
                    "HomBot cleaning pattern status read failed"
                ) from err
            return
        self._apply_mode(attributes)
        self.async_write_ha_state()

    def _on_status(self, attributes: dict[str, str]) -> None:
        """Apply a status.txt result from the vacuum poll."""

        self._apply_mode(attributes)
        self.async_write_ha_state()

    def _apply_mode(self, attributes: dict[str, str]) -> None:
        """Map the exact JSON_MODE value. Anything else is unavailable."""

        option = _OPTION_BY_MODE.get(attributes.get("JSON_MODE"))
        if option is None:
            self._attr_available = False
            self._attr_current_option = None
            return
        self._attr_available = True
        self._attr_current_option = option
