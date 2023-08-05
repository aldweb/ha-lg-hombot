from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntry

from homeassistant.components.vacuum import STATE_CLEANING, STATE_DOCKED, STATE_PAUSED, STATE_RETURNING, StateVacuumEntity

from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from typing import Any
from urllib.parse import quote

from .const import DOMAIN, SPEED_NORMAL, SPEED_TURBO, SUPPORTED_SERVICES, SUPPORTED_SPEEDS


async def async_setup_entry(hass: HomeAssistant, config_entry: ConfigEntry, async_add_entites: AddEntitiesCallback) -> None:
    """Setup of Entry."""
    # get url of hombot
    url = config_entry.data.get("url")
    name = config_entry.data.get("name")

    # create instance
    hombot_vac = HombotVacuum(name, url)
    async_add_entites([hombot_vac])


class HombotVacuum(StateVacuumEntity):
    """Representation of a Hombot vacuum cleaner robot."""

    def __init__(self, name: str, url: str) -> None:
        """Initialize the vacuum."""
        self._name = name
        self._attr_name = name
        self._attr_supported_features = SUPPORTED_SERVICES

        self._url = url

        self._state = None
        self._battery_level = None
        self._fan_speed = SPEED_NORMAL
        self._fan_speed_list = SUPPORTED_SPEEDS

    @property
    def unique_id(self) -> str | None:
        return "hombot_" + self._url

    @property
    def device_info(self) -> DeviceInfo:
        """Return the device info."""
        device_info = DeviceInfo(
            identifiers={
                (DOMAIN, self.unique_id)
            },
            name=self._name,
            manufacturer="LG",
            model="Hombot",
            configuration_url=self._url
        )
        return device_info

    @property
    def state(self) -> str:
        """Return the status of the vacuum."""
        return self._state

    @property
    def battery_level(self) -> int:
        """Return the status of the vacuum."""
        return self._battery_level

    @property
    def fan_speed(self) -> str | None:
        return self._fan_speed

    @property
    def fan_speed_list(self) -> list[str]:
        return self._fan_speed_list

    async def async_update(self):
        """Update status properties."""
        # generate url
        url = self._url + "status.txt"

        # execute command
        session = async_get_clientsession(self.hass)
        response = await session.get(url)
        response_bytes = await response.read()
        response_body = response_bytes.decode("ascii")

        # parse body
        attributes = {}
        for line in response_body.splitlines():
            name, var = line.partition("=")[::2]
            attributes[name] = var.strip('"')

        # assign properties
        self._state = self.convert_state(attributes["JSON_ROBOT_STATE"])
        self._battery_level = int(attributes["JSON_BATTPERC"])
        if attributes["JSON_TURBO"] == "true":
            self._fan_speed = SPEED_TURBO
        else:
            self._fan_speed = SPEED_NORMAL

    def convert_state(self, current_state) -> str:
        """Converts the status of hombot to that of HomeAssistant -> see https://developers.home-assistant.io/docs/core/entity/vacuum/#states"""
        match current_state:
            case "CHARGING":
                return STATE_DOCKED
            case "BACKMOVING_INIT":
                return STATE_CLEANING
            case "WORKING":
                return STATE_CLEANING
            case "PAUSE":
                return STATE_PAUSED
            case "HOMING":
                return STATE_RETURNING
            case"DOCKING":
                return STATE_RETURNING

    async def async_start(self, **kwargs: Any) -> None:
        """Turn the vacuum on."""
        self._state = STATE_CLEANING
        await self.query('{"COMMAND":"CLEAN_START"}')

    async def async_pause(self, **kwargs: Any) -> None:
        """Turn the vacuum on."""
        self._state = STATE_PAUSED
        await self.query('{"COMMAND":"PAUSE"}')

    async def async_return_to_base(self, **kwargs: Any) -> None:
        """Turn the vacuum off."""
        self._state = STATE_RETURNING
        await self.query('{"COMMAND":"HOMING"}')

    async def async_stop(self, **kwargs: Any) -> None:
        """Turn the vacuum off."""
        self._state = STATE_RETURNING
        await self.query('{"COMMAND":"HOMING"}')

    async def async_set_fan_speed(self, fan_speed: str, **kwargs: Any) -> None:
        """Sets fan speed."""
        self._fan_speed = fan_speed
        if fan_speed == SPEED_TURBO:
            await self.query('{"COMMAND":{"TURBO":"true"}}')
        else:
            await self.query('{"COMMAND":{"TURBO":"false"}}')

    async def query(self, command):
        """Execute command."""
        # generate url
        url = self._url + "json.cgi?" + quote(command)

        # execute command
        session = async_get_clientsession(self.hass)
        response = await session.get(url)
        await response.read()

        return True
