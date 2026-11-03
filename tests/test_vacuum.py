"""Vacuum availability, state after commands and status sharing."""

from __future__ import annotations

from contextlib import asynccontextmanager
from urllib.parse import unquote

import pytest
from aiohttp import ClientSession, web
from aiohttp.test_utils import TestServer
from homeassistant.components.vacuum import VacuumActivity
from homeassistant.exceptions import HomeAssistantError

from lg_hombot.cleaning_pattern import HombotCleaningPatternSelect
from lg_hombot.sensor import HombotBatterySensor
from lg_hombot.status import HomBotStatusError, async_fetch_status
from lg_hombot.switch import HombotRepeatSwitch
from lg_hombot.vacuum import HombotVacuum


class _Hass:
    def __init__(self) -> None:
        self.data: dict[str, object] = {}


class _Robot:
    def __init__(self) -> None:
        self.status_body: bytes = b"JSON_ROBOT_STATE=CHARGING\nJSON_BATTPERC=\"80\"\n"
        self.status_code = 200
        self.commands: list[str] = []

    async def status(self, request: web.Request) -> web.Response:
        return web.Response(status=self.status_code, body=self.status_body)

    async def command(self, request: web.Request) -> web.Response:
        self.commands.append(unquote(str(request.rel_url).split("?", 1)[1]))
        return web.Response(text="ok")


@asynccontextmanager
async def _serve(robot: _Robot):
    app = web.Application()
    app.router.add_get("/status.txt", robot.status)
    app.router.add_get("/json.cgi", robot.command)
    server = TestServer(app)
    await server.start_server()
    try:
        yield str(server.make_url("/"))
    finally:
        await server.close()


def _patch(monkeypatch, hass, session) -> None:
    for module in ("vacuum", "entity"):
        monkeypatch.setattr(
            f"lg_hombot.{module}.async_get_clientsession", lambda _h: session
        )


@pytest.mark.asyncio
async def test_non_ascii_nickname_does_not_fail_the_poll() -> None:
    robot = _Robot()
    robot.status_body = "JSON_NICKNAME=Salon é\nJSON_ROBOT_STATE=WORKING\n".encode("utf-8")
    async with _serve(robot) as url:
        async with ClientSession() as session:
            attributes = await async_fetch_status(session, url)
    assert attributes["JSON_ROBOT_STATE"] == "WORKING"
    assert attributes["JSON_NICKNAME"].startswith("Salon ")


@pytest.mark.asyncio
async def test_http_error_raises_status_error() -> None:
    robot = _Robot()
    robot.status_code = 404
    async with _serve(robot) as url:
        async with ClientSession() as session:
            with pytest.raises(HomBotStatusError):
                await async_fetch_status(session, url)


@pytest.mark.asyncio
async def test_failed_poll_makes_every_entity_unavailable_then_recovers(monkeypatch) -> None:
    hass = _Hass()
    robot = _Robot()
    robot.status_body = b'JSON_ROBOT_STATE=CHARGING\nJSON_BATTPERC="80"\nJSON_REPEAT=true\nJSON_MODE=ZZ\n'
    async with _serve(robot) as url:
        async with ClientSession() as session:
            _patch(monkeypatch, hass, session)
            vacuum = HombotVacuum("Living room", url)
            vacuum.hass = hass
            battery = HombotBatterySensor(hass, "Living room", url)
            repeat = HombotRepeatSwitch(hass, "Living room", url)
            pattern = HombotCleaningPatternSelect(hass, "Living room", url)
            for entity in (battery, repeat, pattern):
                await entity.async_added_to_hass()

            await vacuum.async_update()
            assert vacuum.available is True
            assert vacuum.activity == VacuumActivity.DOCKED
            assert battery.native_value == 80 and battery.available is True
            assert repeat.is_on is True and pattern.available is True

            robot.status_code = 500
            await vacuum.async_update()
            assert vacuum.available is False
            assert battery.available is False
            assert repeat.available is False
            assert pattern.available is False

            robot.status_code = 200
            await vacuum.async_update()
            assert vacuum.available is True
            assert battery.native_value == 80 and battery.available is True
            assert repeat.available is True and pattern.available is True
            for entity in (battery, repeat, pattern):
                await entity.async_will_remove_from_hass()


@pytest.mark.asyncio
async def test_return_to_base_shows_returning_and_writes_state(monkeypatch) -> None:
    hass = _Hass()
    robot = _Robot()
    async with _serve(robot) as url:
        async with ClientSession() as session:
            _patch(monkeypatch, hass, session)
            vacuum = HombotVacuum("Living room", url)
            vacuum.hass = hass
            await vacuum.async_return_to_base()
            assert vacuum.activity == VacuumActivity.RETURNING
            assert vacuum.state_writes == 1
            await vacuum.async_start()
            assert vacuum.activity == VacuumActivity.CLEANING
            await vacuum.async_pause()
            assert vacuum.activity == VacuumActivity.PAUSED
    assert robot.commands == [
        '{"COMMAND":"HOMING"}',
        '{"COMMAND":"CLEAN_START"}',
        '{"COMMAND":"PAUSE"}',
    ]


@pytest.mark.asyncio
async def test_unreachable_robot_fails_the_command_and_keeps_the_state(monkeypatch) -> None:
    hass = _Hass()
    async with ClientSession() as session:
        _patch(monkeypatch, hass, session)
        vacuum = HombotVacuum("Living room", "http://127.0.0.1:1/")
        vacuum.hass = hass
        with pytest.raises(HomeAssistantError):
            await vacuum.async_return_to_base()
        assert vacuum.activity == VacuumActivity.IDLE
