"""Support for LG Hombot battery sensor."""
from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntry

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import PERCENTAGE

from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import DOMAIN


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the LG Hombot battery sensor."""
    url = config_entry.data.get("url")
    name = config_entry.data.get("name")

    async_add_entities([HombotBatterySensor(name, url)])


class HombotBatterySensor(SensorEntity):
    """Representation of LG Hombot battery level sensor."""

    _attr_device_class = SensorDeviceClass.BATTERY
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_has_entity_name = True

    def __init__(self, name: str, url: str) -> None:
        """Initialize the battery sensor."""
        self._name = name
        self._url = url
        self._attr_name = "Battery"
        self._attr_unique_id = f"hombot_{url}_battery"
        self._attr_native_value = None

    @property
    def device_info(self) -> DeviceInfo:
        """Return the device info."""
        return DeviceInfo(
            identifiers={
                (DOMAIN, f"hombot_{self._url}")
            },
            name=self._name,
            manufacturer="LG",
            model="Hombot",
            configuration_url=self._url
        )

    async def async_update(self) -> None:
        """Fetch battery level from the device."""
        try:
            url = self._url + "status.txt"
            session = async_get_clientsession(self.hass)
            response = await session.get(url)
            response_bytes = await response.read()
            response_body = response_bytes.decode("ascii")

            # parse body
            attributes = {}
            for line in response_body.splitlines():
                name, var = line.partition("=")[::2]
                attributes[name] = var.strip('"')

            battery_level = attributes.get("JSON_BATTPERC")
            if battery_level is not None:
                self._attr_native_value = int(battery_level)

        except Exception:
            self._attr_native_value = None
          
