"""Unit tests for HomBot snapshot download and cache behavior."""

from __future__ import annotations

import asyncio
import logging
import re

import pytest
from aiohttp import ClientSession, web
from aiohttp.test_utils import TestServer

from lg_hombot.snapshot import (
    HomBotSnapshotError,
    JpegSnapshotCache,
    _async_read_yuv_body,
    async_download_yuv_frame,
    async_fetch_jpeg_snapshot,
)
from lg_hombot.yuv import FRAME_BYTES, yuv422_planar_to_jpeg

_SNAPSHOT_LOGGER = "lg_hombot.snapshot"
_FETCH_TIMING_RE = re.compile(
    r"HomBot snapshot fetch duration_s=([0-9]+\.[0-9]+) outcome=(ok|error)"
)


def _timing_messages(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [
        record.getMessage()
        for record in caplog.records
        if record.name == _SNAPSHOT_LOGGER
        and "HomBot snapshot fetch duration_s=" in record.getMessage()
    ]


class _FakeContent:
    def __init__(self, data: bytes, *, chunk_size: int | None = None) -> None:
        self._data = data
        self._offset = 0
        self._chunk_size = chunk_size
        self.reads: list[int] = []
        self.bytes_returned = 0

    async def read(self, n: int = -1) -> bytes:
        self.reads.append(n)
        if self._offset >= len(self._data):
            return b""
        if n < 0:
            chunk = self._data[self._offset :]
        else:
            size = n if self._chunk_size is None else min(n, self._chunk_size)
            chunk = self._data[self._offset : self._offset + size]
        self._offset += len(chunk)
        self.bytes_returned += len(chunk)
        return chunk


class _FakeResponse:
    def __init__(
        self,
        data: bytes,
        headers: dict[str, str] | None = None,
        *,
        chunk_size: int | None = None,
    ) -> None:
        self.headers = headers or {}
        self.content = _FakeContent(data, chunk_size=chunk_size)


def _neutral_frame() -> bytes:
    data = bytearray(FRAME_BYTES)
    for i in range(38400):
        data[76800 + i] = 128
        data[115200 + i] = 128
    data[0] = 90
    return bytes(data)


@pytest.fixture
def frame() -> bytes:
    return _neutral_frame()


async def _run_server(handler) -> TestServer:
    app = web.Application()
    app.router.add_get("/images/snapshot.yuv", handler)
    server = TestServer(app)
    await server.start_server()
    return server


@pytest.mark.asyncio
async def test_download_exact_frame(frame: bytes) -> None:
    async def handler(_: web.Request) -> web.Response:
        return web.Response(body=frame, content_type="application/octet-stream")

    server = await _run_server(handler)
    try:
        async with ClientSession() as session:
            data = await async_download_yuv_frame(session, str(server.make_url("/")))
        assert len(data) == FRAME_BYTES
        assert data == frame
    finally:
        await server.close()


@pytest.mark.asyncio
async def test_download_rejects_short_body(frame: bytes) -> None:
    async def handler(_: web.Request) -> web.Response:
        return web.Response(body=frame[:-1], content_type="application/octet-stream")

    server = await _run_server(handler)
    try:
        async with ClientSession() as session:
            with pytest.raises(HomBotSnapshotError, match="expected 153600"):
                await async_download_yuv_frame(session, str(server.make_url("/")))
    finally:
        await server.close()


@pytest.mark.asyncio
async def test_download_rejects_long_body(frame: bytes) -> None:
    async def handler(_: web.Request) -> web.Response:
        return web.Response(body=frame + b"\x00", content_type="application/octet-stream")

    server = await _run_server(handler)
    try:
        async with ClientSession() as session:
            with pytest.raises(HomBotSnapshotError, match="expected 153600"):
                await async_download_yuv_frame(session, str(server.make_url("/")))
    finally:
        await server.close()


@pytest.mark.asyncio
async def test_download_rejects_oversized_body_without_buffering_all(frame: bytes) -> None:
    """Oversized bodies are rejected after reading at most expected_size + 1 bytes."""

    async def handler(request: web.Request) -> web.StreamResponse:
        resp = web.StreamResponse(status=200, headers={"Content-Type": "application/octet-stream"})
        await resp.prepare(request)
        await resp.write(frame)
        # Far more than one extra byte; the client must stop after expected_size + 1.
        await resp.write(b"\xff" * 64_000)
        await resp.write_eof()
        return resp

    server = await _run_server(handler)
    try:
        async with ClientSession() as session:
            with pytest.raises(HomBotSnapshotError, match="expected 153600"):
                await async_download_yuv_frame(session, str(server.make_url("/")))
    finally:
        await server.close()


@pytest.mark.asyncio
async def test_download_accepts_exact_frame_without_content_length(frame: bytes) -> None:
    async def handler(request: web.Request) -> web.StreamResponse:
        resp = web.StreamResponse(status=200, headers={"Content-Type": "application/octet-stream"})
        await resp.prepare(request)
        await resp.write(frame)
        await resp.write_eof()
        return resp

    server = await _run_server(handler)
    try:
        async with ClientSession() as session:
            data = await async_download_yuv_frame(session, str(server.make_url("/")))
        assert data == frame
    finally:
        await server.close()


@pytest.mark.asyncio
async def test_read_body_rejects_content_length_larger_than_frame(frame: bytes) -> None:
    response = _FakeResponse(frame, headers={"Content-Length": str(FRAME_BYTES + 50_000)})
    with pytest.raises(HomBotSnapshotError, match="Content-Length"):
        await _async_read_yuv_body(response, FRAME_BYTES)
    assert response.content.reads == []


@pytest.mark.asyncio
async def test_read_body_accepts_exact_bytes_despite_small_content_length(
    frame: bytes,
) -> None:
    """Acceptance follows the bytes read, not a too-small Content-Length header."""
    response = _FakeResponse(frame, headers={"Content-Length": "1"})
    data = await _async_read_yuv_body(response, FRAME_BYTES)
    assert data == frame
    assert response.content.bytes_returned == FRAME_BYTES
    assert response.content.reads[0] == FRAME_BYTES + 1


@pytest.mark.asyncio
async def test_read_body_limits_read_to_expected_plus_one(frame: bytes) -> None:
    oversized = frame + (b"\xff" * 10_000)
    response = _FakeResponse(oversized, chunk_size=4096)
    with pytest.raises(HomBotSnapshotError, match="expected 153600"):
        await _async_read_yuv_body(response, FRAME_BYTES)
    assert response.content.bytes_returned == FRAME_BYTES + 1
    assert max(response.content.reads) <= FRAME_BYTES + 1


@pytest.mark.asyncio
async def test_download_rejects_http_error() -> None:
    async def handler(_: web.Request) -> web.Response:
        return web.Response(status=503, text="busy")

    server = await _run_server(handler)
    try:
        async with ClientSession() as session:
            with pytest.raises(HomBotSnapshotError, match="HTTP 503"):
                await async_download_yuv_frame(session, str(server.make_url("/")))
    finally:
        await server.close()


@pytest.mark.asyncio
async def test_download_timeout() -> None:
    async def handler(_: web.Request) -> web.Response:
        await asyncio.sleep(1.5)
        return web.Response(body=bytes(FRAME_BYTES))

    server = await _run_server(handler)
    try:
        async with ClientSession() as session:
            with pytest.raises(HomBotSnapshotError, match="timed out"):
                await async_download_yuv_frame(
                    session,
                    str(server.make_url("/")),
                    timeout_seconds=0.2,
                )
    finally:
        await server.close()


@pytest.mark.asyncio
async def test_download_connection_error() -> None:
    from aiohttp import ClientError

    class FakeSession:
        def get(self, *args, **kwargs):
            raise ClientError("refused")

    with pytest.raises(HomBotSnapshotError, match="connection error"):
        await async_download_yuv_frame(FakeSession(), "http://example.invalid:6260/")


@pytest.mark.asyncio
async def test_fetch_jpeg_snapshot(frame: bytes) -> None:
    async def handler(_: web.Request) -> web.Response:
        return web.Response(body=frame)

    async def run_in_executor(func, *args):
        return func(*args)

    server = await _run_server(handler)
    try:
        async with ClientSession() as session:
            jpeg = await async_fetch_jpeg_snapshot(
                session,
                str(server.make_url("/")),
                run_in_executor=run_in_executor,
            )
        assert jpeg[:2] == b"\xff\xd8"
        assert jpeg == yuv422_planar_to_jpeg(frame)
    finally:
        await server.close()


@pytest.mark.asyncio
async def test_cache_returns_same_frame_within_window(
    caplog: pytest.LogCaptureFixture,
) -> None:
    calls = 0

    async def fetch() -> bytes:
        nonlocal calls
        calls += 1
        return b"jpeg-" + str(calls).encode()

    cache = JpegSnapshotCache(fetch, cache_seconds=5.0)
    with caplog.at_level(logging.DEBUG, logger=_SNAPSHOT_LOGGER):
        first = await cache.async_get()
        second = await cache.async_get()
    assert first == b"jpeg-1"
    assert second == b"jpeg-1"
    assert calls == 1
    timing = _timing_messages(caplog)
    assert len(timing) == 1
    assert "outcome=ok" in timing[0]
    assert "jpeg_bytes=6" in timing[0]
    assert _FETCH_TIMING_RE.search(timing[0])


@pytest.mark.asyncio
async def test_cache_miss_logs_fetch_timing_duration(
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def fetch() -> bytes:
        await asyncio.sleep(0.02)
        return b"timed-jpeg"

    cache = JpegSnapshotCache(fetch, cache_seconds=5.0)
    with caplog.at_level(logging.DEBUG, logger=_SNAPSHOT_LOGGER):
        assert await cache.async_get() == b"timed-jpeg"
    timing = _timing_messages(caplog)
    assert len(timing) == 1
    match = _FETCH_TIMING_RE.search(timing[0])
    assert match is not None
    assert match.group(2) == "ok"
    assert float(match.group(1)) >= 0.02
    assert "jpeg_bytes=10" in timing[0]
    assert "http://" not in timing[0]


@pytest.mark.asyncio
async def test_cache_coalesces_concurrent_fetches(
    caplog: pytest.LogCaptureFixture,
) -> None:
    calls = 0
    started = asyncio.Event()
    release = asyncio.Event()

    async def fetch() -> bytes:
        nonlocal calls
        calls += 1
        started.set()
        await release.wait()
        return b"shared-jpeg"

    cache = JpegSnapshotCache(fetch, cache_seconds=5.0)
    with caplog.at_level(logging.DEBUG, logger=_SNAPSHOT_LOGGER):
        task1 = asyncio.create_task(cache.async_get())
        await started.wait()
        task2 = asyncio.create_task(cache.async_get())
        # Give task2 time to block on the lock while the first fetch is in flight.
        await asyncio.sleep(0.05)
        release.set()
        results = await asyncio.gather(task1, task2)
    assert results == [b"shared-jpeg", b"shared-jpeg"]
    assert calls == 1
    assert len(_timing_messages(caplog)) == 1
    assert "outcome=ok" in _timing_messages(caplog)[0]
    assert "jpeg_bytes=11" in _timing_messages(caplog)[0]


@pytest.mark.asyncio
async def test_cache_error_returns_none_and_does_not_serve_stale(
    caplog: pytest.LogCaptureFixture,
) -> None:
    state = {"fail": False}

    async def fetch() -> bytes:
        if state["fail"]:
            raise HomBotSnapshotError(
                "HomBot camera returned HTTP 500 for "
                "http://robot.local/images/snapshot.yuv"
            )
        return b"ok-jpeg"

    cache = JpegSnapshotCache(fetch, cache_seconds=0.05)
    with caplog.at_level(logging.DEBUG, logger=_SNAPSHOT_LOGGER):
        assert await cache.async_get() == b"ok-jpeg"
        await asyncio.sleep(0.06)
        state["fail"] = True
        assert await cache.async_get() is None
    timing = _timing_messages(caplog)
    assert len(timing) == 2
    assert "outcome=ok" in timing[0]
    assert "jpeg_bytes=7" in timing[0]
    assert "outcome=error" in timing[1]
    assert "jpeg_bytes" not in timing[1]
    assert "http://" not in timing[1]
    assert "robot.local" not in timing[1]


@pytest.mark.asyncio
async def test_cache_swallows_unexpected_exceptions(
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def fetch() -> bytes:
        raise RuntimeError("unexpected secret-token-xyz")

    cache = JpegSnapshotCache(fetch, cache_seconds=5.0)
    with caplog.at_level(logging.DEBUG, logger=_SNAPSHOT_LOGGER):
        assert await cache.async_get() is None
    timing = _timing_messages(caplog)
    assert len(timing) == 1
    assert "outcome=error" in timing[0]
    assert "secret-token-xyz" not in timing[0]
    assert "jpeg_bytes" not in timing[0]
