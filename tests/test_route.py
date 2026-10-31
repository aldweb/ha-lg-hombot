"""Synthetic tests for HomBot route states and cell coordinates."""

from __future__ import annotations

from collections import Counter
from pathlib import Path

import pytest

from lg_hombot.bbl import read_bbl_bytes
from lg_hombot.blk import BlkHeader, BlkMap, MapBlock, MapCell
from lg_hombot.route import (
    first_block_origin,
    pose_to_cell,
    read_route,
)


def _log(body: str, log_id: str = "synthetic") -> object:
    return read_bbl_bytes(f"{log_id}\n{body}".encode("ascii"))


def _map_at(positions: list[tuple[int, int]]) -> BlkMap:
    blocks = [
        MapBlock(0, 0, y_pos * 100 + x_pos, 0)
        for x_pos, y_pos in positions
    ]
    cells = [
        MapCell(block_index, cell_index, 0)
        for block_index in range(len(blocks))
        for cell_index in range(100)
    ]
    return BlkMap(
        header=BlkHeader(1, 0, 0, 0, 0, 100, len(blocks), 0, len(blocks), 10, 100),
        blocks=tuple(blocks),
        cells=tuple(cells),
        file_size=0,
        trailer_size=0,
    )


def _states(log: object) -> list[str]:
    return [point.state for point in read_route(log).waypoints]


def test_route_module_does_not_import_home_assistant() -> None:
    source = Path(read_route.__globals__["__file__"]).read_text(encoding="utf-8")
    assert "homeassistant" not in source
    assert "aiohttp" not in source
    assert "0.01059453" not in source
    assert "499.7527" not in source
    assert "498.8841" not in source


def test_every_pos_record_is_a_waypoint() -> None:
    log = _log(
        "20010101010101.000001,POSI,RobotPose,0,0,1,\n"
        "20010101010101.000002,EVNT,Bumping,10,20,30\n"
        "20010101010101.000003,POSI,Bumping,40,50,60,gyro\n"
        "20010101010101.000004,POSF,EventPose,100,0,2,Floor\n"
        "20010101010101.000005,POSI,RobotPose,abc,def,ghi\n"
    )

    route = read_route(log)

    assert [point.record.subtype for point in route.waypoints] == [
        "RobotPose",
        "Bumping",
        "EventPose",
    ]
    assert [(point.x, point.y) for point in route.waypoints] == [
        (0.0, 0.0),
        (40.0, 50.0),
        (100.0, 0.0),
    ]


def test_log_without_pos_records_is_invalid() -> None:
    only_events = _log("20010101010101.000001,EVNT,Bumping,1,2,3\n")
    only_text = _log("20010101010101.000001,STRN,App_SB,Begin (Docked )\n")
    non_numeric = _log("20010101010101.000001,POSI,RobotPose,abc,def,ghi\n")

    for log in (only_events, only_text, non_numeric):
        with pytest.raises(ValueError, match="no POS records"):
            read_route(log)


def test_one_pos_record_is_valid() -> None:
    route = read_route(_log("20010101010101.000001,POSI,RobotPose,0,0,1,\n"))

    assert len(route.waypoints) == 1
    assert route.waypoints[0].state == "START"
    assert route.line_segment_count == 0
    assert route.paths[0].state == "START"


def test_input_homing_command_does_not_start_homing() -> None:
    log = _log(
        "20010101010101.000001,STRN,App_HOMING,Input Homing Command\n"
        "20010101010101.000002,POSI,RobotPose,0,0,1,\n"
        "20010101010101.000003,POSI,RobotPose,100,0,2,\n"
    )

    route = read_route(log)

    assert _states(log) == ["CLEAN", "CLEAN"]
    assert [path.state for path in route.paths] == ["CLEAN"]
    assert route.line_segment_count == 1


def test_cleaning_exception_text_does_not_cut_the_route() -> None:
    log = _log(
        "20010101010101.000001,POSI,RobotPose,0,0,1,\n"
        "20010101010101.000002,POSI,RobotPose,100,0,2,\n"
        "20010101010140.100000,STRN,App_SB,Cleaning Exception and Homing\n"
        "20010101010140.200000,STRN,App_SB,go to Homing state\n"
        "20010101010141.300000,POSI,RobotPose,200,0,3,\n",
        log_id="20010101010101_U_101",
    )

    route = read_route(log)

    assert [(point.x, point.y) for point in route.waypoints] == [
        (0.0, 0.0),
        (100.0, 0.0),
        (200.0, 0.0),
    ]
    assert "HOMING" not in [path.state for path in route.paths]
    assert "TRAPPED_HOMING" not in [path.state for path in route.paths]


def test_end_cleaning_changes_the_following_points() -> None:
    log = _log(
        "20010202020202.000001,POSI,RobotPose,0,0,1,\n"
        "20010202020202.000002,POSI,RobotPose,100,-100,2,\n"
        "20010202020220.100000,STRN,App_SB,End Cleaning (Docked) - Battery (Middle)\n"
        "20010202020220.200000,POSI,RobotPose,200,0,3,\n"
        "20010202020222.300000,POSI,RobotPose,300,300,4,\n",
        log_id="20010202020202_U_102",
    )

    route = read_route(log)

    assert [point.state for point in route.waypoints] == [
        "START",
        "CLEAN",
        "END",
        "HOMING",
    ]
    assert [path.state for path in route.paths] == ["START", "CLEAN", "HOMING"]
    assert route.paths[1].waypoints[-1] is route.paths[2].waypoints[0]
    assert route.paths[2].waypoints[-1].x == 300.0
    assert route.line_segment_count == 3


def test_state_change_keeps_the_shared_boundary_point() -> None:
    log = _log(
        "20010101010101.000001,POSI,RobotPose,0,0,1,\n"
        "20010101010101.000002,POSI,RobotPose,100,0,2,\n"
    )

    route = read_route(log)

    assert route.paths[0].state == "START"
    assert route.paths[1].state == "CLEAN"
    assert route.paths[0].waypoints[-1] is route.paths[1].waypoints[0]
    assert route.line_segment_count == 1


def test_unknown_record_does_not_invent_a_state() -> None:
    log = _log(
        "20010101010101.000001,POSI,RobotPose,0,0,1,\n"
        "20010101010101.000002,ZZZZ,NotARealEvent,hello world\n"
        "20010101010101.000003,POSI,RobotPose,100,0,2,\n"
    )

    route = read_route(log)

    assert [point.state for point in route.waypoints] == ["START", "CLEAN"]
    assert Counter(path.state for path in route.paths) == Counter(
        {"START": 1, "CLEAN": 1}
    )


def test_event_pose_uses_the_original_indexof_check() -> None:
    log = _log(
        "20010101010101.000001,POSI,RobotPose,0,0,1,\n"
        "20010101010101.000002,POSF,EventPose,100,0,2,Floor\n"
        "20010101010101.000003,POSF,EventPose,200,0,3,Carpetcurrent\n"
    )

    route = read_route(log)

    assert [point.state for point in route.waypoints] == [
        "START",
        "FLOOR_HARD",
        "FLOOR_CARPET",
    ]
    assert [path.state for path in route.paths] == ["START", "CLEAN", "CARPET"]


def test_trap_homing_is_separate_from_cleaning() -> None:
    log = _log(
        "20010101010101.000001,POSI,RobotPose,0,0,1,\n"
        "20010101010101.000002,POSI,TrapStateStarts,100,0,2,Homing\n"
        "20010101010101.000003,POSI,RobotPose,200,0,3,\n"
    )

    route = read_route(log)

    assert [point.state for point in route.waypoints] == [
        "START",
        "TRAPPED_END",
        "TRAPPED_HOMING",
    ]
    assert route.paths[-1].state == "TRAPPED_HOMING"
    assert "CLEAN" not in [
        path.state
        for path in route.paths
        if any(point.state == "TRAPPED_HOMING" for point in path.waypoints)
        and all(point.state != "START" for point in path.waypoints)
    ]


def test_homing_text_on_a_pose_does_not_end_the_route() -> None:
    log = _log(
        "20010101010101.000001,POSI,RobotPose,0,0,1,Homing\n"
        "20010101010101.000002,POSI,RobotPose,100,0,2,\n"
    )

    route = read_route(log)

    assert len(route.waypoints) == 2
    assert "HOMING" not in [point.state for point in route.waypoints]


def test_pose_origin_follows_the_first_block() -> None:
    assert pose_to_cell(0, 0, 50, 50) == (500, 500)
    assert pose_to_cell(100, -100, 50, 50) == (501, 499)
    assert pose_to_cell(0, 0, 49, 49) == (490, 490)

    shifted = pose_to_cell(100, 200, 49, 49)
    origin = pose_to_cell(0, 0, 49, 49)
    assert shifted[0] - origin[0] == 1
    assert shifted[1] - origin[1] == 2


def test_origin_is_the_first_block_not_a_fixed_offset() -> None:
    map_data = _map_at([(49, 49), (50, 50)])

    assert first_block_origin(map_data) == (49, 49)
    assert pose_to_cell(0, 0, *first_block_origin(map_data)) == (490, 490)
    assert pose_to_cell(0, 0, *first_block_origin(map_data)) != (500, 500)


def test_sentinel_first_block_has_no_origin() -> None:
    map_data = _map_at([(50, 50)])
    sentinel = BlkMap(
        header=map_data.header,
        blocks=(MapBlock(0, 0, -1, 0),) + map_data.blocks[1:],
        cells=map_data.cells,
        file_size=0,
        trailer_size=0,
    )

    with pytest.raises(ValueError, match="no coordinates"):
        first_block_origin(sentinel)
