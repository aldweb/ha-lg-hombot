"""Local-server tests for one historical HomBot cleaning-run download."""

from __future__ import annotations

import asyncio
import struct

import pytest
from aiohttp import ClientSession, web
from aiohttp.test_utils import TestServer

from lg_hombot.map_client import MAX_MAP_BYTES, async_download_learned_map
from lg_hombot.route_client import (
    MAX_BBL_BYTES,
    CleaningRun,
    HomBotRouteError,
    async_download_cleaning_run,
    blackbox_url,
)


BBL_NAME = "cleanlog20010101010101_U_109.bbl"
BLK_NAME = "MAPDATA20010101010121_123456_101.blk"
MAPDATA = f"/usr/data/blackbox/{BLK_NAME}"
BLACKBOX = "/.../usr/data/blackbox/"


def _synthetic_blk() -> bytes:
    header = (1, 100, 10000, 50, 50000, 100, 1, 1000, 1, 10, 100)
    cells = bytearray(100)
    cells[0] = 3
    data = bytearray(struct.pack("<11i", *header))
    data.extend(struct.pack("<4i", 7, 11, 5050, 30))
    data.extend(cells)
    data.extend(b"TR")
    return bytes(data)


def _truncated_blk() -> bytes:
    header = (1, 100, 10000, 50, 50000, 100, 1, 1000, 1, 10, 100)
    data = bytearray(struct.pack("<11i", *header))
    data.extend(struct.pack("<4i", 1, 2, 5050, 3))
    return bytes(data)


def _bbl(*lines: str, log_id: str = "20010101010101_U_109") -> bytes:
    return (log_id + "\n" + "\n".join(lines) + "\n").encode("ascii")


def _valid_bbl(reference: str = MAPDATA) -> bytes:
    return _bbl(
        "20010101010101.000001,POSI,RobotPose,0,0,1",
        f"20010101010121.000001,BULK,MAPDATA,{reference}",
    )


async def _start(handler) -> TestServer:
    app = web.Application()
    app.router.add_get(BLACKBOX + "{name}", handler)
    app.router.add_get("/.../usr/data/MapReuseNavi", handler)
    server = TestServer(app)
    await server.start_server()
    return server


def test_blackbox_url_appends_the_file_without_replacing_the_base() -> None:
    assert blackbox_url("http://192.168.0.10:6260/", BBL_NAME) == (
        f"http://192.168.0.10:6260/.../usr/data/blackbox/{BBL_NAME}"
    )
    assert blackbox_url("http://192.168.0.10:6260", BLK_NAME) == (
        f"http://192.168.0.10:6260/.../usr/data/blackbox/{BLK_NAME}"
    )
    assert blackbox_url("http://192.168.0.10:6260/hombot/", BBL_NAME) == (
        f"http://192.168.0.10:6260/hombot/.../usr/data/blackbox/{BBL_NAME}"
    )
    assert MAX_BBL_BYTES == 256 * 1024
    assert MAX_MAP_BYTES == 1_048_576


@pytest.mark.asyncio
async def test_valid_run_uses_the_exact_mapdata_filename() -> None:
    seen: list[str] = []
    blk = _synthetic_blk()

    async def handler(request: web.Request) -> web.Response:
        seen.append(request.path)
        if request.path.endswith(BBL_NAME):
            return web.Response(body=_valid_bbl(), content_type="text/plain")
        if request.path.endswith(BLK_NAME):
            return web.Response(body=blk, content_type="application/octet-stream")
        return web.Response(status=404, text="missing")

    server = await _start(handler)
    try:
        async with ClientSession() as session:
            run = await async_download_cleaning_run(
                session,
                str(server.make_url("/")),
                BBL_NAME,
            )
    finally:
        await server.close()

    assert seen == [BLACKBOX + BBL_NAME, BLACKBOX + BLK_NAME]
    assert isinstance(run, CleaningRun)
    assert run.bbl_name == BBL_NAME
    assert run.blk_name == BLK_NAME
    assert run.bbl_bytes == _valid_bbl()
    assert run.blk_bytes == blk
    assert run.log.log_id == "20010101010101_U_109"
    assert run.log.map_references == (MAPDATA,)
    assert run.map_data.header.used_blocks == 1
    assert run.map_data.blocks[0].x == 50
    assert run.map_data.blocks[0].y == 50


@pytest.mark.asyncio
async def test_bbl_http_404_does_not_request_a_map() -> None:
    seen: list[str] = []

    async def handler(request: web.Request) -> web.Response:
        seen.append(request.path)
        return web.Response(status=404, text="missing")

    server = await _start(handler)
    try:
        async with ClientSession() as session:
            with pytest.raises(HomBotRouteError, match="HTTP 404") as caught:
                await async_download_cleaning_run(
                    session,
                    str(server.make_url("/")),
                    BBL_NAME,
                )
    finally:
        await server.close()

    assert seen == [BLACKBOX + BBL_NAME]
    assert "http://" not in str(caught.value)


@pytest.mark.asyncio
async def test_blk_http_404_returns_no_partial_run() -> None:
    seen: list[str] = []
    result: CleaningRun | None = None

    async def handler(request: web.Request) -> web.Response:
        seen.append(request.path)
        if request.path.endswith(BBL_NAME):
            return web.Response(body=_valid_bbl())
        return web.Response(status=404, text="missing")

    server = await _start(handler)
    try:
        async with ClientSession() as session:
            with pytest.raises(HomBotRouteError, match="HTTP 404") as caught:
                result = await async_download_cleaning_run(
                    session,
                    str(server.make_url("/")),
                    BBL_NAME,
                )
    finally:
        await server.close()

    assert result is None
    assert seen == [BLACKBOX + BBL_NAME, BLACKBOX + BLK_NAME]
    assert BLK_NAME not in str(caught.value)
    assert "http://" not in str(caught.value)


@pytest.mark.asyncio
async def test_bbl_larger_than_the_cap_is_rejected() -> None:
    async def handler(request: web.Request) -> web.StreamResponse:
        response = web.StreamResponse(status=200)
        await response.prepare(request)
        await response.write(b"x" * (MAX_BBL_BYTES + 1))
        await response.write_eof()
        return response

    server = await _start(handler)
    try:
        async with ClientSession() as session:
            with pytest.raises(HomBotRouteError, match="larger than"):
                await async_download_cleaning_run(
                    session,
                    str(server.make_url("/")),
                    BBL_NAME,
                )
    finally:
        await server.close()


@pytest.mark.asyncio
async def test_blk_larger_than_the_map_cap_is_rejected() -> None:
    result: CleaningRun | None = None

    async def handler(request: web.Request) -> web.StreamResponse:
        if request.path.endswith(BBL_NAME):
            return web.Response(body=_valid_bbl())
        response = web.StreamResponse(status=200)
        await response.prepare(request)
        await response.write(b"x" * (MAX_MAP_BYTES + 1))
        await response.write_eof()
        return response

    server = await _start(handler)
    try:
        async with ClientSession() as session:
            with pytest.raises(HomBotRouteError, match="larger than") as caught:
                result = await async_download_cleaning_run(
                    session,
                    str(server.make_url("/")),
                    BBL_NAME,
                )
    finally:
        await server.close()

    assert result is None
    assert "HomBot cleaning map" in str(caught.value)


@pytest.mark.asyncio
async def test_empty_bbl_and_empty_blk_are_rejected() -> None:
    async def empty_bbl(_: web.Request) -> web.Response:
        return web.Response(body=b"")

    async def empty_blk(request: web.Request) -> web.Response:
        if request.path.endswith(BBL_NAME):
            return web.Response(body=_valid_bbl())
        return web.Response(body=b"")

    for handler, label in ((empty_bbl, "log"), (empty_blk, "map")):
        server = await _start(handler)
        try:
            async with ClientSession() as session:
                with pytest.raises(HomBotRouteError, match="empty") as caught:
                    await async_download_cleaning_run(
                        session,
                        str(server.make_url("/")),
                        BBL_NAME,
                    )
        finally:
            await server.close()
        assert label == "log" or "cleaning map" in str(caught.value)


@pytest.mark.asyncio
async def test_invalid_bbl_does_not_request_a_map() -> None:
    seen: list[str] = []

    async def handler(request: web.Request) -> web.Response:
        seen.append(request.path)
        return web.Response(body=b"\n")

    server = await _start(handler)
    try:
        async with ClientSession() as session:
            with pytest.raises(HomBotRouteError, match="cleaning log is invalid"):
                await async_download_cleaning_run(
                    session,
                    str(server.make_url("/")),
                    BBL_NAME,
                )
    finally:
        await server.close()

    assert seen == [BLACKBOX + BBL_NAME]


@pytest.mark.asyncio
async def test_truncated_blk_returns_no_partial_run() -> None:
    result: CleaningRun | None = None

    async def handler(request: web.Request) -> web.Response:
        if request.path.endswith(BBL_NAME):
            return web.Response(body=_valid_bbl())
        return web.Response(body=_truncated_blk())

    server = await _start(handler)
    try:
        async with ClientSession() as session:
            with pytest.raises(HomBotRouteError, match="cleaning map is invalid"):
                result = await async_download_cleaning_run(
                    session,
                    str(server.make_url("/")),
                    BBL_NAME,
                )
    finally:
        await server.close()

    assert result is None


@pytest.mark.asyncio
async def test_missing_mapdata_does_not_request_a_map() -> None:
    seen: list[str] = []

    async def handler(request: web.Request) -> web.Response:
        seen.append(request.path)
        return web.Response(body=_bbl("20010101010101.000001,POSI,RobotPose,0,0,1"))

    server = await _start(handler)
    try:
        async with ClientSession() as session:
            with pytest.raises(HomBotRouteError, match="no MAPDATA"):
                await async_download_cleaning_run(
                    session,
                    str(server.make_url("/")),
                    BBL_NAME,
                )
    finally:
        await server.close()

    assert seen == [BLACKBOX + BBL_NAME]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "name",
    [
        "",
        " ",
        "../secret.bbl",
        "/usr/data/blackbox/clean.bbl",
        ".../usr/data/blackbox/clean.bbl",
        "folder/clean.bbl",
        "folder\\clean.bbl",
        "C:clean.bbl",
        "clean.bbl/../../other.bbl",
        "clean..bbl",
        ".bbl",
        "clean.blk",
    ],
)
async def test_unsafe_bbl_name_makes_no_request(name: str) -> None:
    seen: list[str] = []

    async def handler(request: web.Request) -> web.Response:
        seen.append(request.path)
        return web.Response(status=500, text="should not be called")

    server = await _start(handler)
    try:
        async with ClientSession() as session:
            with pytest.raises(HomBotRouteError, match="file name is invalid"):
                await async_download_cleaning_run(
                    session,
                    str(server.make_url("/")),
                    name,
                )
    finally:
        await server.close()

    assert seen == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "reference",
    [
        "/tmp/MAPDATA.blk",
        "/usr/data/blackbox/../../etc/passwd",
        "/usr/data/other/MAPDATA.blk",
        ".../usr/data/blackbox/nested/MAPDATA.blk",
        "folder/MAPDATA.blk",
        "MAPDATA.blk/../../other.blk",
        "not-a-blk.bbl",
        "/usr/data/blackbox/",
    ],
)
async def test_unsafe_mapdata_name_does_not_download_that_path(reference: str) -> None:
    seen: list[str] = []

    async def handler(request: web.Request) -> web.Response:
        seen.append(request.path)
        return web.Response(body=_valid_bbl(reference))

    server = await _start(handler)
    try:
        async with ClientSession() as session:
            with pytest.raises(HomBotRouteError, match="map reference is invalid"):
                await async_download_cleaning_run(
                    session,
                    str(server.make_url("/")),
                    BBL_NAME,
                )
    finally:
        await server.close()

    assert seen == [BLACKBOX + BBL_NAME]


@pytest.mark.asyncio
async def test_download_timeout_hides_the_request_url() -> None:
    async def handler(_: web.Request) -> web.Response:
        await asyncio.sleep(1.5)
        return web.Response(body=_valid_bbl())

    server = await _start(handler)
    try:
        base_url = str(server.make_url("/"))
        async with ClientSession() as session:
            with pytest.raises(HomBotRouteError, match="timed out") as caught:
                await async_download_cleaning_run(
                    session,
                    base_url,
                    BBL_NAME,
                    timeout_seconds=0.2,
                )
    finally:
        await server.close()

    assert "http://" not in str(caught.value)
    assert BBL_NAME not in str(caught.value)


@pytest.mark.asyncio
async def test_content_length_must_match_the_body() -> None:
    async def handler(request: web.Request) -> web.StreamResponse:
        response = web.StreamResponse(
            status=200,
            headers={"Content-Length": "100"},
        )
        response.force_close()
        await response.prepare(request)
        await response.write(b"abc")
        await response.write_eof()
        if request.transport is not None:
            request.transport.close()
        return response

    server = await _start(handler)
    try:
        async with ClientSession() as session:
            with pytest.raises(HomBotRouteError, match="does not match") as caught:
                await async_download_cleaning_run(
                    session,
                    str(server.make_url("/")),
                    BBL_NAME,
                    timeout_seconds=2,
                )
    finally:
        await server.close()

    assert "http://" not in str(caught.value)


@pytest.mark.asyncio
async def test_failed_route_download_leaves_the_learned_map_client_working() -> None:
    blk = _synthetic_blk()

    async def handler(request: web.Request) -> web.Response:
        if request.path == "/.../usr/data/MapReuseNavi":
            return web.Response(body=blk)
        return web.Response(status=404, text="missing")

    server = await _start(handler)
    try:
        base_url = str(server.make_url("/"))
        async with ClientSession() as session:
            with pytest.raises(HomBotRouteError):
                await async_download_cleaning_run(session, base_url, BBL_NAME)
            data = await async_download_learned_map(session, base_url)
    finally:
        await server.close()

    assert data == blk
