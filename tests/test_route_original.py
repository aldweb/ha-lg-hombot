"""Synthetic checks for the original HomBot route state machine.

The records below are invented. They do not come from a cleaning log.
"""

from __future__ import annotations

from lg_hombot.bbl import read_bbl_bytes
from lg_hombot.blk import BlkHeader, BlkMap, MapBlock, MapCell
from lg_hombot.map_render import render_map
from lg_hombot.route import read_route, route_statistics
from lg_hombot.route_render import (
    COLOR_PATH_DEFAULT,
    COLOR_PATH_HOMING,
    footer_height,
    render_route,
)


def _labels(map_data: BlkMap, log: object) -> tuple[str, str, str, str]:
    stats = route_statistics(map_data, log)
    return (
        stats.area_text,
        stats.duration_text,
        stats.distance_text,
        stats.bumps_text,
    )


def _log(body: str) -> object:
    return read_bbl_bytes(f"example-route\n{body}".encode("ascii"))


def _map_at(positions: list[tuple[int, int]]) -> BlkMap:
    blocks = [
        MapBlock(0, 0, y_pos * 100 + x_pos, 0)
        for x_pos, y_pos in positions
    ]
    cells = [
        MapCell(block_index, cell_index, 3)
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


def _line(second: int, record: str) -> str:
    return f"199901010101{second:02d}.000001,{record}\n"


def test_mixed_route_keeps_every_pos_point_and_separates_homing() -> None:
    """Every POS type stays on the route, including the trapped-homing tail."""

    log = _log(
        _line(1, "STRN,App_SB,Begin (Docked )")
        + _line(2, "STRN,App_SB,Input Homing Command")
        + _line(3, "POSI,RobotPose,0,0,0,")
        + _line(4, "POSI,RobotPose,40,0,0,")
        + _line(5, "POSI,Bumping,80,0,0,gyro")
        + _line(6, "POSF,EventPose,120,0,0,Floor")
        + _line(7, "POSF,EventPose,160,0,0,Carpetcurrent")
        + _line(8, "POSI,RobotPose,200,0,0,")
        + _line(9, "POSI,TrapStateStarts,240,0,0,stuck")
        + _line(10, "POSI,RobotPose,280,0,0,")
        + _line(11, "POSI,TrapStateEnds,320,0,0,clear")
        + _line(12, "POSI,TrapStateStarts,360,0,0,Homing")
        + _line(13, "POSI,RobotPose,400,0,0,")
        + _line(14, "POSI,RobotPose,440,0,0,")
        + _line(15, "STRN,App_SB,End Cleaning")
    )
    map_data = _map_at([(50, 50)])

    route = read_route(log)
    image = render_route(map_data, log)
    plain = render_map(map_data)

    assert [point.record.record_type for point in route.waypoints] == [
        "POSI",
        "POSI",
        "POSI",
        "POSF",
        "POSF",
        "POSI",
        "POSI",
        "POSI",
        "POSI",
        "POSI",
        "POSI",
        "POSI",
    ]
    assert [point.record.subtype for point in route.waypoints] == [
        "RobotPose",
        "RobotPose",
        "Bumping",
        "EventPose",
        "EventPose",
        "RobotPose",
        "TrapStateStarts",
        "RobotPose",
        "TrapStateEnds",
        "TrapStateStarts",
        "RobotPose",
        "RobotPose",
    ]
    assert [point.state for point in route.waypoints] == [
        "START",
        "CLEAN",
        "CLEAN",
        "FLOOR_HARD",
        "FLOOR_CARPET",
        "CLEAN",
        "TRAPPED",
        "TRAPPED",
        "CLEAN",
        "TRAPPED_END",
        "TRAPPED_HOMING",
        "TRAPPED_HOMING",
    ]
    assert [path.state for path in route.paths] == [
        "START",
        "CLEAN",
        "CLEAN",
        "CARPET",
        "CLEAN",
        "TRAPPED",
        "CLEAN",
        "TRAPPED_HOMING",
    ]
    assert route.line_segment_count == 11
    homing_paths = [path for path in route.paths if path.state == "TRAPPED_HOMING"]
    assert homing_paths
    assert all(path.state != "CLEAN" for path in homing_paths)
    assert image.mode == "RGB"
    assert image.size[0] == plain.size[0]
    assert image.size[1] == plain.size[1] + footer_height(
        plain.size[0], _labels(map_data, log)
    )
    assert image.tobytes() != plain.tobytes()


def test_single_pos_point_renders() -> None:
    """One pose after an input-homing note is a start marker, not a refusal."""

    log = _log(
        _line(1, "STRN,App_SB,Begin (Docked )")
        + _line(2, "STRN,App_SB,Input Homing Command")
        + _line(3, "POSI,RobotPose,0,0,0,")
        + _line(4, "STRN,App_SB,End Cleaning")
    )
    map_data = _map_at([(50, 50)])

    route = read_route(log)
    image = render_route(map_data, log)
    plain = render_map(map_data)

    assert len(route.waypoints) == 1
    assert route.waypoints[0].record.subtype == "RobotPose"
    assert route.waypoints[0].state == "START"
    assert route.line_segment_count == 0
    assert route.paths[0].state == "START"
    assert "HOMING" not in [path.state for path in route.paths]
    assert image.mode == "RGB"
    assert image.size[0] == plain.size[0]
    assert image.size[1] == plain.size[1] + footer_height(
        plain.size[0], _labels(map_data, log)
    )
    assert image.tobytes() != plain.tobytes()


def test_route_draws_six_segments_and_no_homing_color() -> None:
    """Seven poses stay a start segment plus a cleaning segment."""

    poses = "".join(
        _line(index + 3, f"POSI,RobotPose,{index * 40},0,0,")
        for index in range(7)
    )
    log = _log(
        _line(1, "STRN,App_SB,Begin (Docked )")
        + _line(2, "STRN,App_SB,Input Homing Command")
        + poses
        + _line(10, "STRN,App_SB,End Cleaning")
    )
    map_data = _map_at([(50, 50)])

    route = read_route(log)
    image = render_route(map_data, log)

    assert len(route.waypoints) == 7
    assert [point.record.subtype for point in route.waypoints] == ["RobotPose"] * 7
    assert [point.state for point in route.waypoints] == ["START", *["CLEAN"] * 6]
    assert route.line_segment_count == 6
    assert [path.state for path in route.paths] == ["START", "CLEAN"]
    assert route.paths[0].waypoints[-1] is route.paths[1].waypoints[0]
    assert COLOR_PATH_HOMING != COLOR_PATH_DEFAULT
    plain = render_map(map_data)
    assert image.mode == "RGB"
    assert image.size[0] == plain.size[0]
    assert image.size[1] == plain.size[1] + footer_height(
        plain.size[0], _labels(map_data, log)
    )
