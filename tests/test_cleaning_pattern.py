"""Tests for the HomBot cleaning-pattern select."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from urllib.parse import unquote

import pytest
from aiohttp import ClientError, ClientSession, web
from aiohttp.test_utils import TestServer
from homeassistant.exceptions import HomeAssistantError

from lg_hombot.cleaning_pattern import (
    PATTERN_CELL,
    PATTERN_COMMAND_CELL,
    PATTERN_COMMAND_ZIGZAG,
    PATTERN_ZIGZAG,
    HombotCleaningPatternSelect,
)
from lg_hombot.const import DOMAIN
from lg_hombot.route_history import ROUTE_HISTORY_KEY, RouteHistoryState
from lg_hombot.select import HombotRouteSelect, async_setup_entry
from lg_hombot.vacuum import HombotVacuum

URL = "http://192.168.0.10:6260/"


class _Hass:
    def __init__(self) -> None:
        self.data: dict[str, object] = {}


class _Entry:
    def __init__(self, url: str) -> None:
        self.entry_id = "entry-1"
        self.data = {"url": url, "name": "Living room"}


class _Robot:
    def __init__(self) -> None:
        self.status = "JSON_MODE=ZZ\n"
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

    monkeypatch.setattr("lg_hombot.entity.async_get_clientsession", _session)
    monkeypatch.setattr("lg_hombot.vacuum.async_get_clientsession", _session)


def _commands(robot: _Robot) -> list[str]:
    found = []
    for url in robot.requests:
        if url.startswith("/json.cgi?"):
            found.append(unquote(url.split("?", 1)[1]))
    return found


def _assert_only_pattern_commands(robot: _Robot) -> None:
    for url in robot.requests:
        assert "json.cgi?mode" not in url
        query = unquote(url.split("?", 1)[1]) if "?" in url else ""
        assert query != "mode"
        assert "CLEAN_SPOT" not in query


def _prepare(hass: _Hass, url: str) -> _Entry:
    hass.data[DOMAIN] = {"entry-1": {ROUTE_HISTORY_KEY: RouteHistoryState()}}
    return _Entry(url)


def test_pattern_select_uses_the_vacuum_device_and_a_stable_id() -> None:
    hass = _Hass()
    select = HombotCleaningPatternSelect(hass, "Living room", URL)
    vacuum = HombotVacuum("Living room", URL)
    route = HombotRouteSelect(
        hass,
        "Living room",
        URL,
        RouteHistoryState(),
    )

    assert select._attr_name == "Cleaning pattern"
    assert select._attr_has_entity_name is True
    assert select._attr_should_poll is False
    assert select.options == [PATTERN_ZIGZAG, PATTERN_CELL]
    assert select.options == ["Zigzag", "Cell by cell"]
    assert len(select.options) == 2
    assert select._attr_unique_id == f"hombot_{URL}_cleaning_pattern"
    assert select._attr_unique_id != vacuum.unique_id
    assert select._attr_unique_id != route._attr_unique_id
    assert select.device_info["identifiers"] == vacuum.device_info["identifiers"]
    assert select.device_info["identifiers"] == {("lg_hombot", f"hombot_{URL}")}
    assert select.device_info["name"] == "Living room"
    assert select.device_info["manufacturer"] == "LG"
    assert select.available is False
    assert select.current_option is None
    assert PATTERN_COMMAND_ZIGZAG == '{"COMMAND":{"CLEAN_MODE":"CLEAN_ZZ"}}'
    assert PATTERN_COMMAND_CELL == '{"COMMAND":{"CLEAN_MODE":"CLEAN_SB"}}'
    assert "CLEAN_SPOT" not in PATTERN_COMMAND_ZIGZAG
    assert "CLEAN_SPOT" not in PATTERN_COMMAND_CELL


def test_mode_values_map_only_zz_and_sb() -> None:
    select = HombotCleaningPatternSelect(_Hass(), "Living room", URL)

    select._apply_status({"JSON_MODE": "ZZ"})
    assert select.current_option == "Zigzag"
    assert select.available is True

    select._apply_status({"JSON_MODE": "SB"})
    assert select.current_option == "Cell by cell"
    assert select.available is True

    select._apply_status({"JSON_ROBOT_STATE": "CHARGING"})
    assert select.current_option is None
    assert select.available is False

    select._apply_status({"JSON_MODE": ""})
    assert select.current_option is None
    assert select.available is False

    select._apply_status({"JSON_MODE": "SPOT"})
    assert select.current_option is None
    assert select.available is False

    select._apply_status({"JSON_MODE": "zz"})
    assert select.current_option is None
    assert select.available is False

    assert select.options == ["Zigzag", "Cell by cell"]


@pytest.mark.asyncio
async def test_quoted_status_mode_is_parsed_before_it_is_applied(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hass = _Hass()
    robot = _Robot()
    robot.status = 'JSON_MODE="ZZ"\n'
    async with _serve(robot) as url:
        async with ClientSession() as session:
            _patch_session(monkeypatch, hass, session)
            select = HombotCleaningPatternSelect(hass, "Living room", url)
            await select.async_added_to_hass()
            assert select.current_option == "Zigzag"

            robot.status = 'JSON_MODE="SB"\n'
            robot.requests.clear()
            await select._async_read_status()

    assert select.current_option == "Cell by cell"
    assert select.available is True
    assert _commands(robot) == []
    _assert_only_pattern_commands(robot)


@pytest.mark.asyncio
async def test_zigzag_sends_clean_zz_and_reads_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hass = _Hass()
    robot = _Robot()
    robot.status = "JSON_MODE=ZZ\n"
    async with _serve(robot) as url:
        async with ClientSession() as session:
            _patch_session(monkeypatch, hass, session)
            select = HombotCleaningPatternSelect(hass, "Living room", url)
            await select.async_select_option("Zigzag")

    assert _commands(robot) == [PATTERN_COMMAND_ZIGZAG]
    assert any(item.startswith("/status.txt") for item in robot.requests)
    assert robot.requests[0].startswith("/json.cgi?")
    assert robot.requests[1].startswith("/status.txt")
    assert select.current_option == "Zigzag"
    assert select.available is True
    _assert_only_pattern_commands(robot)


@pytest.mark.asyncio
async def test_cell_by_cell_sends_clean_sb_and_reads_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hass = _Hass()
    robot = _Robot()
    robot.status = "JSON_MODE=SB\n"
    async with _serve(robot) as url:
        async with ClientSession() as session:
            _patch_session(monkeypatch, hass, session)
            select = HombotCleaningPatternSelect(hass, "Living room", url)
            await select.async_select_option("Cell by cell")

    assert _commands(robot) == [PATTERN_COMMAND_CELL]
    assert robot.requests[0].startswith("/json.cgi?")
    assert robot.requests[1].startswith("/status.txt")
    assert select.current_option == "Cell by cell"
    assert select.available is True
    _assert_only_pattern_commands(robot)


@pytest.mark.asyncio
async def test_http_ok_does_not_confirm_an_unchanged_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hass = _Hass()
    robot = _Robot()
    robot.status = "JSON_MODE=SB\n"
    async with _serve(robot) as url:
        async with ClientSession() as session:
            _patch_session(monkeypatch, hass, session)
            select = HombotCleaningPatternSelect(hass, "Living room", url)
            select._apply_status({"JSON_MODE": "SB"})
            await select.async_select_option("Zigzag")

    assert _commands(robot) == [PATTERN_COMMAND_ZIGZAG]
    assert any(item.startswith("/status.txt") for item in robot.requests)
    assert select.current_option == "Cell by cell"
    assert select.available is True
    _assert_only_pattern_commands(robot)


@pytest.mark.asyncio
async def test_failed_command_leaves_the_selection_unchanged(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    hass = _Hass()
    robot = _Robot()
    robot.command_code = 500
    robot.status = "JSON_MODE=ZZ\n"
    async with _serve(robot) as url:
        async with ClientSession() as session:
            _patch_session(monkeypatch, hass, session)
            select = HombotCleaningPatternSelect(hass, "Living room", url)
            select._apply_status({"JSON_MODE": "SB"})
            with pytest.raises(HomeAssistantError, match="cleaning pattern command failed"):
                await select.async_select_option("Zigzag")

    assert select.current_option == "Cell by cell"
    assert select.available is True
    assert not any(item.startswith("/status.txt") for item in robot.requests)
    assert "HomBot cleaning pattern command failed" in caplog.text
    assert url not in caplog.text
    _assert_only_pattern_commands(robot)


@pytest.mark.asyncio
async def test_command_connection_error_leaves_the_selection_unchanged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hass = _Hass()

    class _BrokenSession:
        async def get(self, _url: str):
            raise ClientError("connection failed")

    monkeypatch.setattr(
        "lg_hombot.entity.async_get_clientsession",
        lambda given: _BrokenSession() if given is hass else (_ for _ in ()).throw(
            AssertionError("session requested for a different Home Assistant")
        ),
    )
    select = HombotCleaningPatternSelect(hass, "Living room", URL)
    select._apply_status({"JSON_MODE": "ZZ"})
    with pytest.raises(HomeAssistantError, match="cleaning pattern command failed"):
        await select.async_select_option("Cell by cell")

    assert select.current_option == "Zigzag"
    assert select.available is True


@pytest.mark.asyncio
async def test_failed_status_read_leaves_the_confirmed_option(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    hass = _Hass()
    robot = _Robot()
    robot.status_code = 500
    async with _serve(robot) as url:
        async with ClientSession() as session:
            _patch_session(monkeypatch, hass, session)
            select = HombotCleaningPatternSelect(hass, "Living room", url)
            select._apply_status({"JSON_MODE": "ZZ"})
            with pytest.raises(
                HomeAssistantError,
                match="cleaning pattern status read failed",
            ):
                await select.async_select_option("Cell by cell")

    assert _commands(robot) == [PATTERN_COMMAND_CELL]
    assert select.current_option == "Zigzag"
    assert select.available is True
    assert "HomBot cleaning pattern status read failed" in caplog.text
    assert url not in caplog.text
    _assert_only_pattern_commands(robot)


@pytest.mark.asyncio
async def test_unknown_option_sends_no_command(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hass = _Hass()
    robot = _Robot()
    async with _serve(robot) as url:
        async with ClientSession() as session:
            _patch_session(monkeypatch, hass, session)
            select = HombotCleaningPatternSelect(hass, "Living room", url)
            select._apply_status({"JSON_MODE": "ZZ"})
            await select.async_select_option("Spot")
            await select.async_select_option("CLEAN_SPOT")

    assert robot.requests == []
    assert select.current_option == "Zigzag"
    assert select.available is True


@pytest.mark.asyncio
async def test_confirmed_option_is_not_sent_again(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hass = _Hass()
    robot = _Robot()
    robot.status = "JSON_MODE=SB\n"
    async with _serve(robot) as url:
        async with ClientSession() as session:
            _patch_session(monkeypatch, hass, session)
            select = HombotCleaningPatternSelect(hass, "Living room", url)
            select._apply_status({"JSON_MODE": "SB"})
            await select.async_select_option("Cell by cell")

    assert robot.requests == []
    assert select.current_option == "Cell by cell"
    assert select.available is True


@pytest.mark.asyncio
async def test_unavailable_select_sends_the_chosen_option(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hass = _Hass()
    robot = _Robot()
    robot.status = "JSON_MODE=ZZ\n"
    async with _serve(robot) as url:
        async with ClientSession() as session:
            _patch_session(monkeypatch, hass, session)
            select = HombotCleaningPatternSelect(hass, "Living room", url)
            assert select.available is False
            assert select.current_option is None
            await select.async_select_option("Zigzag")

    assert _commands(robot) == [PATTERN_COMMAND_ZIGZAG]
    assert select.current_option == "Zigzag"
    assert select.available is True
    _assert_only_pattern_commands(robot)


@pytest.mark.asyncio
async def test_vacuum_poll_updates_the_pattern_without_its_own_poll(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hass = _Hass()
    robot = _Robot()
    robot.status = 'JSON_MODE="ZZ"\nJSON_ROBOT_STATE=CHARGING\n'
    async with _serve(robot) as url:
        async with ClientSession() as session:
            _patch_session(monkeypatch, hass, session)
            select = HombotCleaningPatternSelect(hass, "Living room", url)
            vacuum = HombotVacuum("Living room", url)
            vacuum.hass = hass
            await select.async_added_to_hass()
            assert select.current_option == "Zigzag"
            assert select.available is True
            assert select._attr_should_poll is False

            robot.status = 'JSON_MODE="SB"\nJSON_ROBOT_STATE=WORKING\n'
            await vacuum.async_update()
            assert select.current_option == "Cell by cell"
            assert select.available is True

            await select.async_will_remove_from_hass()
            robot.status = "JSON_MODE=ZZ\nJSON_ROBOT_STATE=CHARGING\n"
            await vacuum.async_update()
            assert select.current_option == "Cell by cell"

    _assert_only_pattern_commands(robot)


@pytest.mark.asyncio
async def test_initial_status_read_failure_does_not_fail_setup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hass = _Hass()
    robot = _Robot()
    robot.status_code = 500
    async with _serve(robot) as url:
        async with ClientSession() as session:
            _patch_session(monkeypatch, hass, session)
            added: list[list] = []

            def add(entities, update_before_add: bool = False) -> None:
                added.append(list(entities))
                assert update_before_add is False

            await async_setup_entry(hass, _prepare(hass, url), add)
            select = added[1][0]
            assert isinstance(select, HombotCleaningPatternSelect)
            await select.async_added_to_hass()

    assert select.current_option is None
    assert select.available is False
    assert select.options == ["Zigzag", "Cell by cell"]


@pytest.mark.asyncio
async def test_setup_registers_the_route_select_before_the_pattern() -> None:
    hass = _Hass()
    url = URL
    added: list[tuple[list, bool]] = []

    def add(entities, update_before_add: bool = False) -> None:
        added.append((list(entities), update_before_add))

    await async_setup_entry(hass, _prepare(hass, url), add)

    assert len(added) == 2
    route_entities, route_update = added[0]
    pattern_entities, pattern_update = added[1]
    assert route_update is False
    assert pattern_update is False
    assert len(route_entities) == 1
    assert len(pattern_entities) == 1
    route = route_entities[0]
    pattern = pattern_entities[0]
    assert isinstance(route, HombotRouteSelect)
    assert isinstance(pattern, HombotCleaningPatternSelect)
    assert route._attr_name == "Cleaning route"
    assert route._attr_unique_id == f"hombot_{url}_route_select"
    assert route._attr_should_poll is False
    assert route.current_option is None
    assert route.options == []
    assert route._state.runs == ()
    assert route._state.selected_bbl_name is None
    assert pattern._attr_name == "Cleaning pattern"
    assert pattern._attr_unique_id == f"hombot_{url}_cleaning_pattern"
