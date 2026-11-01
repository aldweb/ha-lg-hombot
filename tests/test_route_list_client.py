"""Local-server tests for the HomBot cleaning-run list download."""

from __future__ import annotations

import asyncio

import pytest
from aiohttp import ClientSession, web
from aiohttp.test_utils import TestServer

from lg_hombot.route_list_client import (
    MAX_ROUTE_LIST_BYTES,
    REQUEST_TIMEOUT_SECONDS,
    ROUTE_LIST_PATH,
    HomBotRouteListError,
    async_download_route_list,
    route_list_url,
)


LIST_ROUTE = "/" + ROUTE_LIST_PATH
BODY = b'{"blackbox":"/.../usr/data/blackbox/","maps":[],"logs":[]}'


def test_route_list_url_appends_the_status_document() -> None:
    assert route_list_url("http://192.168.0.10:6260/") == (
        "http://192.168.0.10:6260/sites/maps/status.html"
    )
    assert route_list_url("http://192.168.0.10:6260") == (
        "http://192.168.0.10:6260/sites/maps/status.html"
    )
    assert REQUEST_TIMEOUT_SECONDS == 8.0
    assert MAX_ROUTE_LIST_BYTES == 64 * 1024


async def _start(handler) -> TestServer:
    app = web.Application()
    app.router.add_get(LIST_ROUTE, handler)
    server = TestServer(app)
    await server.start_server()
    return server


@pytest.mark.asyncio
async def test_http_200_returns_the_body() -> None:
    async def handler(_: web.Request) -> web.Response:
        return web.Response(body=BODY, content_type="text/html")

    server = await _start(handler)
    try:
        async with ClientSession() as session:
            payload = await async_download_route_list(
                session,
                str(server.make_url("/")),
            )
    finally:
        await server.close()

    assert payload == BODY


@pytest.mark.asyncio
async def test_http_404_hides_the_request_url() -> None:
    async def handler(_: web.Request) -> web.Response:
        return web.Response(status=404, text="missing")

    server = await _start(handler)
    try:
        base_url = str(server.make_url("/"))
        async with ClientSession() as session:
            with pytest.raises(HomBotRouteListError, match="HTTP 404") as caught:
                await async_download_route_list(session, base_url)
    finally:
        await server.close()

    message = str(caught.value)
    assert "http://" not in message
    assert "127.0.0.1" not in message


@pytest.mark.asyncio
async def test_timeout_hides_the_request_url() -> None:
    async def handler(_: web.Request) -> web.Response:
        await asyncio.sleep(1.5)
        return web.Response(body=BODY)

    server = await _start(handler)
    try:
        base_url = str(server.make_url("/"))
        async with ClientSession() as session:
            with pytest.raises(HomBotRouteListError, match="timed out") as caught:
                await async_download_route_list(
                    session,
                    base_url,
                    timeout_seconds=0.2,
                )
    finally:
        await server.close()

    message = str(caught.value)
    assert "http://" not in message
    assert "127.0.0.1" not in message


@pytest.mark.asyncio
async def test_empty_body_is_rejected() -> None:
    async def handler(_: web.Request) -> web.Response:
        return web.Response(body=b"")

    server = await _start(handler)
    try:
        async with ClientSession() as session:
            with pytest.raises(HomBotRouteListError, match="empty") as caught:
                await async_download_route_list(session, str(server.make_url("/")))
    finally:
        await server.close()

    assert "http://" not in str(caught.value)


@pytest.mark.asyncio
async def test_body_larger_than_64_kib_is_rejected() -> None:
    async def handler(request: web.Request) -> web.StreamResponse:
        response = web.StreamResponse(status=200)
        await response.prepare(request)
        await response.write(b"{" + b"x" * MAX_ROUTE_LIST_BYTES)
        await response.write_eof()
        return response

    server = await _start(handler)
    try:
        async with ClientSession() as session:
            with pytest.raises(HomBotRouteListError, match="larger than") as caught:
                await async_download_route_list(session, str(server.make_url("/")))
    finally:
        await server.close()

    assert "65536" in str(caught.value)
    assert "http://" not in str(caught.value)


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
            with pytest.raises(HomBotRouteListError, match="does not match") as caught:
                await async_download_route_list(
                    session,
                    str(server.make_url("/")),
                    timeout_seconds=2,
                )
    finally:
        await server.close()

    assert "http://" not in str(caught.value)
