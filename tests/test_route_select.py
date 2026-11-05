"""Tests for the historical-route selector, route image, and shared state."""

from __future__ import annotations

import asyncio
import json
import struct
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from io import BytesIO

from unittest.mock import ANY

import pytest
from aiohttp import ClientSession, web
from aiohttp.test_utils import TestServer
from PIL import Image

from lg_hombot.__init__ import PLATFORMS, async_setup_entry, async_unload_entry
from lg_hombot.camera import HombotCamera
from lg_hombot.const import DOMAIN
from lg_hombot.image import SCAN_INTERVAL, HombotMapImage, HombotRouteImage
from lg_hombot.map_client import MAP_PATH
from lg_hombot.route_client import CleaningRun, HomBotRouteError
from lg_hombot.route_history import ROUTE_HISTORY_KEY, RouteHistoryState, get_route_history
from lg_hombot.route_list import HistoricalRun
from lg_hombot.route_list_client import ROUTE_LIST_PATH
from lg_hombot.route_render import footer_height
from lg_hombot.select import HombotRouteSelect, SelectEntity
from lg_hombot.status import STATUS_LISTENERS_KEY, listen_status
from lg_hombot.sensor import HombotBatterySensor
from lg_hombot.switch import HombotRepeatSwitch
from lg_hombot.vacuum import HombotVacuum


BLK_101 = "MAPDATA20010101010121_123456_101.blk"
BBL_101 = "cleanlog20010101010101_U_101.bbl"
BLK_103 = "MAPDATA20010101010121_000001_103.blk"
BBL_103 = "cleanlog20010101010121_U_103.bbl"
BLK_OTHER = "MAPDATA20010101010121_000002_104.blk"
LABEL_ONE = "01.01.2001 - 01:01"
LABEL_101 = "01.01.2001 - 01:01 (101)"
LABEL_103 = "01.01.2001 - 01:01 (103)"
BLACKBOX = "/.../usr/data/blackbox/"


def _synthetic_blk() -> bytes:
    header = (1, 100, 10000, 50, 50000, 100, 1, 1000, 1, 10, 100)
    cells = bytearray(100)
    cells[0] = 3
    data = bytearray(struct.pack("<11i", *header))
    data.extend(struct.pack("<4i", 7, 11, 5050, 30))
    data.extend(cells)
    return bytes(data)


def _bbl(reference: str, *, usable_pose: bool = True) -> bytes:
    lines = [f"20010101010121.000001,BULK,MAPDATA,{reference}"]
    if usable_pose:
        lines.insert(0, "20010101010101.000001,POSI,RobotPose,100,200,90")
        lines.append("20010101010200.000001,STRN,State,End Cleaning")
    else:
        # No POS record: the route reader rejects the log.
        lines.append("20010101010200.000001,STRN,State,End Cleaning")
    return ("20010101010101_U_101\n" + "\n".join(lines) + "\n").encode("ascii")


def _status(*runs: tuple[str, str]) -> bytes:
    return json.dumps(
        {
            "blackbox": BLACKBOX,
            "global_map": "/.../usr/data/MapReuseNavi",
            "maps": [blk for blk, _bbl_name in runs],
            "logs": [bbl for _blk_name, bbl in runs],
        }
    ).encode("ascii")


class _Entry:
    def __init__(self, url: str) -> None:
        self.entry_id = "entry-1"
        self.data = {"url": url, "name": "Living room"}


class _Config:
    def __init__(self) -> None:
        self.time_zone = "Europe/Amsterdam"


class _Hass:
    def __init__(self) -> None:
        self.data: dict[str, object] = {}
        self.config = _Config()
        self.executor_calls = 0
        self.forwarded: list[object] | None = None
        self.unloaded: list[object] | None = None
        self.unload_result = True
        self.config_entries = self
        # Latest-run selection: recorded and not run, unless a test sets run_background.
        self.run_background = False
        self.background_names: list[str] = []
        self.background_tasks: list[asyncio.Task] = []

    def async_create_background_task(self, coro, name, eager_start=True):
        assert eager_start is False
        self.background_names.append(name)
        if not self.run_background:
            coro.close()
            return None
        task = asyncio.get_running_loop().create_task(coro)
        self.background_tasks.append(task)
        return task

    async def async_add_executor_job(self, func, *args):
        self.executor_calls += 1
        return func(*args)

    async def async_forward_entry_setups(self, _entry, platforms) -> None:
        self.forwarded = list(platforms)
        state = get_route_history(self, "entry-1")
        assert isinstance(state, RouteHistoryState)

    async def async_unload_platforms(self, _entry, platforms) -> bool:
        self.unloaded = list(platforms)
        return self.unload_result


def _pair(hass: _Hass, url: str) -> tuple[HombotRouteSelect, HombotRouteImage, HombotMapImage]:
    state = get_route_history(hass, "entry-1")
    select = HombotRouteSelect(hass, "Living room", url, state)
    route = HombotRouteImage(hass, "Living room", url, state)
    learned = HombotMapImage(hass, "Living room", url)
    return select, route, learned


async def _serve(handler) -> TestServer:
    app = web.Application()
    app.router.add_get("/" + ROUTE_LIST_PATH, handler)
    app.router.add_get("/" + MAP_PATH, handler)
    app.router.add_get(BLACKBOX + "{name}", handler)
    server = TestServer(app)
    await server.start_server()
    return server


def test_platforms_keep_vacuum_camera_sensor_and_add_select() -> None:
    assert PLATFORMS == ["vacuum", "sensor", "camera", "image", "select", "switch"]
    assert HombotVacuum.__module__.endswith("vacuum")
    assert HombotCamera.__module__.endswith("camera")
    assert HombotBatterySensor.__module__.endswith("sensor")
    assert HombotRepeatSwitch.__module__.endswith("switch")


@pytest.mark.asyncio
async def test_setup_creates_route_state_before_platforms_and_unload_removes_it() -> None:
    hass = _Hass()
    entry = _Entry("http://192.168.0.10:6260/")

    assert await async_setup_entry(hass, entry) is True
    state = get_route_history(hass, entry.entry_id)
    assert state.runs == ()
    assert state.selected_bbl_name is None
    assert state.png is None
    assert state.rendered_at is None
    assert hass.forwarded == PLATFORMS
    assert hass.data[DOMAIN][entry.entry_id][ROUTE_HISTORY_KEY] is state

    assert await async_unload_entry(hass, entry) is True
    assert DOMAIN not in hass.data
    assert hass.unloaded == PLATFORMS


@pytest.mark.asyncio
async def test_unload_prunes_status_listeners_once_entities_are_gone() -> None:
    hass = _Hass()
    entry = _Entry("http://192.168.0.10:6260/")
    await async_setup_entry(hass, entry)
    remove = listen_status(hass, entry.data["url"], lambda _attributes: None)

    # An entity is still registered: its bucket must survive the unload.
    assert await async_unload_entry(hass, entry) is True
    assert hass.data[DOMAIN] == {STATUS_LISTENERS_KEY: {entry.data["url"]: [ANY]}}

    remove()
    await async_setup_entry(hass, entry)
    assert await async_unload_entry(hass, entry) is True
    assert DOMAIN not in hass.data


@pytest.mark.asyncio
async def test_failed_unload_keeps_the_shared_state() -> None:
    hass = _Hass()
    entry = _Entry("http://192.168.0.10:6260/")
    await async_setup_entry(hass, entry)
    hass.unload_result = False

    assert await async_unload_entry(hass, entry) is False
    assert get_route_history(hass, entry.entry_id).png is None


def test_route_entities_use_the_hombot_device_and_do_not_poll() -> None:
    hass = _Hass()
    url = "http://192.168.0.10:6260/"
    hass.data[DOMAIN] = { "entry-1": {ROUTE_HISTORY_KEY: RouteHistoryState()}}
    select, route, learned = _pair(hass, url)

    assert select._attr_name == "Cleaning route"
    assert select._attr_unique_id == f"hombot_{url}_route_select"
    assert select._attr_should_poll is False
    assert route._attr_name == "Route"
    assert route._attr_unique_id == f"hombot_{url}_route"
    assert route._attr_should_poll is False
    assert route.content_type == "image/png"
    assert learned._attr_name == "Learned map"
    assert learned._attr_unique_id == f"hombot_{url}_map"
    assert learned._attr_should_poll is True
    assert SCAN_INTERVAL.total_seconds() == 300
    assert select.device_info["identifiers"] == {("lg_hombot", f"hombot_{url}")}
    assert route.device_info["identifiers"] == learned.device_info["identifiers"]
    assert not hasattr(route, "_png")
    assert learned._png is None


@pytest.mark.asyncio
async def test_load_publishes_unique_labels_and_schedules_the_latest_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[str] = []

    async def handler(request: web.Request) -> web.Response:
        seen.append(request.path)
        return web.Response(body=_status((BLK_101, BBL_101), (BLK_103, BBL_103)))

    server = await _serve(handler)
    try:
        hass = _Hass()
        url = str(server.make_url("/"))
        await async_setup_entry(hass, _Entry(url))
        select, route, learned = _pair(hass, url)
        async with ClientSession() as session:
            monkeypatch.setattr(
                "lg_hombot.select.async_get_clientsession",
                lambda given: session,
            )
            await select.async_added_to_hass()
            await route.async_added_to_hass()

        assert select.options == [LABEL_101, LABEL_103]
        assert len(select.options) == len(set(select.options))
        assert select.current_option is None
        assert select._state.selected_bbl_name is None
        assert await route.async_image() is None
        assert learned._png is None
        assert seen == ["/" + ROUTE_LIST_PATH]
        assert hass.background_names == ["lg_hombot select latest route"]
    finally:
        await server.close()


@pytest.mark.asyncio
async def test_older_firmware_lists_runs_from_the_maps_page(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """lg.srv V2.51 answers 200 with a text error for status.html."""

    from lg_hombot.route_list_client import MAPS_PAGE_PATH
    from test_route_list import MAPS_PAGE

    seen: list[str] = []

    async def handler(request: web.Request) -> web.Response:
        seen.append(request.path)
        if request.path == "/" + MAPS_PAGE_PATH:
            return web.Response(text=MAPS_PAGE)
        return web.Response(text="file not found : 401")

    app = web.Application()
    app.router.add_get("/" + ROUTE_LIST_PATH, handler)
    app.router.add_get("/" + MAPS_PAGE_PATH, handler)
    server = TestServer(app)
    await server.start_server()
    try:
        hass = _Hass()
        url = str(server.make_url("/"))
        await async_setup_entry(hass, _Entry(url))
        select, _route, _learned = _pair(hass, url)
        async with ClientSession() as session:
            monkeypatch.setattr(
                "lg_hombot.select.async_get_clientsession",
                lambda given: session,
            )
            await select.async_added_to_hass()

        assert select.options == ["03.11.2026 - 12:39", "03.11.2026 - 12:38"]
        assert seen == ["/" + ROUTE_LIST_PATH, "/" + MAPS_PAGE_PATH]
    finally:
        await server.close()


@pytest.mark.asyncio
async def test_invalid_list_leaves_the_options_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def handler(_: web.Request) -> web.Response:
        return web.Response(body=b"{")

    server = await _serve(handler)
    try:
        hass = _Hass()
        url = str(server.make_url("/"))
        await async_setup_entry(hass, _Entry(url))
        select, _route, _learned = _pair(hass, url)
        async with ClientSession() as session:
            monkeypatch.setattr(
                "lg_hombot.select.async_get_clientsession",
                lambda given: session,
            )
            await select.async_added_to_hass()
        assert select.options == []
        assert select.current_option is None
    finally:
        await server.close()


@pytest.mark.asyncio
async def test_list_http_404_leaves_the_options_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def handler(_: web.Request) -> web.Response:
        return web.Response(status=404, text="missing")

    server = await _serve(handler)
    try:
        hass = _Hass()
        url = str(server.make_url("/"))
        await async_setup_entry(hass, _Entry(url))
        select, _route, _learned = _pair(hass, url)
        async with ClientSession() as session:
            monkeypatch.setattr(
                "lg_hombot.select.async_get_clientsession",
                lambda given: session,
            )
            await select.async_added_to_hass()
        assert select.options == []
        assert select.current_option is None
    finally:
        await server.close()


@pytest.mark.asyncio
async def test_restored_option_is_cleared_without_a_download(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[str] = []

    async def handler(request: web.Request) -> web.Response:
        seen.append(request.path)
        return web.Response(body=_status((BLK_101, BBL_101)))

    async def restore(self) -> None:
        await HombotRouteSelect.async_added_to_hass(self)
        self._attr_current_option = self.options[0]
        self._state.selected_bbl_name = BBL_101

    server = await _serve(handler)
    try:
        hass = _Hass()
        url = str(server.make_url("/"))
        await async_setup_entry(hass, _Entry(url))
        select, _route, _learned = _pair(hass, url)
        monkeypatch.setattr(SelectEntity, "async_internal_added_to_hass", restore)
        async with ClientSession() as session:
            monkeypatch.setattr(
                "lg_hombot.select.async_get_clientsession",
                lambda given: session,
            )
            await select.async_internal_added_to_hass()
        assert select.current_option is None
        assert select._state.selected_bbl_name is None
        assert seen == ["/" + ROUTE_LIST_PATH]
    finally:
        await server.close()


@pytest.mark.asyncio
async def test_selection_downloads_one_bbl_and_its_blk_and_publishes_png(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[str] = []
    blk = _synthetic_blk()
    reference = f"/usr/data/blackbox/{BLK_101}"

    async def handler(request: web.Request) -> web.Response:
        seen.append(request.path)
        if request.path.endswith("status.html"):
            return web.Response(body=_status((BLK_101, BBL_101)))
        if request.path.endswith(BBL_101):
            return web.Response(body=_bbl(reference))
        if request.path.endswith(BLK_101):
            return web.Response(body=blk)
        return web.Response(status=404, text="missing")

    server = await _serve(handler)
    try:
        hass = _Hass()
        url = str(server.make_url("/"))
        await async_setup_entry(hass, _Entry(url))
        select, route, learned = _pair(hass, url)
        learned._png = b"learned-map"
        other = {"camera": b"jpeg", "vacuum": "docked"}
        async with ClientSession() as session:
            monkeypatch.setattr(
                "lg_hombot.select.async_get_clientsession",
                lambda given: session,
            )
            await select.async_added_to_hass()
            await route.async_added_to_hass()
            seen.clear()
            assert select.options == [LABEL_ONE]
            await select.async_select_option(LABEL_ONE)

        png = await route.async_image()
        assert png is not None
        assert png.startswith(b"\x89PNG")
        with Image.open(BytesIO(png)) as image:
            assert image.format == "PNG"
            assert image.size == (
                80,
                80 + footer_height(80, ("0.01m²", "1s", "0.22m", "0")),
            )
        assert select.current_option == LABEL_ONE
        assert select._state.selected_bbl_name == BBL_101
        assert select._state.rendered_at is not None
        assert route._attr_image_last_updated == select._state.rendered_at
        assert seen == [BLACKBOX + BBL_101, BLACKBOX + BLK_101]
        assert hass.executor_calls == 1
        assert learned._png == b"learned-map"
        assert other == {"camera": b"jpeg", "vacuum": "docked"}

        seen.clear()
        await select.async_select_option(LABEL_ONE)
        assert seen == []
        assert select._state.selected_bbl_name == BBL_101
        assert await route.async_image() == png
    finally:
        await server.close()


@pytest.mark.asyncio
async def test_mapdata_mismatch_clears_the_route_and_keeps_the_learned_map(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    seen: list[str] = []

    async def handler(request: web.Request) -> web.Response:
        seen.append(request.path)
        if request.path.endswith("status.html"):
            return web.Response(body=_status((BLK_101, BBL_101)))
        if request.path.endswith(BBL_101):
            return web.Response(body=_bbl(f"/usr/data/blackbox/{BLK_OTHER}"))
        if request.path.endswith(BLK_OTHER):
            return web.Response(body=_synthetic_blk())
        return web.Response(status=404, text="missing")

    server = await _serve(handler)
    try:
        hass = _Hass()
        url = str(server.make_url("/"))
        await async_setup_entry(hass, _Entry(url))
        select, route, learned = _pair(hass, url)
        learned._png = b"learned-map"
        async with ClientSession() as session:
            monkeypatch.setattr(
                "lg_hombot.select.async_get_clientsession",
                lambda given: session,
            )
            await select.async_added_to_hass()
            await route.async_added_to_hass()
            seen.clear()
            with caplog.at_level("WARNING"):
                await select.async_select_option(LABEL_ONE)

        assert await route.async_image() is None
        assert select._state.rendered_at is None
        assert select.current_option == LABEL_ONE
        assert seen == [BLACKBOX + BBL_101, BLACKBOX + BLK_OTHER]
        assert BLK_101 not in "".join(seen)
        assert hass.executor_calls == 0
        assert learned._png == b"learned-map"
        assert BLK_101 in caplog.text
        assert BLK_OTHER in caplog.text
        assert "http://" not in caplog.text
    finally:
        await server.close()


@pytest.mark.asyncio
async def test_failed_download_clears_only_the_route_png(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def handler(request: web.Request) -> web.Response:
        if request.path.endswith("status.html"):
            return web.Response(
                body=_status((BLK_101, BBL_101), (BLK_103, BBL_103))
            )
        if request.path.endswith(BBL_101):
            return web.Response(body=_bbl(f"/usr/data/blackbox/{BLK_101}"))
        if request.path.endswith(BLK_101):
            return web.Response(body=_synthetic_blk())
        return web.Response(status=404, text="missing")

    server = await _serve(handler)
    try:
        hass = _Hass()
        url = str(server.make_url("/"))
        await async_setup_entry(hass, _Entry(url))
        select, route, learned = _pair(hass, url)
        learned._png = b"learned-map"
        async with ClientSession() as session:
            monkeypatch.setattr(
                "lg_hombot.select.async_get_clientsession",
                lambda given: session,
            )
            await select.async_added_to_hass()
            await route.async_added_to_hass()
            await select.async_select_option(LABEL_101)
            assert await route.async_image() is not None
            await select.async_select_option(LABEL_103)

        assert select.current_option == LABEL_103
        assert await route.async_image() is None
        assert learned._png == b"learned-map"
        assert learned._attr_image_last_updated is None
    finally:
        await server.close()


@pytest.mark.asyncio
async def test_failed_render_clears_only_the_route_png(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def handler(request: web.Request) -> web.Response:
        if request.path.endswith("status.html"):
            return web.Response(body=_status((BLK_101, BBL_101)))
        if request.path.endswith(BBL_101):
            return web.Response(
                body=_bbl(f"/usr/data/blackbox/{BLK_101}", usable_pose=False)
            )
        if request.path.endswith(BLK_101):
            return web.Response(body=_synthetic_blk())
        return web.Response(status=404, text="missing")

    server = await _serve(handler)
    try:
        hass = _Hass()
        url = str(server.make_url("/"))
        await async_setup_entry(hass, _Entry(url))
        select, route, learned = _pair(hass, url)
        learned._png = b"learned-map"
        async with ClientSession() as session:
            monkeypatch.setattr(
                "lg_hombot.select.async_get_clientsession",
                lambda given: session,
            )
            await select.async_added_to_hass()
            await route.async_added_to_hass()
            await select.async_select_option(LABEL_ONE)

        assert select.current_option == LABEL_ONE
        assert await route.async_image() is None
        assert select._state.rendered_at is None
        assert learned._png == b"learned-map"
        assert hass.executor_calls == 1
    finally:
        await server.close()


@pytest.mark.asyncio
async def test_failed_bbl_download_can_be_retried(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempts = {"bbl": 0}
    seen: list[str] = []

    async def handler(request: web.Request) -> web.Response:
        seen.append(request.path)
        if request.path.endswith("status.html"):
            return web.Response(body=_status((BLK_101, BBL_101)))
        if request.path.endswith(BBL_101):
            attempts["bbl"] += 1
            if attempts["bbl"] == 1:
                return web.Response(status=404, text="missing")
            return web.Response(body=_bbl(f"/usr/data/blackbox/{BLK_101}"))
        if request.path.endswith(BLK_101):
            return web.Response(body=_synthetic_blk())
        return web.Response(status=404, text="missing")

    server = await _serve(handler)
    try:
        async with _loaded_route(server, monkeypatch) as (
            _hass,
            select,
            route,
            learned,
        ):
            seen.clear()
            await select.async_select_option(LABEL_ONE)

            assert select.current_option == LABEL_ONE
            assert select._state.selected_bbl_name is None
            assert await route.async_image() is None
            assert seen == [BLACKBOX + BBL_101]
            assert learned._png == b"learned-map"

            seen.clear()
            await select.async_select_option(LABEL_ONE)
            png = await route.async_image()
            assert png is not None and png.startswith(b"\x89PNG")
            assert select.current_option == LABEL_ONE
            assert select._state.selected_bbl_name == BBL_101
            assert seen == [BLACKBOX + BBL_101, BLACKBOX + BLK_101]
            assert learned._png == b"learned-map"
    finally:
        await server.close()


@pytest.mark.asyncio
async def test_failed_blk_download_can_be_retried(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempts = {"blk": 0}
    seen: list[str] = []

    async def handler(request: web.Request) -> web.Response:
        seen.append(request.path)
        if request.path.endswith("status.html"):
            return web.Response(body=_status((BLK_101, BBL_101)))
        if request.path.endswith(BBL_101):
            return web.Response(body=_bbl(f"/usr/data/blackbox/{BLK_101}"))
        if request.path.endswith(BLK_101):
            attempts["blk"] += 1
            if attempts["blk"] == 1:
                return web.Response(status=404, text="missing")
            return web.Response(body=_synthetic_blk())
        return web.Response(status=404, text="missing")

    server = await _serve(handler)
    try:
        async with _loaded_route(server, monkeypatch) as (
            _hass,
            select,
            route,
            learned,
        ):
            seen.clear()
            await select.async_select_option(LABEL_ONE)

            assert select.current_option == LABEL_ONE
            assert select._state.selected_bbl_name is None
            assert await route.async_image() is None
            assert seen == [BLACKBOX + BBL_101, BLACKBOX + BLK_101]
            assert learned._png == b"learned-map"

            seen.clear()
            await select.async_select_option(LABEL_ONE)
            png = await route.async_image()
            assert png is not None and png.startswith(b"\x89PNG")
            assert select._state.selected_bbl_name == BBL_101
            assert seen == [BLACKBOX + BBL_101, BLACKBOX + BLK_101]
            assert learned._png == b"learned-map"
    finally:
        await server.close()


@pytest.mark.asyncio
async def test_blk_mismatch_can_be_retried(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempts = {"bbl": 0}
    seen: list[str] = []

    async def handler(request: web.Request) -> web.Response:
        seen.append(request.path)
        if request.path.endswith("status.html"):
            return web.Response(body=_status((BLK_101, BBL_101)))
        if request.path.endswith(BBL_101):
            attempts["bbl"] += 1
            reference = BLK_OTHER if attempts["bbl"] == 1 else BLK_101
            return web.Response(body=_bbl(f"/usr/data/blackbox/{reference}"))
        if request.path.endswith(BLK_101) or request.path.endswith(BLK_OTHER):
            return web.Response(body=_synthetic_blk())
        return web.Response(status=404, text="missing")

    server = await _serve(handler)
    try:
        async with _loaded_route(server, monkeypatch) as (
            _hass,
            select,
            route,
            learned,
        ):
            seen.clear()
            await select.async_select_option(LABEL_ONE)

            assert select.current_option == LABEL_ONE
            assert select._state.selected_bbl_name is None
            assert await route.async_image() is None
            assert seen == [BLACKBOX + BBL_101, BLACKBOX + BLK_OTHER]
            assert learned._png == b"learned-map"

            seen.clear()
            await select.async_select_option(LABEL_ONE)
            png = await route.async_image()
            assert png is not None and png.startswith(b"\x89PNG")
            assert select.current_option == LABEL_ONE
            assert select._state.selected_bbl_name == BBL_101
            assert seen == [BLACKBOX + BBL_101, BLACKBOX + BLK_101]
            assert learned._png == b"learned-map"
    finally:
        await server.close()


@pytest.mark.asyncio
async def test_failed_render_can_be_retried(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempts = {"bbl": 0}

    async def handler(request: web.Request) -> web.Response:
        if request.path.endswith("status.html"):
            return web.Response(body=_status((BLK_101, BBL_101)))
        if request.path.endswith(BBL_101):
            attempts["bbl"] += 1
            return web.Response(
                body=_bbl(
                    f"/usr/data/blackbox/{BLK_101}",
                    usable_pose=attempts["bbl"] > 1,
                )
            )
        if request.path.endswith(BLK_101):
            return web.Response(body=_synthetic_blk())
        return web.Response(status=404, text="missing")

    server = await _serve(handler)
    try:
        async with _loaded_route(server, monkeypatch) as (
            hass,
            select,
            route,
            learned,
        ):
            await select.async_select_option(LABEL_ONE)

            assert select.current_option == LABEL_ONE
            assert select._state.selected_bbl_name is None
            assert await route.async_image() is None
            assert learned._png == b"learned-map"
            assert hass.executor_calls == 1

            await select.async_select_option(LABEL_ONE)
            png = await route.async_image()
            assert png is not None and png.startswith(b"\x89PNG")
            assert select.current_option == LABEL_ONE
            assert select._state.selected_bbl_name == BBL_101
            assert hass.executor_calls == 2
            assert learned._png == b"learned-map"
    finally:
        await server.close()


@pytest.mark.asyncio
async def test_unknown_option_does_not_select_another_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    async def fake_download(_session, _url, bbl_name, **_kwargs):
        calls.append(bbl_name)
        raise HomBotRouteError("HomBot cleaning log returned HTTP 404")

    hass = _Hass()
    url = "http://192.168.0.10:6260/"
    await async_setup_entry(hass, _Entry(url))
    select, route, learned = _pair(hass, url)
    select._state.runs = (
        HistoricalRun(BBL_101, BLK_101, "101", LABEL_101),
        HistoricalRun(BBL_103, BLK_103, "103", LABEL_103),
    )
    select._attr_options = [LABEL_101, LABEL_103]
    select._attr_current_option = LABEL_101
    select._state.selected_bbl_name = BBL_101
    select._state.publish_png(b"png-101", select._state.rendered_at or _now())
    learned._png = b"learned-map"
    monkeypatch.setattr(
        "lg_hombot.select.async_download_cleaning_run",
        fake_download,
    )

    await select.async_select_option("missing route")

    assert calls == []
    assert select.current_option == LABEL_101
    assert await route.async_image() == b"png-101"
    assert learned._png == b"learned-map"


@pytest.mark.asyncio
async def test_stale_download_cannot_overwrite_a_newer_route(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gates = {BBL_101: asyncio.Event(), BBL_103: asyncio.Event()}
    started = {BBL_101: asyncio.Event(), BBL_103: asyncio.Event()}
    calls: list[str] = []
    built = {
        BBL_101: _cleaning(BBL_101, BLK_101, b"PNG-101"),
        BBL_103: _cleaning(BBL_103, BLK_103, b"PNG-103"),
    }

    async def fake_download(_session, _url, bbl_name, **_kwargs):
        calls.append(bbl_name)
        started[bbl_name].set()
        await gates[bbl_name].wait()
        return built[bbl_name]

    def render(map_data, log, time_zone=None) -> bytes:
        del map_data, time_zone
        return log.log_id.encode("ascii")

    hass = _Hass()
    url = "http://192.168.0.10:6260/"
    await async_setup_entry(hass, _Entry(url))
    select, route, learned = _pair(hass, url)
    _install_two_runs(select)
    learned._png = b"learned-map"
    monkeypatch.setattr("lg_hombot.select.async_download_cleaning_run", fake_download)
    monkeypatch.setattr("lg_hombot.select.render_route_png", render)
    monkeypatch.setattr(
        "lg_hombot.select.async_get_clientsession",
        lambda _hass: object(),
    )

    first = asyncio.create_task(select.async_select_option(LABEL_101))
    await asyncio.wait_for(started[BBL_101].wait(), timeout=2)
    second = asyncio.create_task(select.async_select_option(LABEL_103))
    await asyncio.wait_for(started[BBL_103].wait(), timeout=2)
    try:
        gates[BBL_101].set()
        await first
        assert await route.async_image() is None
        assert select.current_option == LABEL_103

        gates[BBL_103].set()
        await second
        assert await route.async_image() == b"PNG-103"
        assert select._state.selected_bbl_name == BBL_103
        assert learned._png == b"learned-map"
        assert calls == [BBL_101, BBL_103]
    finally:
        gates[BBL_101].set()
        gates[BBL_103].set()
        for task in (first, second):
            if not task.done():
                task.cancel()


@pytest.mark.asyncio
async def test_stale_failure_does_not_clear_the_newer_route(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gates = {BBL_101: asyncio.Event(), BBL_103: asyncio.Event()}
    started = {BBL_101: asyncio.Event(), BBL_103: asyncio.Event()}

    async def fake_download(_session, _url, bbl_name, **_kwargs):
        started[bbl_name].set()
        await gates[bbl_name].wait()
        if bbl_name == BBL_101:
            raise HomBotRouteError("HomBot cleaning log returned HTTP 404")
        return _cleaning(bbl_name, BLK_103, b"PNG-103")

    def render(map_data, log, time_zone=None) -> bytes:
        del map_data, time_zone
        return log.log_id.encode("ascii")

    hass = _Hass()
    url = "http://192.168.0.10:6260/"
    await async_setup_entry(hass, _Entry(url))
    select, route, learned = _pair(hass, url)
    _install_two_runs(select)
    learned._png = b"learned-map"
    monkeypatch.setattr("lg_hombot.select.async_download_cleaning_run", fake_download)
    monkeypatch.setattr("lg_hombot.select.render_route_png", render)
    monkeypatch.setattr(
        "lg_hombot.select.async_get_clientsession",
        lambda _hass: object(),
    )

    first = asyncio.create_task(select.async_select_option(LABEL_101))
    await asyncio.wait_for(started[BBL_101].wait(), timeout=2)
    second = asyncio.create_task(select.async_select_option(LABEL_103))
    await asyncio.wait_for(started[BBL_103].wait(), timeout=2)
    try:
        gates[BBL_103].set()
        await second
        assert await route.async_image() == b"PNG-103"

        gates[BBL_101].set()
        await first
        assert await route.async_image() == b"PNG-103"
        assert select.current_option == LABEL_103
        assert select._state.selected_bbl_name == BBL_103
        assert learned._png == b"learned-map"
    finally:
        gates[BBL_101].set()
        gates[BBL_103].set()
        for task in (first, second):
            if not task.done():
                task.cancel()


@asynccontextmanager
async def _loaded_route(
    server: TestServer,
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[tuple[_Hass, HombotRouteSelect, HombotRouteImage, HombotMapImage]]:
    """Load one run list against the local server and keep its session open."""

    hass = _Hass()
    url = str(server.make_url("/"))
    await async_setup_entry(hass, _Entry(url))
    select, route, learned = _pair(hass, url)
    learned._png = b"learned-map"
    async with ClientSession() as session:
        monkeypatch.setattr(
            "lg_hombot.select.async_get_clientsession",
            lambda given: session,
        )
        await select.async_added_to_hass()
        await route.async_added_to_hass()
        yield hass, select, route, learned


def _now():
    from datetime import datetime, timezone

    return datetime.now(timezone.utc)


def _install_two_runs(select: HombotRouteSelect) -> None:
    select._state.runs = (
        HistoricalRun(BBL_101, BLK_101, "101", LABEL_101),
        HistoricalRun(BBL_103, BLK_103, "103", LABEL_103),
    )
    select._attr_options = [LABEL_101, LABEL_103]


def _cleaning(bbl_name: str, blk_name: str, png: bytes) -> CleaningRun:
    del png
    from lg_hombot.bbl import read_bbl_bytes
    from lg_hombot.blk import read_blk_bytes

    log_id = "PNG-101" if bbl_name == BBL_101 else "PNG-103"
    reference = f"/usr/data/blackbox/{blk_name}"
    text = (
        f"{log_id}\n"
        "20010101010101.000001,POSI,RobotPose,100,200,90\n"
        f"20010101010121.000001,BULK,MAPDATA,{reference}\n"
    ).encode("ascii")
    return CleaningRun(
        bbl_name=bbl_name,
        bbl_bytes=text,
        log=read_bbl_bytes(text),
        blk_name=blk_name,
        blk_bytes=_synthetic_blk(),
        map_data=read_blk_bytes(_synthetic_blk()),
    )


BLK_NEW = "MAPDATA20010202020221_000009_105.blk"
BBL_NEW = "cleanlog20010202020202_U_105.bbl"
LABEL_NEW = "02.02.2001 - 02:02"


def _latest_handler(seen: list[str], *, newest_first: bool):
    """A robot with an old run and a newer one, listed in either order."""
    blk = _synthetic_blk()
    runs = [(BLK_101, BBL_101), (BLK_NEW, BBL_NEW)]
    if newest_first:
        runs.reverse()

    async def handler(request: web.Request) -> web.Response:
        seen.append(request.path)
        if request.path.endswith("status.html"):
            return web.Response(body=_status(*runs))
        if request.path.endswith(BBL_NEW):
            return web.Response(body=_bbl(f"/usr/data/blackbox/{BLK_NEW}"))
        if request.path.endswith(BBL_101):
            return web.Response(body=_bbl(f"/usr/data/blackbox/{BLK_101}"))
        if request.path.endswith((BLK_NEW, BLK_101)):
            return web.Response(body=blk)
        return web.Response(status=404, text="missing")

    return handler


@pytest.mark.asyncio
@pytest.mark.parametrize("newest_first", [False, True])
async def test_latest_run_is_selected_after_load_whatever_the_list_order(
    monkeypatch: pytest.MonkeyPatch, newest_first: bool
) -> None:
    seen: list[str] = []
    server = await _serve(_latest_handler(seen, newest_first=newest_first))
    try:
        hass = _Hass()
        hass.run_background = True
        url = str(server.make_url("/"))
        await async_setup_entry(hass, _Entry(url))
        select, route, _learned = _pair(hass, url)
        async with ClientSession() as session:
            monkeypatch.setattr(
                "lg_hombot.select.async_get_clientsession", lambda given: session
            )
            await select.async_added_to_hass()
            await route.async_added_to_hass()
            # Setup is not held back by the download.
            assert select.current_option is None
            assert await route.async_image() is None
            await asyncio.gather(*hass.background_tasks)

        assert select.current_option == LABEL_NEW
        assert select._state.selected_bbl_name == BBL_NEW
        png = await route.async_image()
        assert png is not None and png.startswith(b"\x89PNG")
        assert BLACKBOX + BBL_NEW in seen and BLACKBOX + BBL_101 not in seen
    finally:
        await server.close()


@pytest.mark.asyncio
async def test_a_run_chosen_by_the_user_is_not_replaced_by_the_latest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[str] = []
    server = await _serve(_latest_handler(seen, newest_first=False))
    try:
        hass = _Hass()
        hass.run_background = True
        url = str(server.make_url("/"))
        await async_setup_entry(hass, _Entry(url))
        select, _route, _learned = _pair(hass, url)
        async with ClientSession() as session:
            monkeypatch.setattr(
                "lg_hombot.select.async_get_clientsession", lambda given: session
            )
            await select.async_added_to_hass()
            # The user picks before the background task gets to run.
            await select.async_select_option(LABEL_ONE)
            await asyncio.gather(*hass.background_tasks)

        assert select.current_option == LABEL_ONE
        assert select._state.selected_bbl_name == BBL_101
        assert BLACKBOX + BBL_NEW not in seen
    finally:
        await server.close()


@pytest.mark.asyncio
async def test_a_failed_latest_download_can_be_retried_by_choosing_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def handler(request: web.Request) -> web.Response:
        if request.path.endswith("status.html"):
            return web.Response(body=_status((BLK_101, BBL_101)))
        return web.Response(status=500, text="boom")

    server = await _serve(handler)
    try:
        hass = _Hass()
        hass.run_background = True
        url = str(server.make_url("/"))
        await async_setup_entry(hass, _Entry(url))
        select, route, _learned = _pair(hass, url)
        async with ClientSession() as session:
            monkeypatch.setattr(
                "lg_hombot.select.async_get_clientsession", lambda given: session
            )
            await select.async_added_to_hass()
            await asyncio.gather(*hass.background_tasks)

        assert select.options == [LABEL_ONE]
        assert select.current_option == LABEL_ONE
        assert select._state.selected_bbl_name is None
        assert await route.async_image() is None
    finally:
        await server.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["invalid", "missing"])
async def test_nothing_is_scheduled_without_a_usable_list(
    monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    async def handler(_: web.Request) -> web.Response:
        if failure == "invalid":
            return web.Response(body=b"{")
        return web.Response(status=404, text="missing")

    server = await _serve(handler)
    try:
        hass = _Hass()
        hass.run_background = True
        url = str(server.make_url("/"))
        await async_setup_entry(hass, _Entry(url))
        select, _route, _learned = _pair(hass, url)
        async with ClientSession() as session:
            monkeypatch.setattr(
                "lg_hombot.select.async_get_clientsession", lambda given: session
            )
            await select.async_added_to_hass()
        assert hass.background_names == []
        assert select.options == [] and select.current_option is None
    finally:
        await server.close()


@pytest.mark.asyncio
async def test_removing_the_entity_cancels_a_pending_latest_selection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started = asyncio.Event()

    async def handler(request: web.Request) -> web.Response:
        if request.path.endswith("status.html"):
            return web.Response(body=_status((BLK_101, BBL_101)))
        started.set()
        await asyncio.sleep(30)
        return web.Response(status=500)

    server = await _serve(handler)
    try:
        hass = _Hass()
        hass.run_background = True
        url = str(server.make_url("/"))
        await async_setup_entry(hass, _Entry(url))
        select, _route, _learned = _pair(hass, url)
        async with ClientSession() as session:
            monkeypatch.setattr(
                "lg_hombot.select.async_get_clientsession", lambda given: session
            )
            await select.async_added_to_hass()
            await asyncio.wait_for(started.wait(), 5)
            await select.async_will_remove_from_hass()
            (task,) = hass.background_tasks
            with pytest.raises(asyncio.CancelledError):
                await task
        assert task.cancelled()
    finally:
        await server.close()
