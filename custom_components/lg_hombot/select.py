"""Select entities for the LG HomBot.

The historical-route selector loads its run list once when the entity is
added. A failure leaves the options empty and does not fail the config
entry. Nothing is selected until the user chooses a label, including
after a restart.

The cleaning-pattern selector is registered beside it. It does not use
the route list or the route image.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from functools import partial
from io import BytesIO
from typing import Any

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .bbl import BblLog
from .blk import BlkMap
from .cleaning_pattern import HombotCleaningPatternSelect
from .device import hombot_device_info, normalize_url
from .route_client import HomBotRouteError, async_download_cleaning_run
from .route_history import RouteHistoryState, get_route_history
from .route_list import HistoricalRun, RouteList, parse_maps_page, parse_route_list
from .route_list_client import (
    MAPS_PAGE_PATH,
    HomBotRouteListError,
    async_download_route_list,
)
from .route_render import render_route

_LOGGER = logging.getLogger(__name__)


def render_route_png(
    map_data: BlkMap,
    log: BblLog,
    time_zone: str | None = None,
) -> bytes:
    """Render one cleaning route to PNG. Intended to run in an executor."""

    try:
        image = render_route(map_data, log, time_zone=time_zone)
    except ValueError:
        raise HomBotRouteError("HomBot route image is invalid") from None

    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the route selector, then the cleaning-pattern selector."""

    url = normalize_url(config_entry.data.get("url"))
    name = config_entry.data.get("name")
    state = get_route_history(hass, config_entry.entry_id)
    async_add_entities([HombotRouteSelect(hass, name, url, state)])
    async_add_entities([HombotCleaningPatternSelect(hass, name, url)])


class HombotRouteSelect(SelectEntity):
    """Choose one historical cleaning run by its unique label."""

    _attr_has_entity_name = True
    _attr_name = "Cleaning route"
    _attr_should_poll = False
    _attr_current_option: str | None = None
    _attr_options: list[str] = []

    def __init__(
        self,
        hass: HomeAssistant,
        name: str,
        url: str,
        state: RouteHistoryState,
    ) -> None:
        """Initialize the selector. No run is selected."""

        self.hass = hass
        self._name = name
        self._url = url
        self._state = state
        self._attr_unique_id = f"hombot_{url}_route_select"
        self._attr_options = []
        self._attr_current_option = None

    @property
    def device_info(self) -> DeviceInfo:
        """Attach the selector to the same device as vacuum and camera."""

        return hombot_device_info(self._name, self._url)

    async def async_added_to_hass(self) -> None:
        """Load the run list once. Errors become an empty option list."""

        await super().async_added_to_hass()
        self._attr_current_option = None
        self._state.selected_bbl_name = None
        await self._async_load_runs()

    async def async_internal_added_to_hass(self) -> None:
        """Load options, then drop a selection restored by Home Assistant."""

        await super().async_internal_added_to_hass()
        self._attr_current_option = None
        self._state.selected_bbl_name = None

    async def async_select_option(self, option: str) -> None:
        """Download and render the run with this label.

        The same BBL file is not downloaded again after a success. A
        failure keeps the chosen option visible, clears the route PNG and
        releases the BBL name so that option can be chosen again. An older
        download cannot change a newer selection.
        """

        run = self._run_for_label(option)
        if run is None:
            return
        if self._state.selected_bbl_name == run.bbl_name:
            return

        self._state.generation += 1
        generation = self._state.generation
        self._state.selected_bbl_name = run.bbl_name
        self._attr_current_option = option
        self._state.clear_png()
        self._write_state()

        try:
            session = async_get_clientsession(self.hass)
            cleaning = await async_download_cleaning_run(
                session,
                self._url,
                run.bbl_name,
            )
        except HomBotRouteError as err:
            self._fail_current(generation, err)
            return
        except Exception:  # noqa: BLE001 - keep route faults off other entities
            _LOGGER.exception("Unexpected HomBot route download error")
            self._fail_current(generation, None)
            return

        if generation != self._state.generation:
            return
        if cleaning.blk_name != run.blk_name:
            _LOGGER.warning(
                "HomBot route map does not match the selected run: "
                "list file %s, MAPDATA file %s",
                run.blk_name,
                cleaning.blk_name,
            )
            self._fail_current(generation, None)
            return

        try:
            png = await self.hass.async_add_executor_job(
                partial(
                    render_route_png,
                    time_zone=self.hass.config.time_zone,
                ),
                cleaning.map_data,
                cleaning.log,
            )
        except HomBotRouteError as err:
            self._fail_current(generation, err)
            return
        except Exception:  # noqa: BLE001 - keep route faults off other entities
            _LOGGER.exception("Unexpected HomBot route render error")
            self._fail_current(generation, None)
            return

        if generation != self._state.generation:
            return
        self._state.publish_png(png, datetime.now(timezone.utc))

    def _run_for_label(self, option: str) -> HistoricalRun | None:
        """Return the listed run for this label, without choosing another."""

        for run in self._state.runs:
            if run.label == option:
                return run
        return None

    async def _async_fetch_route_list(self, session: Any) -> RouteList:
        """Read the status document, else the maps page of older firmware.

        Firmware without ``sites/maps/status.html`` answers with a text
        error instead of JSON. Its ``maps.html`` page lists the same runs.
        Only the maps page error is raised when both fail.
        """

        try:
            payload = await async_download_route_list(session, self._url)
            return parse_route_list(payload)
        except (HomBotRouteListError, ValueError):
            _LOGGER.debug("HomBot status document unavailable, trying maps page")
        payload = await async_download_route_list(
            session, self._url, path=MAPS_PAGE_PATH
        )
        return parse_maps_page(payload)

    async def _async_load_runs(self) -> None:
        """Fetch the status document once and keep every parsed run."""

        try:
            session = async_get_clientsession(self.hass)
            parsed = await self._async_fetch_route_list(session)
        except (HomBotRouteListError, ValueError) as err:
            _LOGGER.warning("HomBot route list update failed: %s", err)
            self._apply_runs(())
            return
        except Exception:  # noqa: BLE001 - list errors must not fail setup
            _LOGGER.exception("Unexpected HomBot route list error")
            self._apply_runs(())
            return

        self._apply_runs(parsed.runs)

    def _apply_runs(self, runs: tuple[HistoricalRun, ...]) -> None:
        """Replace the options. The current selection is not changed here."""

        self._state.runs = tuple(runs)
        self._attr_options = [run.label for run in self._state.runs]

    def _fail_current(self, generation: int, err: HomBotRouteError | None) -> None:
        """Release this attempt when it is still the latest selection.

        The visible option stays. The route PNG is cleared and the BBL
        name is freed, so the same option can start a new download. A
        newer selection is left untouched.
        """

        if generation != self._state.generation:
            return
        if err is not None:
            _LOGGER.warning("HomBot route update failed: %s", err)
        self._state.selected_bbl_name = None
        self._state.clear_png()

    def _write_state(self) -> None:
        """Publish the selector state when Home Assistant is attached."""

        writer = getattr(self, "async_write_ha_state", None)
        if writer is not None:
            writer()
