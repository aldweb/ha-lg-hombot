"""LG Hombot integration."""

from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform

PLATFORMS = [
    "vacuum",
    "sensor"
]


async def async_setup_entry(hass: HomeAssistant, config_entry: ConfigEntry) -> bool:
    await hass.config_entries.async_forward_entry_setups(config_entry, PLATFORMS)
    return True
