"""Tests for the HomBot learned-map download."""

from __future__ import annotations

import asyncio
import struct

import pytest
from aiohttp import ClientError, ClientSession, ClientTimeout, web
from aiohttp.test_utils import TestServer

from lg_hombot.blk import read_blk_bytes
from lg_hombot.map_client import (
    MAX_MAP_BYTES,
    HomBotMapError,
    async_download_learned_map,
    map_url,
)

MAP_PATH = "/.../usr/data/MapReuseNavi"


def _synthetic_blk() -> bytes:
    header = (1, 100, 10000, 50, 50000, 100, 1, 1000, 1, 10, 100)
    cells = bytearray(100)
    cells[0] = 3
    data = bytearray(struct.pack("<11i", *header))
    data.extend(struct.pack("<4i", 7, 11, 0, 30))
    data.extend(cells)
    data.extend(b"TR")
    return bytes(data)


def _truncated_blk() -> bytes:
    header = (1, 100, 10000, 50, 50000, 100, 1, 1000, 1, 10, 100)
    data = bytearray(struct.pack("<11i", *header))
    data.extend(struct.pack("<4i", 1, 2, 0, 3))
    return bytes(data)


class _FakeContent:
    def __init__(self, data: bytes) -> None:
        self._data = data
        self._offset = 0
        self.bytes_returned = 0

    async def read(self, n: int = -1) -> bytes:
        if self._offset >= len(self._data):
            return b""
        if n < 0:
            chunk = self._data[self._offset :]
        else:
            chunk = self._data[self._offset : self._offset + n]
        self._offset += len(chunk)
        self.bytes_returned += len(chunk)
        return chunk


class _FakeResponse:
    def __init__(self, data: bytes, headers: dict[str, str] | None = None, status: int = 200):
        self.status = status
        self.headers = headers or {}
        self.content = _FakeContent(data)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False


class _FakeSession:
    def __init__(self, response: _FakeResponse | None = None, error: Exception | None = None):
        self.response = response
        self.error = error
        self.calls: list[tuple[str, object]] = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs.get("timeout")))
        if self.error is not None:
            raise self.error
        return self.response


async def _run_server(handler) -> TestServer:
    app = web.Application()
    app.router.add_get(MAP_PATH, handler)
    server = TestServer(app)
    await server.start_server()
    return server


def test_map_url_appends_path_without_replacing_the_base() -> None:
    rooted = "http://192.168.0.10:6260/"
    bare = "http://192.168.0.10:6260"

    assert map_url(rooted) == "http://192.168.0.10:6260/.../usr/data/MapReuseNavi"
    assert map_url(bare) == "http://192.168.0.10:6260/.../usr/data/MapReuseNavi"
    assert map_url("http://192.168.0.10:6260/hombot/") == (
        "http://192.168.0.10:6260/hombot/.../usr/data/MapReuseNavi"
    )


@pytest.mark.asyncio
async def test_download_uses_exact_path_and_ignores_content_type() -> None:
    payload = _synthetic_blk()
    seen: dict[str, str] = {}

    async def handler(request: web.Request) -> web.Response:
        seen["method"] = request.method
        seen["path"] = request.path
        return web.Response(body=payload, content_type="text/html")

    server = await _run_server(handler)
    try:
        base_url = str(server.make_url("/"))
        async with ClientSession() as session:
            data = await async_download_learned_map(session, base_url)
        assert seen == {"method": "GET", "path": MAP_PATH}
        assert data == payload
        assert read_blk_bytes(data).header.used_blocks == 1
    finally:
        await server.close()


@pytest.mark.asyncio
async def test_download_rejects_http_404() -> None:
    async def handler(_: web.Request) -> web.Response:
        return web.Response(status=404, text="missing")

    server = await _run_server(handler)
    try:
        async with ClientSession() as session:
            with pytest.raises(HomBotMapError, match="HTTP 404"):
                await async_download_learned_map(session, str(server.make_url("/")))
    finally:
        await server.close()


@pytest.mark.asyncio
async def test_download_rejects_other_http_errors() -> None:
    async def handler(_: web.Request) -> web.Response:
        return web.Response(status=503, text="busy")

    server = await _run_server(handler)
    try:
        async with ClientSession() as session:
            with pytest.raises(HomBotMapError, match="HTTP 503"):
                await async_download_learned_map(session, str(server.make_url("/")))
    finally:
        await server.close()


@pytest.mark.asyncio
async def test_download_timeout() -> None:
    async def handler(_: web.Request) -> web.Response:
        await asyncio.sleep(1.5)
        return web.Response(body=_synthetic_blk())

    server = await _run_server(handler)
    try:
        async with ClientSession() as session:
            with pytest.raises(HomBotMapError, match="timed out"):
                await async_download_learned_map(
                    session,
                    str(server.make_url("/")),
                    timeout_seconds=0.2,
                )
    finally:
        await server.close()


@pytest.mark.asyncio
async def test_download_connection_error() -> None:
    session = _FakeSession(error=ClientError("refused"))

    with pytest.raises(HomBotMapError, match="connection error"):
        await async_download_learned_map(session, "http://192.168.0.10:6260/")


@pytest.mark.asyncio
async def test_download_os_error() -> None:
    session = _FakeSession(error=OSError("reset"))

    with pytest.raises(HomBotMapError, match="OS error"):
        await async_download_learned_map(session, "http://192.168.0.10:6260/")


@pytest.mark.asyncio
async def test_download_uses_explicit_timeout() -> None:
    response = _FakeResponse(_synthetic_blk(), headers={"Content-Length": "162"})
    session = _FakeSession(response=response)

    data = await async_download_learned_map(
        session,
        "http://192.168.0.10:6260/",
        timeout_seconds=12,
    )

    assert data == _synthetic_blk()
    url, timeout = session.calls[0]
    assert url == "http://192.168.0.10:6260/.../usr/data/MapReuseNavi"
    assert isinstance(timeout, ClientTimeout)
    assert timeout.total == 12


@pytest.mark.asyncio
async def test_truncated_blk_is_not_a_valid_map() -> None:
    payload = _truncated_blk()

    async def handler(_: web.Request) -> web.Response:
        return web.Response(body=payload)

    server = await _run_server(handler)
    try:
        async with ClientSession() as session:
            data = await async_download_learned_map(session, str(server.make_url("/")))
        with pytest.raises(ValueError, match="truncated"):
            read_blk_bytes(data)
    finally:
        await server.close()


@pytest.mark.asyncio
async def test_body_larger_than_maximum_is_rejected() -> None:
    content = _FakeContent(b"\x00" * (MAX_MAP_BYTES + 10))
    response = _FakeResponse(b"", headers={})
    response.content = content
    session = _FakeSession(response=response)

    with pytest.raises(HomBotMapError, match="larger than"):
        await async_download_learned_map(session, "http://192.168.0.10:6260/")

    assert content.bytes_returned == MAX_MAP_BYTES + 1


@pytest.mark.asyncio
async def test_chunked_body_larger_than_maximum_is_rejected() -> None:
    async def handler(request: web.Request) -> web.StreamResponse:
        response = web.StreamResponse(status=200)
        await response.prepare(request)
        await response.write(b"\x00" * (MAX_MAP_BYTES + 1))
        await response.write_eof()
        return response

    server = await _run_server(handler)
    try:
        async with ClientSession() as session:
            with pytest.raises(HomBotMapError, match="larger than"):
                await async_download_learned_map(session, str(server.make_url("/")))
    finally:
        await server.close()


@pytest.mark.asyncio
async def test_content_length_above_maximum_is_rejected_before_the_body() -> None:
    content = _FakeContent(b"\x00" * 32)
    response = _FakeResponse(
        b"",
        headers={"Content-Length": str(MAX_MAP_BYTES + 50)},
    )
    response.content = content
    session = _FakeSession(response=response)

    with pytest.raises(HomBotMapError, match="Content-Length"):
        await async_download_learned_map(session, "http://192.168.0.10:6260/")

    assert content.bytes_returned == 0


@pytest.mark.asyncio
async def test_body_length_must_match_content_length() -> None:
    response = _FakeResponse(b"abc", headers={"Content-Length": "100"})
    session = _FakeSession(response=response)

    with pytest.raises(HomBotMapError, match="does not match"):
        await async_download_learned_map(session, "http://192.168.0.10:6260/")


@pytest.mark.asyncio
async def test_empty_body_is_rejected() -> None:
    async def handler(_: web.Request) -> web.Response:
        return web.Response(body=b"")

    server = await _run_server(handler)
    try:
        async with ClientSession() as session:
            with pytest.raises(HomBotMapError, match="empty"):
                await async_download_learned_map(session, str(server.make_url("/")))
    finally:
        await server.close()
