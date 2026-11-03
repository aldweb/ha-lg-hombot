"""Cleaning pattern select for the LG HomBot."""

from __future__ import annotations

from homeassistant.components.select import SelectEntity

from .entity import HombotStatusEntity

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


class HombotCleaningPatternSelect(HombotStatusEntity, SelectEntity):
    """Cleaning pattern, read from status.txt and set with an explicit command."""

    _attr_name = "Cleaning pattern"
    _attr_current_option: str | None = None
    _attr_options = [PATTERN_ZIGZAG, PATTERN_CELL]
    _label = "cleaning pattern"
    _unique_suffix = "cleaning_pattern"

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
        await self._async_send_command(command)

    def _apply_status(self, attributes: dict[str, str]) -> None:
        """Map the exact JSON_MODE value. Anything else is unavailable."""

        option = _OPTION_BY_MODE.get(attributes.get("JSON_MODE"))
        self._attr_available = option is not None
        self._attr_current_option = option
