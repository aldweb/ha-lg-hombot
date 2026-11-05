"""Run list refresh when the robot comes back to CHARGING."""

from __future__ import annotations

import asyncio

import pytest
from aiohttp import ClientSession, web

from lg_hombot.status import publish_status

from test_route_select import (
    BLACKBOX,
    _bbl,
    _Entry,
    _Hass,
    _pair,
    _serve,
    _status,
    _synthetic_blk,
    async_setup_entry,
)


def _run(day: int, run_id: str) -> tuple[str, str]:
    """(blk, bbl) of a run saved on 2001-01-<day> 10:00."""
    stamp = f"200101{day:02d}100000"
    return (
        f"MAPDATA{stamp}_000001_{run_id}.blk",
        f"cleanlog{stamp}_U_{run_id}.bbl",
    )


def _label(day: int) -> str:
    return f"{day:02d}.01.2001 - 10:00"


class _Robot:
    """A robot whose run list can change between two refreshes."""

    def __init__(self, *runs: tuple[str, str]) -> None:
        self.runs: list[tuple[str, str]] = list(runs)
        self.seen: list[str] = []
        self.list_status = 200
        self.list_delay = 0.0
        self.empty_style = "empty"  # how a robot with no run answers
        self.gates: dict[str, asyncio.Event] = {}  # a file waits until its gate opens

    def downloads(self) -> list[str]:
        return [p for p in self.seen if p.startswith(BLACKBOX)]

    def list_requests(self) -> int:
        return sum(1 for p in self.seen if p.endswith("status.html"))

    async def handler(self, request: web.Request) -> web.Response:
        self.seen.append(request.path)
        if request.path.endswith("status.html"):
            if self.list_delay:
                await asyncio.sleep(self.list_delay)
            if self.list_status != 200:
                return web.Response(status=self.list_status, text="boom")
            if self.runs or self.empty_style == "empty":
                return web.Response(body=_status(*self.runs))
            # Padded with blank slots, like the maps page of older firmware.
            return web.Response(body=_status(("", ""), ("", "")))
        for name, gate in self.gates.items():
            if request.path.endswith(name):
                await gate.wait()
        for blk, bbl in self.runs:
            if request.path.endswith(bbl):
                return web.Response(body=_bbl(f"/usr/data/blackbox/{blk}"))
            if request.path.endswith(blk):
                return web.Response(body=_synthetic_blk())
        return web.Response(status=404, text="missing")


class _Rig:
    def __init__(self, robot, hass, select, route, session, url) -> None:
        self.robot, self.hass, self.select, self.route = robot, hass, select, route
        self.session, self.url = session, url

    async def settle(self) -> None:
        """Wait for every background task started so far."""
        while True:
            pending = [t for t in self.hass.background_tasks if not t.done()]
            if not pending:
                return
            await asyncio.gather(*pending, return_exceptions=True)

    def status(self, state: str | None) -> None:
        publish_status(self.hass, self.url, {} if state is None else {"JSON_ROBOT_STATE": state})

    async def charge_cycle(self) -> None:
        """The robot cleans, comes back to its dock, and the tasks finish."""
        self.status("WORKING")
        self.status("CHARGING")
        await self.settle()


@pytest.fixture
async def rig(monkeypatch: pytest.MonkeyPatch):
    async def build(*runs: tuple[str, str]) -> _Rig:
        robot = _Robot(*runs)
        server = await _serve(robot.handler)
        hass = _Hass()
        hass.run_background = True
        url = str(server.make_url("/"))
        await async_setup_entry(hass, _Entry(url))
        select, route, _learned = _pair(hass, url)
        session = ClientSession()
        monkeypatch.setattr("lg_hombot.select.async_get_clientsession", lambda _h: session)
        # The 5 s delay has its own tests below. The others do not wait for it.
        monkeypatch.setattr("lg_hombot.select.REFRESH_DELAY_SECONDS", 0)
        built = _Rig(robot, hass, select, route, session, url)
        built.server = server
        await select.async_added_to_hass()
        await route.async_added_to_hass()
        await built.settle()
        rigs.append(built)
        return built

    rigs: list[_Rig] = []
    yield build
    for built in rigs:
        await built.select.async_will_remove_from_hass()
        await built.session.close()
        await built.server.close()


@pytest.mark.asyncio
async def test_new_cleaning_is_selected_when_the_robot_is_back_on_its_dock(rig) -> None:
    r = await rig(_run(1, "1"), _run(2, "2"))
    assert r.select.current_option == _label(2)
    assert await r.route.async_image() is not None

    r.robot.runs.append(_run(3, "3"))
    r.robot.seen.clear()
    await r.charge_cycle()

    assert r.select.options == [_label(1), _label(2), _label(3)]
    assert r.select.current_option == _label(3)
    assert r.select._state.selected_bbl_name == _run(3, "3")[1]
    assert await r.route.async_image() is not None
    assert r.robot.list_requests() == 1
    assert [p.rsplit("/", 1)[1] for p in r.robot.downloads()] == [_run(3, "3")[1], _run(3, "3")[0]]


@pytest.mark.asyncio
async def test_nothing_new_means_no_download_and_the_same_image(rig) -> None:
    r = await rig(_run(1, "1"), _run(2, "2"))
    before = await r.route.async_image()
    r.robot.seen.clear()
    await r.charge_cycle()

    assert r.robot.list_requests() == 1
    assert r.robot.downloads() == []
    assert r.select.current_option == _label(2)
    assert await r.route.async_image() == before


@pytest.mark.asyncio
async def test_an_older_run_chosen_by_the_user_is_kept(rig) -> None:
    r = await rig(_run(1, "1"), _run(2, "2"))
    await r.select.async_select_option(_label(1))
    image = await r.route.async_image()

    r.robot.runs.append(_run(3, "3"))
    r.robot.seen.clear()
    await r.charge_cycle()

    assert r.select.options == [_label(1), _label(2), _label(3)]  # the new run is listed
    assert r.select.current_option == _label(1)
    assert r.select._state.selected_bbl_name == _run(1, "1")[1]
    assert r.robot.downloads() == []
    assert await r.route.async_image() == image


@pytest.mark.asyncio
async def test_choosing_the_latest_by_hand_still_follows_the_latest(rig) -> None:
    r = await rig(_run(1, "1"), _run(2, "2"))
    await r.select.async_select_option(_label(1))
    await r.select.async_select_option(_label(2))  # the latest, chosen by hand

    r.robot.runs.append(_run(3, "3"))
    await r.charge_cycle()
    assert r.select.current_option == _label(3)


@pytest.mark.asyncio
async def test_a_failed_latest_download_is_retried_by_the_refresh(rig) -> None:
    r = await rig(_run(1, "1"))
    r.select._state.selected_bbl_name = None  # as after a failed download
    r.select._state.clear_png()
    assert await r.route.async_image() is None

    await r.charge_cycle()
    assert r.select.current_option == _label(1)
    assert await r.route.async_image() is not None


@pytest.mark.asyncio
async def test_a_failed_refresh_changes_nothing(rig) -> None:
    r = await rig(_run(1, "1"), _run(2, "2"))
    image = await r.route.async_image()
    r.robot.list_status = 500
    await r.charge_cycle()

    assert r.select.options == [_label(1), _label(2)]
    assert r.select.current_option == _label(2)
    assert await r.route.async_image() == image


# ---- when the robot has no run at all -----------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("style", ["empty", "blank"])
async def test_erased_runs_empty_the_options_and_the_route_image(rig, style: str) -> None:
    r = await rig(_run(1, "1"), _run(2, "2"))
    assert await r.route.async_image() is not None

    r.robot.runs.clear()
    r.robot.empty_style = style
    await r.charge_cycle()

    assert r.select.options == []
    assert r.select.current_option is None
    assert r.select._state.selected_bbl_name is None
    assert r.select._state.runs == ()
    assert await r.route.async_image() is None
    assert r.select._state.rendered_at is None


@pytest.mark.asyncio
async def test_a_run_appearing_after_an_empty_list_is_selected(rig) -> None:
    r = await rig()  # the robot has no run at the start
    assert r.select.options == [] and r.select.current_option is None
    assert r.hass.background_names == []  # nothing to select, nothing scheduled

    r.robot.runs.append(_run(1, "1"))
    await r.charge_cycle()
    assert r.select.options == [_label(1)]
    assert r.select.current_option == _label(1)
    assert await r.route.async_image() is not None


@pytest.mark.asyncio
async def test_a_run_appears_again_after_all_were_erased(rig) -> None:
    r = await rig(_run(1, "1"))
    r.robot.runs.clear()
    await r.charge_cycle()
    assert r.select.options == []

    r.robot.runs.append(_run(5, "5"))
    await r.charge_cycle()
    assert r.select.current_option == _label(5)
    assert await r.route.async_image() is not None


@pytest.mark.asyncio
async def test_a_chosen_run_erased_from_the_robot_moves_to_the_latest(rig) -> None:
    r = await rig(_run(1, "1"), _run(2, "2"))
    await r.select.async_select_option(_label(1))

    r.robot.runs[:] = [_run(2, "2")]  # the run the user had chosen is gone
    await r.charge_cycle()
    assert r.select.options == [_label(2)]
    assert r.select.current_option == _label(2)
    assert r.select._state.selected_bbl_name == _run(2, "2")[1]
    assert await r.route.async_image() is not None


@pytest.mark.asyncio
async def test_the_latest_erased_selects_the_new_latest(rig) -> None:
    r = await rig(_run(1, "1"), _run(2, "2"))
    assert r.select.current_option == _label(2)

    r.robot.runs[:] = [_run(1, "1")]
    await r.charge_cycle()
    assert r.select.current_option == _label(1)
    assert r.select._state.selected_bbl_name == _run(1, "1")[1]


@pytest.mark.asyncio
async def test_a_download_in_flight_cannot_publish_for_an_erased_run(rig) -> None:
    r = await rig(_run(1, "1"))
    blk2, bbl2 = _run(2, "2")
    gate = asyncio.Event()
    r.robot.gates[bbl2] = gate          # the new run's log is slow to download
    r.robot.runs.append(_run(2, "2"))

    # A refresh selects the new run, and its download waits at the gate.
    r.status("WORKING")
    r.status("CHARGING")
    for _ in range(50):
        if r.select.current_option == _label(2):
            break
        await asyncio.sleep(0.01)
    assert r.select.current_option == _label(2)
    assert await r.route.async_image() is None

    # Meanwhile the robot lost every run, and the next refresh drops the selection.
    await r.select._async_apply_refreshed_runs(())
    assert r.select.current_option is None

    gate.set()
    await r.settle()
    assert r.select.current_option is None
    assert r.select._state.selected_bbl_name is None
    assert await r.route.async_image() is None
    assert r.select._state.rendered_at is None


# ---- when a refresh starts ----------------------------------------------


@pytest.mark.asyncio
async def test_only_a_return_to_charging_starts_a_refresh(rig) -> None:
    r = await rig(_run(1, "1"))
    r.robot.seen.clear()

    r.status("CHARGING")          # first status heard: only remembered
    r.status("CHARGING")          # still on the dock
    r.status("WORKING")
    r.status("PAUSE")
    r.status("HOMING")
    await r.settle()
    assert r.robot.list_requests() == 0

    r.status("CHARGING")
    await r.settle()
    assert r.robot.list_requests() == 1


@pytest.mark.asyncio
async def test_coming_back_in_reach_on_the_dock_starts_a_refresh(rig) -> None:
    r = await rig(_run(1, "1"))
    r.status("CHARGING")
    r.status(None)                # the robot cannot be reached
    r.robot.runs.append(_run(2, "2"))
    r.robot.seen.clear()
    r.status("CHARGING")
    await r.settle()
    assert r.select.current_option == _label(2)


@pytest.mark.asyncio
async def test_a_robot_unreachable_at_start_gets_its_list_when_it_comes_back(rig) -> None:
    r = await rig()               # list unreadable at the start
    r.robot.list_status = 500
    await r.select.async_will_remove_from_hass()
    r.select = type(r.select)(r.hass, "Living room", r.url, r.select._state)
    await r.select.async_added_to_hass()
    assert r.select.options == []

    r.status(None)                # the vacuum could not reach it either
    r.robot.list_status = 200
    r.robot.runs.append(_run(1, "1"))
    r.status("CHARGING")
    await r.settle()
    assert r.select.options == [_label(1)]
    assert r.select.current_option == _label(1)


@pytest.mark.asyncio
async def test_two_returns_in_a_row_share_one_refresh(rig) -> None:
    r = await rig(_run(1, "1"))
    r.status("WORKING")
    r.robot.list_delay = 0.2
    r.robot.seen.clear()
    r.status("CHARGING")
    r.status("WORKING")
    r.status("CHARGING")          # while the first refresh is still reading
    await r.settle()
    assert r.robot.list_requests() == 1


@pytest.mark.asyncio
async def test_removing_the_entity_stops_the_refresh(rig) -> None:
    r = await rig(_run(1, "1"))
    r.robot.list_delay = 5
    r.status("WORKING")
    r.status("CHARGING")
    await asyncio.sleep(0.05)
    await r.select.async_will_remove_from_hass()
    await r.settle()
    refresh = r.hass.background_tasks[-1]
    assert refresh.cancelled()

    r.robot.seen.clear()
    r.status("WORKING")
    r.status("CHARGING")          # nobody listens any more
    await r.settle()
    assert r.robot.list_requests() == 0


# ---- the delay after CHARGING ---------------------------------------------


def test_the_delay_is_five_seconds() -> None:
    from lg_hombot import select

    assert select.REFRESH_DELAY_SECONDS == 5


@pytest.mark.asyncio
async def test_the_list_is_read_only_after_the_delay(rig, monkeypatch) -> None:
    r = await rig(_run(1, "1"))
    monkeypatch.setattr("lg_hombot.select.REFRESH_DELAY_SECONDS", 0.3)
    r.robot.runs.append(_run(2, "2"))   # written by the robot a moment after CHARGING
    r.robot.seen.clear()

    r.status("WORKING")
    r.status("CHARGING")
    await asyncio.sleep(0.1)
    assert r.robot.list_requests() == 0           # nothing is read at once
    assert r.select.current_option == _label(1)

    await r.settle()
    assert r.robot.list_requests() == 1
    assert r.select.current_option == _label(2)


@pytest.mark.asyncio
async def test_a_return_during_the_delay_does_not_start_a_second_read(rig, monkeypatch) -> None:
    r = await rig(_run(1, "1"))
    monkeypatch.setattr("lg_hombot.select.REFRESH_DELAY_SECONDS", 0.3)
    r.robot.seen.clear()

    r.status("WORKING")
    r.status("CHARGING")
    await asyncio.sleep(0.1)
    r.status("WORKING")
    r.status("CHARGING")                          # still waiting for the first one
    await r.settle()
    assert r.robot.list_requests() == 1


@pytest.mark.asyncio
async def test_removing_the_entity_during_the_delay_cancels_the_read(rig, monkeypatch) -> None:
    r = await rig(_run(1, "1"))
    monkeypatch.setattr("lg_hombot.select.REFRESH_DELAY_SECONDS", 30)
    r.robot.seen.clear()

    r.status("WORKING")
    r.status("CHARGING")
    await asyncio.sleep(0.05)
    await r.select.async_will_remove_from_hass()
    await r.settle()
    assert r.hass.background_tasks[-1].cancelled()
    assert r.robot.list_requests() == 0
