"""Tests for the HomBot learned-map image entity."""

from __future__ import annotations

import struct
from io import BytesIO

import pytest
from aiohttp import ClientSession, web
from aiohttp.test_utils import TestServer
from PIL import Image

from lg_hombot.blk import read_blk_bytes
from lg_hombot.const import DOMAIN
from lg_hombot.image import SCAN_INTERVAL, HombotMapImage, HombotRouteImage, async_setup_entry
from lg_hombot.map_client import MAP_PATH
from lg_hombot.map_render import render_map
from lg_hombot.route_history import ROUTE_HISTORY_KEY, RouteHistoryState

MAP_ROUTE = "/" + MAP_PATH


def _synthetic_blk() -> bytes:
    header = (1, 100, 10000, 50, 50000, 100, 1, 1000, 1, 10, 100)
    cells = bytearray(100)
    cells[0] = 3
    data = bytearray(struct.pack("<11i", *header))
    data.extend(struct.pack("<4i", 0, 0, 0, 0))
    data.extend(cells)
    return bytes(data)


class _Hass:
    def __init__(self) -> None:
        self.data: dict[str, object] = {}
        self.executor_calls = 0

    async def async_add_executor_job(self, func, *args):
        self.executor_calls += 1
        return func(*args)


def _entity(hass: _Hass, url: str = "http://192.168.0.10:6260/") -> HombotMapImage:
    return HombotMapImage(hass, "Living room", url)


async def _serve(payload: bytes | None, status: int = 200) -> TestServer:
    async def handler(_: web.Request) -> web.Response:
        if status != 200:
            return web.Response(status=status, text="no map")
        return web.Response(body=payload or b"", content_type="application/octet-stream")

    app = web.Application()
    app.router.add_get(MAP_ROUTE, handler)
    server = TestServer(app)
    await server.start_server()
    return server


def test_map_entity_identity_matches_the_hombot_device() -> None:
    entity = _entity(_Hass())
    url = "http://192.168.0.10:6260/"

    assert entity._attr_name == "Learned map"
    assert entity._attr_unique_id == f"hombot_{url}_map"
    assert entity._attr_unique_id != f"hombot_{url}_camera"
    assert entity.device_info["identifiers"] == {("lg_hombot", f"hombot_{url}")}
    assert entity.device_info["name"] == "Living room"
    assert entity.device_info["manufacturer"] == "LG"
    assert entity.device_info["configuration_url"] == url
    assert entity.content_type == "image/png"
    assert entity._attr_should_poll is True
    assert SCAN_INTERVAL.total_seconds() == 300


@pytest.mark.asyncio
async def test_successful_update_returns_a_png(monkeypatch: pytest.MonkeyPatch) -> None:
    hass = _Hass()
    entity = _entity(hass)
    vacuum_state = {"activity": "docked"}
    server = await _serve(_synthetic_blk())
    try:
        async with ClientSession() as session:
            monkeypatch.setattr(
                "lg_hombot.image.async_get_clientsession",
                lambda given: session if given is hass else (_ for _ in ()).throw(
                    AssertionError("session requested for a different Home Assistant")
                ),
            )
            entity._url = str(server.make_url("/"))
            await entity.async_update()

        png = await entity.async_image()
        assert png is not None
        assert png.startswith(b"\x89PNG")
        plain = render_map(read_blk_bytes(_synthetic_blk()))
        with Image.open(BytesIO(png)) as image:
            assert image.format == "PNG"
            assert image.mode == "RGB"
            assert image.size[0] == plain.size[0] == 80
            assert image.size[1] > plain.size[1]
            assert image.crop((0, 0, plain.size[0], plain.size[1])).tobytes() == (
                plain.tobytes()
            )
        assert entity._attr_image_last_updated is not None
        assert hass.executor_calls == 1
        assert hass.data == {}
        assert vacuum_state == {"activity": "docked"}
    finally:
        await server.close()


@pytest.mark.asyncio
async def test_download_error_does_not_publish_an_image(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hass = _Hass()
    entity = _entity(hass)
    other_entities = {"camera": b"jpeg-bytes", "vacuum": "docked"}
    server = await _serve(None, status=404)
    try:
        async with ClientSession() as session:
            monkeypatch.setattr(
                "lg_hombot.image.async_get_clientsession",
                lambda given: session,
            )
            entity._url = str(server.make_url("/"))
            await entity.async_update()

        assert await entity.async_image() is None
        assert entity._attr_image_last_updated is None
        assert other_entities == {"camera": b"jpeg-bytes", "vacuum": "docked"}
        assert hass.data == {}
    finally:
        await server.close()


@pytest.mark.asyncio
async def test_failed_update_clears_the_previous_png(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hass = _Hass()
    entity = _entity(hass)
    calls = {"count": 0}

    async def handler(_: web.Request) -> web.Response:
        calls["count"] += 1
        if calls["count"] == 1:
            return web.Response(body=_synthetic_blk())
        return web.Response(status=404, text="missing")

    app = web.Application()
    app.router.add_get(MAP_ROUTE, handler)
    server = TestServer(app)
    await server.start_server()
    try:
        async with ClientSession() as session:
            monkeypatch.setattr(
                "lg_hombot.image.async_get_clientsession",
                lambda given: session,
            )
            entity._url = str(server.make_url("/"))
            await entity.async_update()
            first = await entity.async_image()
            await entity.async_update()

        assert first is not None
        assert first.startswith(b"\x89PNG")
        assert await entity.async_image() is None
        assert entity._attr_image_last_updated is None
    finally:
        await server.close()


@pytest.mark.asyncio
async def test_truncated_download_does_not_publish_an_image(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hass = _Hass()
    entity = _entity(hass)
    server = await _serve(b"\x00" * 20)
    try:
        async with ClientSession() as session:
            monkeypatch.setattr(
                "lg_hombot.image.async_get_clientsession",
                lambda given: session,
            )
            entity._url = str(server.make_url("/"))
            await entity.async_update()
        assert await entity.async_image() is None
        assert hass.executor_calls == 1
    finally:
        await server.close()


class _Entry:
    def __init__(self, url: str) -> None:
        self.entry_id = "entry-1"
        self.data = {"url": url, "name": "Living room"}


def _prepare(hass: _Hass, url: str) -> _Entry:
    hass.data[DOMAIN] = { "entry-1": {ROUTE_HISTORY_KEY: RouteHistoryState()}}
    return _Entry(url)


async def _setup_images(hass: _Hass, url: str):
    """Add both images the way Home Assistant applies update_before_add."""

    calls: list[tuple[list, bool]] = []

    async def add(entities, update_before_add: bool = False) -> None:
        found = list(entities)
        calls.append((found, update_before_add))
        if update_before_add:
            for entity in found:
                await entity.async_update()

    await async_setup_entry(hass, _prepare(hass, url), add)
    return calls


@pytest.mark.asyncio
async def test_first_add_downloads_the_learned_map_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hass = _Hass()
    requests: list[str] = []

    async def handler(request: web.Request) -> web.Response:
        requests.append(request.path)
        if request.path == MAP_ROUTE:
            return web.Response(body=_synthetic_blk())
        return web.Response(status=404, text="not the learned map")

    app = web.Application()
    app.router.add_get("/{tail:.*}", handler)
    server = TestServer(app)
    await server.start_server()
    try:
        async with ClientSession() as session:
            monkeypatch.setattr(
                "lg_hombot.image.async_get_clientsession",
                lambda given: session if given is hass else (_ for _ in ()).throw(
                    AssertionError("session requested for a different Home Assistant")
                ),
            )
            calls = await _setup_images(hass, str(server.make_url("/")))

        assert len(calls) == 2
        map_entities, map_update = calls[0]
        route_entities, route_update = calls[1]
        assert map_update is True
        assert route_update is False
        assert len(map_entities) == 1
        assert isinstance(map_entities[0], HombotMapImage)
        assert len(route_entities) == 1
        assert isinstance(route_entities[0], HombotRouteImage)
        assert map_entities[0]._attr_should_poll is True
        assert SCAN_INTERVAL.total_seconds() == 300
        assert route_entities[0]._attr_should_poll is False
        png = await map_entities[0].async_image()
        assert png is not None
        assert png.startswith(b"\x89PNG")
        assert map_entities[0]._attr_image_last_updated is not None
        assert await route_entities[0].async_image() is None
        assert requests == [MAP_ROUTE]
    finally:
        await server.close()


@pytest.mark.asyncio
async def test_failed_initial_map_download_does_not_block_setup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hass = _Hass()
    server = await _serve(None, status=503)
    try:
        async with ClientSession() as session:
            monkeypatch.setattr(
                "lg_hombot.image.async_get_clientsession",
                lambda given: session,
            )
            calls = await _setup_images(hass, str(server.make_url("/")))

        learned = calls[0][0][0]
        route = calls[1][0][0]
        assert calls[0][1] is True
        assert calls[1][1] is False
        assert await learned.async_image() is None
        assert learned._attr_image_last_updated is None
        assert learned._attr_should_poll is True
        assert SCAN_INTERVAL.total_seconds() == 300
        assert await route.async_image() is None
        assert isinstance(route, HombotRouteImage)
    finally:
        await server.close()
