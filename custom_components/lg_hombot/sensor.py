"""Support for LG Hombot battery sensor."""

from __future__ import annotations

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .device import normalize_url
from .entity import HombotStatusEntity


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the LG Hombot battery sensor."""
    url = normalize_url(config_entry.data.get("url"))
    name = config_entry.data.get("name")

    async_add_entities([HombotBatterySensor(hass, name, url)])


class HombotBatterySensor(HombotStatusEntity, SensorEntity):
    """Battery level, read from the status.txt the vacuum polls.

    The sensor makes no request of its own: it follows the vacuum update, so
    the robot is polled once per cycle.
    """

    _attr_device_class = SensorDeviceClass.BATTERY
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_name = "Battery"
    _attr_native_value: int | None = None
    _label = "battery"
    _unique_suffix = "battery"

    def _apply_status(self, attributes: dict[str, str]) -> None:
        """Map JSON_BATTPERC. A missing or invalid level makes the sensor unavailable."""

        try:
            level = int(attributes["JSON_BATTPERC"])
        except (KeyError, TypeError, ValueError):
            level = None
        if level is not None and not 0 <= level <= 100:
            level = None

        self._attr_native_value = level
        self._attr_available = level is not None
        self._attr_extra_state_attributes = {
            "charging": attributes.get("JSON_ROBOT_STATE") == "CHARGING"
        }
