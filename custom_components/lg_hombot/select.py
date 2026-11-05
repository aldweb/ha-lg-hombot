"""Select entities for the LG HomBot.

The historical-route selector loads its run list once when the entity is
added. A failure leaves the options empty and does not fail the config
entry. Once the list is loaded, the most recent run is selected in the
background, so the route image is not empty after a start or a reload.
A run the user chooses meanwhile is never replaced.

The list is read again, REFRESH_DELAY_SECONDS after the robot comes back
to CHARGING (or is reachable again), because a cleaning has just ended.
The most recent
run is selected again when the user has chosen nothing, or last chose
the run that was the most recent. A run chosen by the user that is
still listed is kept. A run that is no longer on the robot is dropped
from the selection and from the route image. A refresh that fails
changes nothing. A robot with no run at all gives an empty option list.

The cleaning-pattern selector is registered beside it. It does not use
the route list or the route image.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
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
from .const import DOMAIN
from .device import hombot_device_info, normalize_url
from .route_client import HomBotRouteError, async_download_cleaning_run
from .route_history import RouteHistoryState, get_route_history
from .route_list import (
    HistoricalRun,
    RouteList,
    latest_run,
    parse_maps_page,
    parse_route_list,
)
from .route_list_client import (
    MAPS_PAGE_PATH,
    HomBotRouteListError,
    async_download_route_list,
)
from .route_render import render_route
from .status import listen_status

_LOGGER = logging.getLogger(__name__)

ROBOT_STATE_KEY = "JSON_ROBOT_STATE"
ROBOT_STATE_CHARGING = "CHARGING"

# The robot may still be writing the files of the cleaning that just ended
# when it reports CHARGING, so the list is read a few seconds later.
REFRESH_DELAY_SECONDS = 5


def _run_key(run: HistoricalRun) -> tuple[str, str]:
    """Identify a run by its files. A label can change when the list does."""

    return (run.bbl_name, run.blk_name)


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
    """Choose one historical cleaning run by its unique label.

    The most recent run is selected once the list is loaded.
    """

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
        self._auto_select_task: asyncio.Task | None = None
        self._refresh_task: asyncio.Task | None = None
        self._remove_status_listener: Callable[[], None] | None = None
        # None is an unreachable robot. Nothing is known before the first status.
        self._status_seen = False
        self._last_robot_state: str | None = None

    @property
    def device_info(self) -> DeviceInfo:
        """Attach the selector to the same device as vacuum and camera."""

        return hombot_device_info(self._name, self._url)

    async def async_added_to_hass(self) -> None:
        """Load the run list once, then select the latest run in the background.

        Errors become an empty option list. The download and the render do
        not delay the setup of the entity.
        """

        await super().async_added_to_hass()
        self._attr_current_option = None
        self._state.selected_bbl_name = None
        await self._async_load_runs()
        self._start_auto_select()
        self._remove_status_listener = listen_status(
            self.hass, self._url, self._on_status
        )

    async def async_will_remove_from_hass(self) -> None:
        """Stop following the robot and stop work that has not finished."""

        if self._remove_status_listener is not None:
            self._remove_status_listener()
            self._remove_status_listener = None
        for task in (self._auto_select_task, self._refresh_task):
            if task is not None and not task.done():
                task.cancel()
        self._auto_select_task = None
        self._refresh_task = None
        await super().async_will_remove_from_hass()

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

    def _start_auto_select(self) -> None:
        """Select the most recent run without waiting for its download."""

        latest = latest_run(self._state.runs)
        if latest is None:
            return
        # Not eager: the first state write must come after the entity is added.
        self._auto_select_task = self.hass.async_create_background_task(
            self._async_auto_select(latest.label),
            f"{DOMAIN} select latest route",
            eager_start=False,
        )

    async def _async_auto_select(self, label: str) -> None:
        """Select this run unless the user already chose one."""

        if self._attr_current_option is not None:
            return
        await self.async_select_option(label)

    def _on_status(self, attributes: dict[str, str]) -> None:
        """Read the run list again when the robot comes back to CHARGING.

        The first status heard is only remembered: the list was just read.
        An empty status means the robot could not be reached; the next
        CHARGING after it counts as a return, since a cleaning may have
        ended while the robot was out of reach.
        """

        state = attributes.get(ROBOT_STATE_KEY) if attributes else None
        was_known = self._status_seen
        previous = self._last_robot_state
        self._status_seen = True
        self._last_robot_state = state

        if state == ROBOT_STATE_CHARGING and was_known and previous != state:
            self._start_refresh()

    def _start_refresh(self) -> None:
        """Start one refresh. One already running, delay included, is enough."""

        task = self._refresh_task
        if task is not None and not task.done():
            return
        self._refresh_task = self.hass.async_create_background_task(
            self._async_refresh_runs(),
            f"{DOMAIN} refresh cleaning routes",
            eager_start=False,
        )

    async def _async_refresh_runs(self) -> None:
        """Wait a few seconds, then read the list again.

        A failed read keeps the options and selection.
        """

        await asyncio.sleep(REFRESH_DELAY_SECONDS)
        try:
            session = async_get_clientsession(self.hass)
            parsed = await self._async_fetch_route_list(session)
        except (HomBotRouteListError, ValueError) as err:
            _LOGGER.warning("HomBot route list refresh failed: %s", err)
            return
        except Exception:  # noqa: BLE001 - a refresh must not break other entities
            _LOGGER.exception("Unexpected HomBot route list refresh error")
            return

        await self._async_apply_refreshed_runs(parsed.runs)

    async def _async_apply_refreshed_runs(
        self, runs: tuple[HistoricalRun, ...]
    ) -> None:
        """Replace the options, then decide what stays selected.

        The most recent run is selected again when the user has chosen
        nothing, last chose the run that was the most recent, or chose a
        run the robot no longer has. A run chosen by the user that is
        still listed stays selected, with its current label.
        """

        old_latest = latest_run(self._state.runs)
        chosen = self._run_for_label(self._attr_current_option or "")
        new_latest = latest_run(runs)

        kept = None
        if chosen is not None:
            kept = next((run for run in runs if _run_key(run) == _run_key(chosen)), None)

        self._state.runs = tuple(runs)
        self._attr_options = [run.label for run in runs]

        follows_latest = (
            chosen is None
            or kept is None
            or (old_latest is not None and _run_key(chosen) == _run_key(old_latest))
        )

        if kept is not None:
            # The label can differ from the previous list: same run, new label.
            self._attr_current_option = kept.label
        else:
            self._drop_selection()

        self._write_state()

        if new_latest is not None and follows_latest:
            await self.async_select_option(new_latest.label)

    def _drop_selection(self) -> None:
        """Forget the selected run and its route image, and ignore a download in flight."""

        self._state.generation += 1
        self._attr_current_option = None
        self._state.selected_bbl_name = None
        self._state.clear_png()

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
