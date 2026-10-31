"""Synthetic tests for drawing a HomBot route on a BLK map."""

from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from lg_hombot.bbl import read_bbl_bytes
from lg_hombot.blk import BlkHeader, BlkMap, MapBlock, MapCell
from lg_hombot.map_render import render_map
from lg_hombot.route import route_statistics
from lg_hombot.route_render import (
    COLOR_EVENT_HOME,
    COLOR_EVENT_START,
    COLOR_PATH_CARPET,
    COLOR_PATH_DEFAULT,
    COLOR_PATH_HOMING,
    COLOR_PATH_TRAPPED_HOMING,
    FOOTER_INK,
    _icon_bumps,
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


FLOOR = (168, 137, 87)
BACKGROUND = (128, 128, 128)


def _log(body: str) -> object:
    return read_bbl_bytes(f"synthetic\n{body}".encode("ascii"))


def _map_at(
    positions: list[tuple[int, int]],
    floors: dict[int, dict[int, int]] | None = None,
) -> BlkMap:
    overrides = floors or {}
    blocks = [
        MapBlock(0, 0, y_pos * 100 + x_pos, 0)
        for x_pos, y_pos in positions
    ]
    cells = []
    for block_index in range(len(blocks)):
        for cell_index in range(100):
            value = overrides.get(block_index, {}).get(cell_index, 0)
            cells.append(MapCell(block_index, cell_index, value))
    return BlkMap(
        header=BlkHeader(1, 0, 0, 0, 0, 100, len(blocks), 0, len(blocks), 10, 100),
        blocks=tuple(blocks),
        cells=tuple(cells),
        file_size=0,
        trailer_size=0,
    )


def _center(cell_x: int, cell_y: int, *, min_x: int, min_y: int, height_cells: int) -> tuple[int, int]:
    relative_x = cell_x - min_x * 10
    relative_y = cell_y - min_y * 10
    pixel_x = relative_x * 8 + 4
    pixel_y = (height_cells - relative_y - 1) * 8 + 4
    return pixel_x, pixel_y


def _over(base: tuple[int, int, int], color: tuple[int, int, int, int]) -> tuple[int, int, int]:
    alpha = color[3] / 255
    return tuple(
        int(round(channel * alpha + base_channel * (1 - alpha)))
        for channel, base_channel in zip(color[:3], base)
    )


def test_route_renderer_does_not_import_home_assistant() -> None:
    source = Path(render_route.__globals__["__file__"]).read_text(encoding="utf-8")
    assert "homeassistant" not in source
    assert "aiohttp" not in source
    assert "0.01059453" not in source
    assert "499.7527" not in source
    assert "498.8841" not in source


def test_route_is_drawn_on_a_copy_of_the_map() -> None:
    map_data = _map_at([(50, 50)], {0: {index: 3 for index in range(100)}})
    log = _log(
        "20010101010101.000001,POSI,RobotPose,0,0,1,\n"
        "20010101010101.000002,POSI,RobotPose,0,100,2,\n"
        "20010101010140.100000,STRN,App_SB,Cleaning Exception and Homing\n"
        "20010101010141.300000,POSI,RobotPose,500,0,3,\n"
    )
    plain = render_map(map_data)
    before = plain.tobytes()

    routed = render_route(map_data, log)

    assert plain.tobytes() == before
    assert isinstance(routed, Image.Image)
    assert routed.mode == "RGB"
    assert routed.size[0] == plain.size[0] == 80
    assert routed.size[1] == plain.size[1] + footer_height(80, _labels(map_data, log))

    origin = _center(500, 500, min_x=50, min_y=50, height_cells=10)
    above = _center(500, 501, min_x=50, min_y=50, height_cells=10)
    later = _center(505, 500, min_x=50, min_y=50, height_cells=10)
    untouched = _center(508, 508, min_x=50, min_y=50, height_cells=10)
    cleaning = _over(FLOOR, COLOR_PATH_DEFAULT)

    assert origin == (4, 76)
    assert above == (4, 68)
    assert above[1] < origin[1]
    assert routed.getpixel(origin)[:3] == COLOR_EVENT_START[:3]
    assert routed.getpixel(above) == cleaning
    assert routed.getpixel(later) == cleaning
    assert plain.getpixel(origin) == FLOOR
    assert routed.getpixel(untouched) == plain.getpixel(untouched) == FLOOR


def test_start_marker_and_state_colors_follow_the_frontend() -> None:
    map_data = _map_at([(50, 50)], {0: {index: 3 for index in range(100)}})
    log = _log(
        "20010101010101.000001,POSI,RobotPose,0,0,0,\n"
        "20010101010101.000002,POSI,RobotPose,200,0,0,\n"
        "20010101010101.000003,POSF,EventPose,200,200,0,Carpetcurrent\n"
        "20010101010101.000004,STRN,App_SB,End Cleaning (Docked) - Battery (Middle)\n"
        "20010101010101.000005,POSI,RobotPose,400,0,0,\n"
        "20010101010101.000006,POSI,RobotPose,600,0,0,\n"
    )

    routed = render_route(map_data, log)

    start = _center(500, 500, min_x=50, min_y=50, height_cells=10)
    homing_mid = _center(505, 500, min_x=50, min_y=50, height_cells=10)
    carpet = _center(503, 501, min_x=50, min_y=50, height_cells=10)

    assert routed.getpixel(start)[:3] == COLOR_EVENT_START[:3]
    assert routed.getpixel(homing_mid) == _over(FLOOR, COLOR_PATH_HOMING)
    assert routed.getpixel(carpet) == _over(FLOOR, COLOR_PATH_CARPET)
    assert COLOR_PATH_HOMING[:3] != COLOR_PATH_TRAPPED_HOMING[:3]
    assert COLOR_PATH_HOMING[:3] != COLOR_PATH_DEFAULT[:3]


def test_trapped_homing_is_not_drawn_as_cleaning() -> None:
    map_data = _map_at([(50, 50)], {0: {index: 3 for index in range(100)}})
    log = _log(
        "20010101010101.000001,POSI,RobotPose,0,0,0,\n"
        "20010101010101.000002,POSI,TrapStateStarts,200,0,0,Homing\n"
        "20010101010101.000003,POSI,RobotPose,400,0,0,\n"
    )

    routed = render_route(map_data, log)
    trapped_end = _center(502, 500, min_x=50, min_y=50, height_cells=10)
    homing_mid = _center(503, 500, min_x=50, min_y=50, height_cells=10)

    assert routed.getpixel(trapped_end)[:3] == COLOR_EVENT_HOME[:3]
    assert routed.getpixel(homing_mid) == _over(FLOOR, COLOR_PATH_TRAPPED_HOMING)
    assert routed.getpixel(homing_mid) != _over(FLOOR, COLOR_PATH_DEFAULT)
    assert routed.getpixel(homing_mid) != _over(FLOOR, COLOR_PATH_HOMING)


def test_higher_cell_y_matches_render_map_orientation() -> None:
    map_data = _map_at([(50, 50)], {0: {index: 3 for index in range(100)}})
    log = _log(
        "20010101010101.000001,POSI,RobotPose,0,0,0,\n"
        "20010101010101.000002,POSI,RobotPose,0,100,0,\n"
    )

    default = render_route(map_data, log)
    flipped = render_route(map_data, log, flip_vertical=True)
    cleaning = _over(FLOOR, COLOR_PATH_DEFAULT)

    low = _center(500, 500, min_x=50, min_y=50, height_cells=10)
    high = _center(500, 501, min_x=50, min_y=50, height_cells=10)
    assert default.getpixel(low)[:3] == COLOR_EVENT_START[:3]
    assert default.getpixel(high) == cleaning
    assert high[1] < low[1]

    flipped_low = (low[0], 4)
    flipped_high = (high[0], 12)
    assert flipped.getpixel(flipped_low)[:3] == COLOR_EVENT_START[:3]
    assert flipped.getpixel(flipped_high) == cleaning
    assert flipped_high[1] > flipped_low[1]


def test_first_block_49_49_places_the_origin_on_that_cell() -> None:
    map_data = _map_at([(49, 49), (50, 50)], {0: {0: 3}})
    log = _log("20010101010101.000001,POSI,RobotPose,0,0,0,\n")
    plain = render_map(map_data)
    before = plain.tobytes()

    routed = render_route(map_data, log)

    origin = _center(490, 490, min_x=49, min_y=49, height_cells=20)
    wrong_fixed_offset = _center(500, 500, min_x=49, min_y=49, height_cells=20)

    assert origin == (4, 156)
    assert routed.getpixel(origin)[:3] == COLOR_EVENT_START[:3]
    assert plain.getpixel(origin) == FLOOR
    assert routed.getpixel(wrong_fixed_offset) == plain.getpixel(wrong_fixed_offset) == BACKGROUND
    assert plain.tobytes() == before


def test_one_pos_point_renders_an_image() -> None:
    map_data = _map_at([(50, 50)], {0: {0: 3}})
    plain = render_map(map_data)
    routed = render_route(
        map_data,
        _log("20010101010101.000001,POSI,RobotPose,0,0,0,\n"),
    )

    assert routed.mode == "RGB"
    assert routed.size[0] == plain.size[0]
    assert routed.size[1] == plain.size[1] + footer_height(
        plain.size[0],
        _labels(map_data, _log("20010101010101.000001,POSI,RobotPose,0,0,0,\n")),
    )
    assert routed.tobytes() != plain.tobytes()


def test_route_without_pos_records_does_not_return_a_map() -> None:
    map_data = _map_at([(50, 50)], {0: {0: 3}})
    log = _log("20010101010140.100000,STRN,App_SB,Begin (Docked )\n")
    plain = render_map(map_data)
    before = plain.tobytes()

    with pytest.raises(ValueError, match="no POS records"):
        render_route(map_data, log)

    assert plain.tobytes() == before


def test_footer_bump_icon_is_a_bent_arrow_inside_its_box() -> None:
    canvas = Image.new("RGB", (20, 20), (255, 255, 255))
    _icon_bumps(ImageDraw.Draw(canvas), 0, 0)

    def ink(x_pos: int, y_pos: int) -> bool:
        return canvas.getpixel((x_pos, y_pos)) == FOOTER_INK

    assert ink(4, 11)
    assert ink(6, 11)
    assert ink(11, 4)
    assert ink(11, 6)
    assert not ink(7, 7)
    for y_pos in range(canvas.size[1]):
        for x_pos in range(canvas.size[0]):
            if canvas.getpixel((x_pos, y_pos)) == (255, 255, 255):
                continue
            assert canvas.getpixel((x_pos, y_pos)) == FOOTER_INK
            assert 0 <= x_pos < 14
            assert 0 <= y_pos < 14
