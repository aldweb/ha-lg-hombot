"""Shared base for the HomBot entities that follow status.txt.

The Repeat switch and the cleaning-pattern select read the same status.txt
that the vacuum polls, and change the robot with one explicit json.cgi
command. This mixin holds everything they have in common; each entity only
declares its label, its unique-id suffix and how to map status.txt to a state.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
import logging
from typing import Any

from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.entity import DeviceInfo

from .device import hombot_device_info
from .status import (
    HomBotCommandError,
    HomBotStatusError,
    async_fetch_status,
    async_send_command,
    listen_status,
)

_LOGGER = logging.getLogger(__name__)

# The robot needs a moment before status.txt reflects a command. Reading it
# immediately could bring the previous value back into the UI until the next
# vacuum poll.
STATUS_READ_DELAY = 1.0


async def _settle() -> None:
    """Give the robot time to apply a command before status.txt is read."""

    await asyncio.sleep(STATUS_READ_DELAY)


class HombotStatusEntity:
    """Mixin: no polling, state read from status.txt, one command to change it.

    The entity reads status.txt when it is added, after each command (once the
    robot has had ``STATUS_READ_DELAY`` seconds to apply it) and whenever the
    vacuum update publishes a status. Without those vacuum updates there is no
    periodic refresh.

    List this class before the Home Assistant entity class in the bases.
    Subclasses set ``_label`` and ``_unique_suffix`` and implement
    ``_apply_status``.
    """

    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_available = False

    _label: str
    _unique_suffix: str

    def __init__(self, hass: Any, name: str, url: str) -> None:
        """Initialize the entity. No state is confirmed yet."""

        self.hass = hass
        self._name = name
        self._url = url
        self._attr_unique_id = f"hombot_{url}_{self._unique_suffix}"
        self._remove_listener: Callable[[], None] | None = None

    @property
    def device_info(self) -> DeviceInfo:
        """Attach the entity to the same device as the vacuum."""

        return hombot_device_info(self._name, self._url)

    def _apply_status(self, attributes: dict[str, str]) -> None:
        """Map status.txt to the entity state. Set availability here."""

        raise NotImplementedError

    async def async_added_to_hass(self) -> None:
        """Follow vacuum status updates and read the current value."""

        await super().async_added_to_hass()
        self._remove_listener = listen_status(self.hass, self._url, self._on_status)
        await self._async_read_status()

    async def async_will_remove_from_hass(self) -> None:
        """Stop receiving vacuum status updates."""

        if self._remove_listener is not None:
            self._remove_listener()
            self._remove_listener = None
        await super().async_will_remove_from_hass()

    async def _async_send_command(self, command: str) -> None:
        """Send one explicit command, wait, then apply only a fresh status."""

        session = async_get_clientsession(self.hass)
        try:
            await async_send_command(session, self._url, command, require_ok=True)
        except HomBotCommandError as err:
            _LOGGER.warning("HomBot %s command failed: %s", self._label, err)
            raise HomeAssistantError(f"HomBot {self._label} command failed") from err
        except Exception as err:  # noqa: BLE001 - report the service call as failed
            _LOGGER.exception("Unexpected HomBot %s command error", self._label)
            raise HomeAssistantError(f"HomBot {self._label} command failed") from err
        await _settle()
        await self._async_read_status(raise_on_error=True)

    async def _async_read_status(self, *, raise_on_error: bool = False) -> None:
        """Apply the state only when status.txt can be read.

        The read during entity setup logs and leaves the current state.
        The read after a command fails the service call instead, still
        without applying a state from the command alone.
        """

        session = async_get_clientsession(self.hass)
        try:
            attributes = await async_fetch_status(session, self._url)
        except HomBotStatusError as err:
            _LOGGER.warning("HomBot %s status read failed: %s", self._label, err)
            if raise_on_error:
                raise HomeAssistantError(
                    f"HomBot {self._label} status read failed"
                ) from err
            return
        except Exception as err:  # noqa: BLE001 - keep a setup read off other entities
            _LOGGER.exception("Unexpected HomBot %s status error", self._label)
            if raise_on_error:
                raise HomeAssistantError(
                    f"HomBot {self._label} status read failed"
                ) from err
            return
        self._apply_status(attributes)
        self.async_write_ha_state()

    def _on_status(self, attributes: dict[str, str]) -> None:
        """Apply a status.txt result from the vacuum poll."""

        self._apply_status(attributes)
        self.async_write_ha_state()
