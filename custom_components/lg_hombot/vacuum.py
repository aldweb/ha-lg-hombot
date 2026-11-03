"""Vacuum entity for the LG HomBot."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.vacuum import StateVacuumEntity, VacuumActivity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import SPEED_NORMAL, SPEED_TURBO, SUPPORTED_SERVICES, SUPPORTED_SPEEDS
from .device import hombot_device_info, normalize_url
from .status import (
    HomBotCommandError,
    HomBotStatusError,
    async_fetch_status,
    async_send_command,
    publish_status,
)

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the vacuum from a config entry."""

    url = normalize_url(config_entry.data.get("url"))
    name = config_entry.data.get("name")
    async_add_entities([HombotVacuum(name, url)])


class HombotVacuum(StateVacuumEntity):
    """Representation of a Hombot vacuum cleaner robot."""

    def __init__(self, name: str, url: str) -> None:
        """Initialize the vacuum."""
        self._name = name
        self._attr_name = name
        self._attr_supported_features = SUPPORTED_SERVICES

        self._url = url

        self._activity = VacuumActivity.IDLE
        self._fan_speed = SPEED_NORMAL
        self._fan_speed_list = SUPPORTED_SPEEDS
        self._attr_unique_id = "hombot_" + self._url

    @property
    def unique_id(self) -> str | None:
        return self._attr_unique_id

    @property
    def device_info(self) -> DeviceInfo:
        """Return the device info."""
        return hombot_device_info(self._name, self._url)

    @property
    def activity(self) -> VacuumActivity:
        """Return the activity of the vacuum."""
        return self._activity

    @property
    def fan_speed(self) -> str | None:
        return self._fan_speed

    @property
    def fan_speed_list(self) -> list[str]:
        return self._fan_speed_list

    async def async_update(self) -> None:
        """Update status properties.

        An unreachable robot makes the entity unavailable and tells the other
        HomBot entities with an empty status, so none of them keeps showing
        the last known value.
        """
        session = async_get_clientsession(self.hass)
        try:
            attributes = await async_fetch_status(session, self._url)
        except HomBotStatusError as err:
            if self._attr_available is not False:
                _LOGGER.warning("HomBot %s is unavailable: %s", self._name, err)
            self._attr_available = False
            self._publish({})
            return

        if self._attr_available is False:
            _LOGGER.info("HomBot %s is back online", self._name)
        self._attr_available = True

        self._activity = self.convert_state(attributes.get("JSON_ROBOT_STATE"))
        if attributes.get("JSON_TURBO") == "true":
            self._fan_speed = SPEED_TURBO
        else:
            self._fan_speed = SPEED_NORMAL

        self._attr_extra_state_attributes = {
            "repeat_mode": attributes.get("JSON_REPEAT") == "true",
            "cleaning_mode": attributes.get("JSON_MODE"),
            "nickname": attributes.get("JSON_NICKNAME"),
            "firmware_version": attributes.get("JSON_VERSION"),
            "last_clean": attributes.get("CLREC_LAST_CLEAN"),
            "total_cleanings": attributes.get("CLREC_CURRENTBUMPING"),
        }
        self._publish(attributes)

    def _publish(self, attributes: dict[str, str]) -> None:
        """Share a status with the other entities. This never raises."""
        if getattr(self, "hass", None) is not None:
            publish_status(self.hass, self._url, attributes)

    def convert_state(self, current_state) -> VacuumActivity:
        """Convert the Hombot status to Home Assistant VacuumActivity."""
        match current_state:
            case "CHARGING":
                return VacuumActivity.DOCKED
            case "BACKMOVING_INIT" | "WORKING":
                return VacuumActivity.CLEANING
            case "PAUSE":
                return VacuumActivity.PAUSED
            case "HOMING" | "DOCKING":
                return VacuumActivity.RETURNING
            case _:
                return VacuumActivity.IDLE

    async def async_start(self, **kwargs: Any) -> None:
        """Start cleaning."""
        await self.query('{"COMMAND":"CLEAN_START"}')
        self._set_activity(VacuumActivity.CLEANING)

    async def async_pause(self, **kwargs: Any) -> None:
        """Pause cleaning."""
        await self.query('{"COMMAND":"PAUSE"}')
        self._set_activity(VacuumActivity.PAUSED)

    async def async_return_to_base(self, **kwargs: Any) -> None:
        """Send the robot back to its base."""
        await self.query('{"COMMAND":"HOMING"}')
        self._set_activity(VacuumActivity.RETURNING)

    async def async_stop(self, **kwargs: Any) -> None:
        """Stop cleaning. The robot has no stop in place: it returns home."""
        await self.query('{"COMMAND":"HOMING"}')
        self._set_activity(VacuumActivity.RETURNING)

    async def async_set_fan_speed(self, fan_speed: str, **kwargs: Any) -> None:
        """Set the fan speed."""
        turbo = "true" if fan_speed == SPEED_TURBO else "false"
        await self.query('{"COMMAND":{"TURBO":"%s"}}' % turbo)
        self._fan_speed = SPEED_TURBO if fan_speed == SPEED_TURBO else SPEED_NORMAL
        self._write_state()

    def _set_activity(self, activity: VacuumActivity) -> None:
        """Show the activity the robot was just told to take, until the next poll."""
        self._activity = activity
        self._write_state()

    def _write_state(self) -> None:
        writer = getattr(self, "async_write_ha_state", None)
        if writer is not None and getattr(self, "hass", None) is not None:
            writer()

    async def query(self, command) -> bool:
        """Send a command. A command that did not reach the robot fails the service call."""
        session = async_get_clientsession(self.hass)
        try:
            await async_send_command(session, self._url, command, require_ok=True)
        except HomBotCommandError as err:
            _LOGGER.warning("HomBot %s command failed: %s", self._name, err)
            raise HomeAssistantError(f"HomBot {self._name} command failed") from err
        return True
