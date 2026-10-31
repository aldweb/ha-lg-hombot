"""Tests for the HomBot Repeat switch."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from urllib.parse import unquote

import pytest
from aiohttp import ClientError, ClientSession, web
from aiohttp.test_utils import TestServer
from homeassistant.components.vacuum import VacuumActivity
from homeassistant.exceptions import HomeAssistantError

from lg_hombot.status import listen_status
from lg_hombot.switch import (
    REPEAT_COMMAND_OFF,
    REPEAT_COMMAND_ON,
    HombotRepeatSwitch,
)
from lg_hombot.vacuum import HombotVacuum

URL = "http://192.168.0.10:6260/"


class _Hass:
    def __init__(self) -> None:
        self.data: dict[str, object] = {}


class _Robot:
    def __init__(self) -> None:
        self.status = "JSON_REPEAT=false\n"
        self.status_code = 200
        self.command_code = 200
        self.requests: list[str] = []

    async def status_handler(self, request: web.Request) -> web.Response:
        self.requests.append(str(request.rel_url))
        if self.status_code != 200:
            return web.Response(status=self.status_code, text="no")
        return web.Response(text=self.status)

    async def command_handler(self, request: web.Request) -> web.Response:
        self.requests.append(str(request.rel_url))
        return web.Response(status=self.command_code, text="ok")


@asynccontextmanager
async def _serve(robot: _Robot) -> AsyncIterator[str]:
    app = web.Application()
    app.router.add_get("/status.txt", robot.status_handler)
    app.router.add_get("/json.cgi", robot.command_handler)
    server = TestServer(app)
    await server.start_server()
    try:
        yield str(server.make_url("/"))
    finally:
        await server.close()


def _patch_session(monkeypatch: pytest.MonkeyPatch, hass: _Hass, session: ClientSession) -> None:
    def _session(given: _Hass) -> ClientSession:
        if given is not hass:
            raise AssertionError("session requested for a different Home Assistant")
        return session

    monkeypatch.setattr("lg_hombot.vacuum.async_get_clientsession", _session)
    monkeypatch.setattr("lg_hombot.switch.async_get_clientsession", _session)


def _commands(robot: _Robot) -> list[str]:
    found = []
    for url in robot.requests:
        if url.startswith("/json.cgi?"):
            found.append(unquote(url.split("?", 1)[1]))
    return found


def _assert_no_repeat_toggle(robot: _Robot) -> None:
    for url in robot.requests:
        assert "json.cgi?repeat" not in url
        query = unquote(url.split("?", 1)[1]) if "?" in url else ""
        assert query != "repeat"


def test_repeat_switch_uses_the_vacuum_device_and_a_stable_id() -> None:
    hass = _Hass()
    switch = HombotRepeatSwitch(hass, "Living room", URL)
    vacuum = HombotVacuum("Living room", URL)

    assert switch._attr_name == "Repeat"
    assert switch._attr_has_entity_name is True
    assert switch._attr_should_poll is False
    assert switch._attr_unique_id == f"hombot_{URL}_repeat"
    assert switch._attr_unique_id != vacuum.unique_id
    assert switch.device_info["identifiers"] == vacuum.device_info["identifiers"]
    assert switch.device_info["identifiers"] == {("lg_hombot", f"hombot_{URL}")}
    assert switch.device_info["name"] == "Living room"
    assert switch.device_info["manufacturer"] == "LG"
    assert switch.available is False
    assert switch.is_on is None
    assert REPEAT_COMMAND_ON == '{"COMMAND":{"REPEAT":"true"}}'
    assert REPEAT_COMMAND_OFF == '{"COMMAND":{"REPEAT":"false"}}'


def test_repeat_status_values() -> None:
    switch = HombotRepeatSwitch(_Hass(), "Living room", URL)

    switch._apply_repeat({"JSON_REPEAT": "true"})
    assert switch.is_on is True
    assert switch.available is True

    switch._apply_repeat({"JSON_REPEAT": "false"})
    assert switch.is_on is False
    assert switch.available is True

    switch._apply_repeat({"JSON_ROBOT_STATE": "CHARGING"})
    assert switch.available is False

    switch._apply_repeat({"JSON_REPEAT": "yes"})
    assert switch.available is False
    switch._apply_repeat({"JSON_REPEAT": "TRUE"})
    assert switch.available is False
    switch._apply_repeat({"JSON_REPEAT": ""})
    assert switch.available is False


@pytest.mark.asyncio
async def test_turn_on_sends_repeat_true_and_reads_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hass = _Hass()
    robot = _Robot()
    robot.status = "JSON_REPEAT=true\n"
    async with _serve(robot) as url:
        async with ClientSession() as session:
            _patch_session(monkeypatch, hass, session)
            switch = HombotRepeatSwitch(hass, "Living room", url)
            await switch.async_turn_on()

    assert _commands(robot) == [REPEAT_COMMAND_ON]
    assert any(item.startswith("/status.txt") for item in robot.requests)
    assert switch.is_on is True
    assert switch.available is True
    _assert_no_repeat_toggle(robot)


@pytest.mark.asyncio
async def test_turn_off_sends_repeat_false_and_reads_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hass = _Hass()
    robot = _Robot()
    robot.status = "JSON_REPEAT=false\n"
    async with _serve(robot) as url:
        async with ClientSession() as session:
            _patch_session(monkeypatch, hass, session)
            switch = HombotRepeatSwitch(hass, "Living room", url)
            switch._attr_is_on = True
            switch._attr_available = True
            await switch.async_turn_off()

    assert _commands(robot) == [REPEAT_COMMAND_OFF]
    assert switch.is_on is False
    assert switch.available is True
    _assert_no_repeat_toggle(robot)


@pytest.mark.asyncio
async def test_http_ok_does_not_force_the_requested_repeat_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hass = _Hass()
    robot = _Robot()
    robot.status = "JSON_REPEAT=false\n"
    async with _serve(robot) as url:
        async with ClientSession() as session:
            _patch_session(monkeypatch, hass, session)
            switch = HombotRepeatSwitch(hass, "Living room", url)
            switch._attr_is_on = False
            switch._attr_available = True
            await switch.async_turn_on()

    assert _commands(robot) == [REPEAT_COMMAND_ON]
    assert switch.is_on is False
    assert switch.available is True


@pytest.mark.asyncio
async def test_failed_command_leaves_the_switch_unchanged(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    hass = _Hass()
    robot = _Robot()
    robot.command_code = 500
    robot.status = "JSON_REPEAT=true\n"
    async with _serve(robot) as url:
        async with ClientSession() as session:
            _patch_session(monkeypatch, hass, session)
            switch = HombotRepeatSwitch(hass, "Living room", url)
            switch._attr_is_on = False
            switch._attr_available = True
            with pytest.raises(HomeAssistantError, match="repeat command failed"):
                await switch.async_turn_on()

    assert switch.is_on is False
    assert switch.available is True
    assert not any(item.startswith("/status.txt") for item in robot.requests)
    assert "HomBot repeat command failed" in caplog.text
    assert url not in caplog.text


@pytest.mark.asyncio
async def test_failed_status_read_does_not_force_a_new_state(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    hass = _Hass()
    robot = _Robot()
    robot.status_code = 500
    async with _serve(robot) as url:
        async with ClientSession() as session:
            _patch_session(monkeypatch, hass, session)
            switch = HombotRepeatSwitch(hass, "Living room", url)
            switch._attr_is_on = False
            switch._attr_available = True
            with pytest.raises(HomeAssistantError, match="repeat status read failed"):
                await switch.async_turn_on()

    assert _commands(robot) == [REPEAT_COMMAND_ON]
    assert switch.is_on is False
    assert switch.available is True
    assert "HomBot repeat status read failed" in caplog.text
    assert url not in caplog.text


@pytest.mark.asyncio
async def test_vacuum_poll_updates_repeat_without_a_switch_poll(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hass = _Hass()
    robot = _Robot()
    robot.status = "JSON_REPEAT=false\nJSON_ROBOT_STATE=CHARGING\n"
    async with _serve(robot) as url:
        async with ClientSession() as session:
            _patch_session(monkeypatch, hass, session)
            switch = HombotRepeatSwitch(hass, "Living room", url)
            vacuum = HombotVacuum("Living room", url)
            vacuum.hass = hass
            await switch.async_added_to_hass()
            assert switch.is_on is False
            assert switch.available is True

            robot.status = 'JSON_REPEAT="true"\nJSON_ROBOT_STATE=WORKING\n'
            await vacuum.async_update()
            assert switch.is_on is True
            assert switch.available is True
            assert vacuum._attr_extra_state_attributes["repeat_mode"] is True

            robot.status = "JSON_ROBOT_STATE=CHARGING\n"
            await vacuum.async_update()
            assert switch.available is False
            assert vacuum._attr_extra_state_attributes["repeat_mode"] is False
            await switch.async_will_remove_from_hass()


@pytest.mark.asyncio
async def test_existing_vacuum_commands_stay_unchanged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hass = _Hass()
    robot = _Robot()
    robot.status = "JSON_REPEAT=true\nJSON_ROBOT_STATE=CHARGING\nJSON_TURBO=false\n"
    async with _serve(robot) as url:
        async with ClientSession() as session:
            _patch_session(monkeypatch, hass, session)
            vacuum = HombotVacuum("Living room", url)
            vacuum.hass = hass
            await vacuum.async_start()
            await vacuum.async_pause()
            await vacuum.async_stop()
            await vacuum.async_return_to_base()
            await vacuum.async_set_fan_speed("Turbo")
            await vacuum.async_set_fan_speed("Normal")
            await vacuum.async_update()

    assert _commands(robot) == [
        '{"COMMAND":"CLEAN_START"}',
        '{"COMMAND":"PAUSE"}',
        '{"COMMAND":"HOMING"}',
        '{"COMMAND":"HOMING"}',
        '{"COMMAND":{"TURBO":"true"}}',
        '{"COMMAND":{"TURBO":"false"}}',
    ]
    assert vacuum._attr_extra_state_attributes["repeat_mode"] is True
    _assert_no_repeat_toggle(robot)


@pytest.mark.asyncio
async def test_repeat_commands_never_use_the_toggle_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hass = _Hass()
    robot = _Robot()
    async with _serve(robot) as url:
        async with ClientSession() as session:
            _patch_session(monkeypatch, hass, session)
            switch = HombotRepeatSwitch(hass, "Living room", url)
            robot.status = "JSON_REPEAT=true\n"
            await switch.async_turn_on()
            robot.status = "JSON_REPEAT=false\n"
            await switch.async_turn_off()

    assert _commands(robot) == [REPEAT_COMMAND_ON, REPEAT_COMMAND_OFF]
    _assert_no_repeat_toggle(robot)


@pytest.mark.asyncio
async def test_a_status_listener_error_does_not_fail_the_vacuum_poll(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hass = _Hass()
    robot = _Robot()
    robot.status = "JSON_REPEAT=true\nJSON_ROBOT_STATE=CHARGING\n"

    def _boom(_attributes: dict[str, str]) -> None:
        raise RuntimeError("listener failed")

    async with _serve(robot) as url:
        async with ClientSession() as session:
            _patch_session(monkeypatch, hass, session)
            vacuum = HombotVacuum("Living room", url)
            vacuum.hass = hass
            listen_status(hass, url, _boom)
            await vacuum.async_update()

    assert vacuum.activity == VacuumActivity.DOCKED
    assert vacuum._attr_extra_state_attributes["repeat_mode"] is True


@pytest.mark.asyncio
async def test_status_publication_failure_does_not_fail_the_vacuum_update(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    hass = _Hass()
    robot = _Robot()
    robot.status = (
        "JSON_REPEAT=true\nJSON_ROBOT_STATE=CHARGING\nJSON_TURBO=false\n"
    )

    def _boom(*_args, **_kwargs) -> None:
        raise RuntimeError("publish failed")

    async with _serve(robot) as url:
        async with ClientSession() as session:
            _patch_session(monkeypatch, hass, session)
            monkeypatch.setattr("lg_hombot.vacuum.publish_status", _boom)
            vacuum = HombotVacuum("Living room", url)
            vacuum.hass = hass
            await vacuum.async_update()

    assert vacuum.activity == VacuumActivity.DOCKED
    assert vacuum.fan_speed == "Normal"
    assert vacuum._attr_extra_state_attributes["repeat_mode"] is True
    assert "HomBot status publication failed" in caplog.text
    assert url not in caplog.text


@pytest.mark.asyncio
async def test_repeat_connection_error_fails_without_changing_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hass = _Hass()

    class _BrokenSession:
        async def get(self, _url: str):
            raise ClientError("connection failed")

    monkeypatch.setattr(
        "lg_hombot.switch.async_get_clientsession",
        lambda given: _BrokenSession() if given is hass else (_ for _ in ()).throw(
            AssertionError("session requested for a different Home Assistant")
        ),
    )
    switch = HombotRepeatSwitch(hass, "Living room", URL)
    switch._attr_is_on = False
    switch._attr_available = True
    with pytest.raises(HomeAssistantError, match="repeat command failed"):
        await switch.async_turn_on()

    assert switch.is_on is False
    assert switch.available is True


@pytest.mark.asyncio
async def test_status_connection_error_after_command_does_not_force_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hass = _Hass()
    robot = _Robot()
    robot.status = "JSON_REPEAT=true\n"

    class _StatusFails:
        def __init__(self, session: ClientSession) -> None:
            self._session = session

        def get(self, url: str):
            if str(url).endswith("status.txt"):
                raise ClientError("status failed")
            return self._session.get(url)

    async with _serve(robot) as url:
        async with ClientSession() as session:
            failing = _StatusFails(session)
            monkeypatch.setattr(
                "lg_hombot.switch.async_get_clientsession",
                lambda given: failing if given is hass else (_ for _ in ()).throw(
                    AssertionError("session requested for a different Home Assistant")
                ),
            )
            switch = HombotRepeatSwitch(hass, "Living room", url)
            switch._attr_is_on = False
            switch._attr_available = True
            with pytest.raises(HomeAssistantError, match="repeat status read failed"):
                await switch.async_turn_on()

    assert _commands(robot) == [REPEAT_COMMAND_ON]
    assert switch.is_on is False
    assert switch.available is True
    _assert_no_repeat_toggle(robot)


@pytest.mark.asyncio
async def test_initial_status_read_failure_leaves_the_switch_unchanged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hass = _Hass()
    robot = _Robot()
    robot.status_code = 500
    async with _serve(robot) as url:
        async with ClientSession() as session:
            _patch_session(monkeypatch, hass, session)
            switch = HombotRepeatSwitch(hass, "Living room", url)
            await switch.async_added_to_hass()

    assert switch.is_on is None
    assert switch.available is False


@pytest.mark.asyncio
async def test_vacuum_http_error_does_not_raise(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hass = _Hass()
    robot = _Robot()
    robot.command_code = 500
    async with _serve(robot) as url:
        async with ClientSession() as session:
            _patch_session(monkeypatch, hass, session)
            vacuum = HombotVacuum("Living room", url)
            vacuum.hass = hass
            await vacuum.async_start()
            await vacuum.async_pause()
            await vacuum.async_stop()
            await vacuum.async_return_to_base()
            await vacuum.async_set_fan_speed("Turbo")
            await vacuum.async_set_fan_speed("Normal")

    assert _commands(robot) == [
        '{"COMMAND":"CLEAN_START"}',
        '{"COMMAND":"PAUSE"}',
        '{"COMMAND":"HOMING"}',
        '{"COMMAND":"HOMING"}',
        '{"COMMAND":{"TURBO":"true"}}',
        '{"COMMAND":{"TURBO":"false"}}',
    ]
    _assert_no_repeat_toggle(robot)
