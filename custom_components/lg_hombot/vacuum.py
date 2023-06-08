from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntry

from homeassistant.components.vacuum import VacuumEntity
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from homeassistant.helpers.aiohttp_client import async_get_clientsession

from typing import Any
from urllib.parse import quote

from .const import DOMAIN, SUPPORTED_SERVICES


async def async_setup_entry(hass: HomeAssistant, config_entry: ConfigEntry, async_add_entites: AddEntitiesCallback) -> None:
    """Setup of Entry."""
    # get url of hombot
    url = config_entry.data.get("url")
    name = config_entry.data.get("name")

    # create instance
    hombot_vac = HombotVacuum(name, url)
    async_add_entites([hombot_vac])


class HombotVacuum(VacuumEntity):
    """Representation of a Hombot vacuum cleaner robot."""

    def __init__(self, name: str, url: str) -> None:
        """Initialize the vacuum."""
        self._name = name
        self._attr_name = name
        self._attr_supported_features = SUPPORTED_SERVICES

        self._url = url

        self._state = False
        self._status = None
        self._battery_level = None

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
            model="Hombot"
        )
        return device_info

    @property
    def is_on(self) -> bool:
        """Return true if vacuum is on."""
        return self._state

    @property
    def status(self) -> str:
        """Return the status of the vacuum."""
        return self._status

    @property
    def battery_level(self) -> int:
        """Return the status of the vacuum."""
        return self._battery_level

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
        self._status = attributes["JSON_ROBOT_STATE"]
        self._state = self._status in ["WORKING", "BACKMOVING_INIT"]
        self._battery_level = int(attributes["JSON_BATTPERC"])

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the vacuum on."""
        self._state = True
        self._status = "WORKING"
        await self.query('{"COMMAND":"CLEAN_START"}')

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the vacuum off."""
        self._state = False
        self._status = "HOMING"
        await self.query('{"COMMAND":"HOMING"}')

    async def query(self, command):
        """Execute command."""
        # generate url
        url = self._url + "json.cgi?" + quote(command)

        # execute command
        session = async_get_clientsession(self.hass)
        response = await session.get(url)
        await response.read()

        return True
