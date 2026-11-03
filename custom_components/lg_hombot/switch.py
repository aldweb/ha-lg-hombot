"""Repeat switch for the LG HomBot."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .entity import HombotStatusEntity

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


class HombotRepeatSwitch(HombotStatusEntity, SwitchEntity):
    """Repeat mode, read from status.txt and set with an explicit command."""

    _attr_name = "Repeat"
    _attr_is_on: bool | None = None
    _label = "repeat"
    _unique_suffix = "repeat"

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Enable Repeat, then read status.txt again."""

        await self._async_send_command(REPEAT_COMMAND_ON)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Disable Repeat, then read status.txt again."""

        await self._async_send_command(REPEAT_COMMAND_OFF)

    def _apply_status(self, attributes: dict[str, str]) -> None:
        """Map the exact JSON_REPEAT value. Anything else is unavailable."""

        value = attributes.get("JSON_REPEAT")
        if value not in ("true", "false"):
            self._attr_available = False
            self._attr_is_on = None
            return
        self._attr_available = True
        self._attr_is_on = value == "true"
