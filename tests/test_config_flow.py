"""Config flow: URL normalization, connection check and duplicate protection."""

from __future__ import annotations

from contextlib import asynccontextmanager

import pytest
from aiohttp import ClientSession, web
from aiohttp.test_utils import TestServer

from lg_hombot import config_flow
from lg_hombot.config_flow import LgHombotConfigFlow


@asynccontextmanager
async def _robot(status_code: int = 200):
    seen: list[str] = []

    async def status(request: web.Request) -> web.Response:
        seen.append(str(request.rel_url))
        return web.Response(status=status_code, text="JSON_ROBOT_STATE=CHARGING\n")

    app = web.Application()
    app.router.add_get("/status.txt", status)
    server = TestServer(app)
    await server.start_server()
    try:
        yield server, seen
    finally:
        await server.close()


def _flow(monkeypatch: pytest.MonkeyPatch, session: ClientSession) -> LgHombotConfigFlow:
    flow = LgHombotConfigFlow()
    flow.hass = object()
    monkeypatch.setattr(config_flow, "async_get_clientsession", lambda _hass: session)
    LgHombotConfigFlow.configured_ids = set()
    return flow


@pytest.mark.asyncio
async def test_url_without_final_slash_is_stored_with_it(monkeypatch) -> None:
    async with _robot() as (server, seen):
        async with ClientSession() as session:
            flow = _flow(monkeypatch, session)
            raw = f"http://{server.host}:{server.port}"
            result = await flow.async_step_user({"name": "Hombot", "url": raw})

    assert result["type"] == "create_entry"
    assert result["data"] == {"name": "Hombot", "url": raw + "/"}
    assert seen == ["/status.txt"]


@pytest.mark.asyncio
async def test_invalid_url_shows_an_error_without_a_request(monkeypatch) -> None:
    async with ClientSession() as session:
        flow = _flow(monkeypatch, session)
        result = await flow.async_step_user({"name": "Hombot", "url": "not a url"})
    assert result["type"] == "form"
    assert result["errors"] == {"base": "invalid_url"}


@pytest.mark.asyncio
async def test_http_error_is_cannot_connect(monkeypatch) -> None:
    async with _robot(status_code=404) as (server, _seen):
        async with ClientSession() as session:
            flow = _flow(monkeypatch, session)
            result = await flow.async_step_user(
                {"name": "Hombot", "url": f"http://{server.host}:{server.port}/"}
            )
    assert result["type"] == "form"
    assert result["errors"] == {"base": "cannot_connect"}


@pytest.mark.asyncio
async def test_unreachable_robot_is_cannot_connect(monkeypatch) -> None:
    async with ClientSession() as session:
        flow = _flow(monkeypatch, session)
        result = await flow.async_step_user(
            {"name": "Hombot", "url": "http://127.0.0.1:1/"}
        )
    assert result["errors"] == {"base": "cannot_connect"}


@pytest.mark.asyncio
async def test_same_robot_cannot_be_added_twice(monkeypatch) -> None:
    async with _robot() as (server, _seen):
        async with ClientSession() as session:
            flow = _flow(monkeypatch, session)
            LgHombotConfigFlow.configured_ids = {f"{server.host}:{server.port}"}
            with pytest.raises(Exception, match="already_configured"):
                await flow.async_step_user(
                    {"name": "Again", "url": f"http://{server.host}:{server.port}"}
                )
