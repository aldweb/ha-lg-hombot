"""Synthetic tests for the HomBot BLK map renderer."""

from __future__ import annotations

from pathlib import Path

import struct

import pytest
from PIL import Image, ImageChops

from lg_hombot.blk import (
    BlkHeader,
    BlkMap,
    MapBlock,
    MapCell,
    count_floor_cells,
    read_blk_bytes,
)
from lg_hombot.map_render import render_map
from lg_hombot.route import format_area
from lg_hombot.route_render import FOOTER_CARD, FOOTER_INK, render_learned_map


BACKGROUND = (128, 128, 128)
FLOOR = (168, 137, 87)
WALL = (95, 95, 95)
CARPET = (255, 255, 255)

SNEAK = (190, 155, 98, 204)
SNEAK_WALL = (190, 155, 98, 51)
SCREW = (133, 108, 68, 204)
FIGHT = (244, 67, 54, 204)
BUMP_ABYSS = (118, 94, 107, 204)
ABYSS = (0, 0, 223, 204)
BUMP = (202, 0, 100, 204)
MOVE = (225, 142, 14, 204)
BORDER_FIRST = (102, 0, 0, 128)
BORDER_OTHER = (102, 102, 102, 128)


def _header(used_blocks: int, block_max: int | None = None) -> BlkHeader:
    return BlkHeader(
        map_count=1,
        ceil_min=100,
        ceil_max=10000,
        climb_max=50,
        unknown=50000,
        cell_bytes=100,
        block_max=used_blocks if block_max is None else block_max,
        flag_max=1000,
        used_blocks=used_blocks,
        cell_dim=10,
        cell_count=100,
    )


def _cell_byte(
    floor: int = 0,
    *,
    collision: bool = False,
    infrared: bool = False,
    abyss: bool = False,
    ultrasonic: bool = False,
    sneaking: bool = False,
    screwing: bool = False,
) -> int:
    value = floor
    if collision:
        value |= 0x80
    if infrared:
        value |= 0x40
    if abyss:
        value |= 0x20
    if ultrasonic:
        value |= 0x10
    if sneaking:
        value |= 0x08
    if screwing:
        value |= 0x04
    return value


def _map_at(
    positions: list[tuple[int, int]],
    cells_by_block: dict[int, dict[int, int]] | None = None,
    *,
    block_max: int | None = None,
) -> BlkMap:
    """Build a BlkMap. Missing cell indexes stay inaccessible."""

    overrides = cells_by_block or {}
    blocks: list[MapBlock] = []
    cells: list[MapCell] = []

    for block_index, (x_pos, y_pos) in enumerate(positions):
        blocks.append(MapBlock(0, 0, y_pos * 100 + x_pos, 0))
        for cell_index in range(100):
            value = overrides.get(block_index, {}).get(cell_index, 0)
            cells.append(MapCell(block_index, cell_index, value))

    return BlkMap(
        header=_header(len(blocks), block_max),
        blocks=tuple(blocks),
        cells=tuple(cells),
        file_size=0,
        trailer_size=0,
    )


def _center(cell_index: int, *, origin_x: int = 0, min_x: int = 0) -> tuple[int, int]:
    """Center of a cell in a single-row block span, default Y orientation."""

    local_x = cell_index % 10
    local_y = cell_index // 10
    pixel_x = (origin_x - min_x) * 10 + local_x
    pixel_y = 9 - local_y
    return pixel_x * 8 + 4, pixel_y * 8 + 4


def _blend(
    base_color: tuple[int, int, int],
    overlay_color: tuple[int, int, int, int],
) -> tuple[int, int, int]:
    base = Image.new("RGBA", (1, 1), (*base_color, 255))
    overlay = Image.new("RGBA", (1, 1), overlay_color)
    pixel = Image.alpha_composite(base, overlay).getpixel((0, 0))
    return pixel[0], pixel[1], pixel[2]


def test_renderer_does_not_import_home_assistant() -> None:
    source = Path(render_map.__globals__["__file__"]).read_text(encoding="utf-8")
    assert "homeassistant" not in source
    assert "aiohttp" not in source


def test_canvas_size_follows_used_blocks() -> None:
    single = render_map(_map_at([(50, 49)], block_max=1000))
    span = render_map(_map_at([(1, 4), (3, 6)]))

    assert isinstance(single, Image.Image)
    assert single.mode == "RGB"
    assert single.size == (80, 80)
    assert span.mode == "RGB"
    assert span.size == (240, 240)


def test_floor_colors_and_inaccessible_background() -> None:
    image = render_map(_map_at([(0, 0)], {
        0: {
            0: _cell_byte(0),
            1: _cell_byte(1),
            2: _cell_byte(2),
            3: _cell_byte(3),
        },
    }))

    assert image.getpixel(_center(0)) == BACKGROUND
    assert image.getpixel(_center(1)) == WALL
    assert image.getpixel(_center(2)) == CARPET
    assert image.getpixel(_center(3)) == FLOOR


def test_sensor_layers_use_researched_alpha() -> None:
    image = render_map(_map_at([(0, 0)], {
        0: {
            0: _cell_byte(3, sneaking=True),
            1: _cell_byte(1, sneaking=True),
            2: _cell_byte(3, screwing=True),
            3: _cell_byte(3, abyss=True),
            4: _cell_byte(3, collision=True),
            5: _cell_byte(0, sneaking=True),
        },
    }))

    assert image.getpixel(_center(0)) == _blend(FLOOR, SNEAK)
    assert image.getpixel(_center(1)) == _blend(WALL, SNEAK_WALL)
    assert image.getpixel(_center(1)) != _blend(WALL, SNEAK)
    assert image.getpixel(_center(2)) == _blend(FLOOR, SCREW)
    assert image.getpixel(_center(3)) == _blend(FLOOR, ABYSS)
    assert image.getpixel(_center(4)) == _blend(FLOOR, BUMP)
    assert image.getpixel(_center(5)) == _blend(BACKGROUND, SNEAK)


def test_fight_and_bump_abyss_are_exclusive() -> None:
    image = render_map(_map_at([(0, 0)], {
        0: {
            0: _cell_byte(3, screwing=True, abyss=True, collision=True),
            1: _cell_byte(3, abyss=True, collision=True),
        },
    }))

    fight = _blend(_blend(FLOOR, SCREW), FIGHT)
    separate = _blend(_blend(FLOOR, ABYSS), BUMP)

    assert image.getpixel(_center(0)) == fight
    assert image.getpixel(_center(0)) != _blend(FLOOR, FIGHT)
    assert image.getpixel(_center(0)) != _blend(fight, BUMP)
    assert image.getpixel(_center(1)) == _blend(FLOOR, BUMP_ABYSS)
    assert image.getpixel(_center(1)) != separate


def test_move_is_the_last_overlay() -> None:
    image = render_map(_map_at([(0, 0)], {
        0: {
            0: _cell_byte(3, sneaking=True, screwing=True),
            1: _cell_byte(
                3,
                sneaking=True,
                screwing=True,
                abyss=True,
                collision=True,
            ),
        },
    }))

    move_only = _blend(_blend(_blend(FLOOR, SNEAK), SCREW), MOVE)
    under_move = _blend(_blend(_blend(FLOOR, SNEAK), SCREW), FIGHT)
    with_move = _blend(under_move, MOVE)

    assert image.getpixel(_center(0)) == move_only
    assert image.getpixel(_center(0)) != _blend(_blend(FLOOR, SNEAK), SCREW)
    assert image.getpixel(_center(1)) == with_move
    assert image.getpixel(_center(1)) != under_move


def test_infrared_and_ultrasonic_do_not_change_pixels() -> None:
    plain = _map_at([(0, 0)], {
        0: {
            0: _cell_byte(3),
            1: _cell_byte(3, sneaking=True, screwing=True, abyss=True),
        },
    })
    flagged = _map_at([(0, 0)], {
        0: {
            0: _cell_byte(3, infrared=True, ultrasonic=True),
            1: _cell_byte(
                3,
                sneaking=True,
                screwing=True,
                abyss=True,
                infrared=True,
                ultrasonic=True,
            ),
        },
    })

    difference = ImageChops.difference(render_map(plain), render_map(flagged))

    assert difference.getbbox() is None


def test_first_block_border_differs_from_later_blocks() -> None:
    image = render_map(_map_at([(4, 8), (5, 8)]))

    assert image.size == (160, 80)
    assert image.getpixel((0, 0)) == _blend(BACKGROUND, BORDER_FIRST)
    assert image.getpixel((80, 0)) == _blend(BACKGROUND, BORDER_OTHER)
    assert image.getpixel((4, 4)) == BACKGROUND
    assert image.getpixel((84, 4)) == BACKGROUND


def test_default_orientation_inverts_y_only() -> None:
    image = render_map(_map_at([(0, 0)], {
        0: {
            0: _cell_byte(3),
            9: _cell_byte(1),
            90: _cell_byte(2),
        },
    }))

    assert image.getpixel((4, 76)) == FLOOR
    assert image.getpixel((76, 76)) == WALL
    assert image.getpixel((4, 4)) == CARPET


def _asymmetric_map() -> BlkMap:
    return _map_at([(0, 0)], {
        0: {
            0: _cell_byte(3),
            9: _cell_byte(1),
            90: _cell_byte(2),
        },
    })


def test_horizontal_flip_mirrors_the_image() -> None:
    default = render_map(_asymmetric_map())
    flipped = render_map(_asymmetric_map(), flip_horizontal=True)
    expected = default.transpose(Image.Transpose.FLIP_LEFT_RIGHT)

    assert flipped.size == default.size
    assert flipped.getpixel((76, 76)) == FLOOR
    assert ImageChops.difference(flipped, expected).getbbox() is None


def test_vertical_flip_mirrors_the_image() -> None:
    default = render_map(_asymmetric_map())
    flipped = render_map(_asymmetric_map(), flip_vertical=True)
    expected = default.transpose(Image.Transpose.FLIP_TOP_BOTTOM)

    assert flipped.size == default.size
    assert flipped.getpixel((4, 4)) == FLOOR
    assert ImageChops.difference(flipped, expected).getbbox() is None


def test_both_flips_rotate_the_image_180_degrees() -> None:
    default = render_map(_asymmetric_map())
    flipped = render_map(
        _asymmetric_map(),
        flip_horizontal=True,
        flip_vertical=True,
    )
    expected = default.transpose(Image.Transpose.ROTATE_180)

    assert flipped.size == default.size
    assert flipped.getpixel((76, 4)) == FLOOR
    assert ImageChops.difference(flipped, expected).getbbox() is None


def test_missing_blocks_stay_empty_background() -> None:
    image = render_map(
        _map_at(
            [(0, 0), (2, 0)],
            {
                0: {0: _cell_byte(3)},
                1: {0: _cell_byte(1)},
            },
            block_max=1000,
        )
    )
    gap = image.crop((80, 0, 160, 80))
    blank = Image.new("RGB", gap.size, BACKGROUND)

    assert image.size == (240, 80)
    assert image.getpixel(_center(0)) == FLOOR
    assert image.getpixel(_center(0, origin_x=2, min_x=0)) == WALL
    assert ImageChops.difference(gap, blank).getbbox() is None


def test_map_without_used_blocks_is_rejected() -> None:
    with pytest.raises(ValueError, match="no used blocks"):
        render_map(_map_at([]))


def _map_from_values(values: list[int]) -> BlkMap:
    blocks: list[MapBlock] = []
    cells: list[MapCell] = []
    for index, value in enumerate(values):
        block_index = index // 100
        if index % 100 == 0:
            blocks.append(MapBlock(0, 0, block_index, 0))
        cells.append(MapCell(block_index, index % 100, value))
    return BlkMap(
        header=_header(len(blocks)),
        blocks=tuple(blocks),
        cells=tuple(cells),
        file_size=0,
        trailer_size=0,
    )


def test_reference_floor_count_formats_as_18_93_square_metres() -> None:
    map_data = _map_from_values([3] * 1893 + [1] * 7 + [0] * 10)

    assert count_floor_cells(map_data) == 1893
    assert format_area(count_floor_cells(map_data)) == "18.93m²"


def test_carpet_and_normal_count_and_other_floors_do_not() -> None:
    map_data = _map_at(
        [(0, 0)],
        {0: {0: _cell_byte(3), 1: _cell_byte(2), 2: _cell_byte(1), 3: _cell_byte(0)}},
    )

    assert count_floor_cells(map_data) == 2


def test_sensor_flags_do_not_change_the_floor_count() -> None:
    plain = _map_at([(0, 0)], {0: {0: 3, 1: 2, 2: 1, 3: 0}})
    flagged = _map_at(
        [(0, 0)],
        {0: {0: 3 | 0xFC, 1: 2 | 0xA0, 2: 1 | 0xFC, 3: 0xF0}},
    )

    assert count_floor_cells(plain) == 2
    assert count_floor_cells(flagged) == count_floor_cells(plain)


def test_reserved_block_cells_are_not_counted() -> None:
    header = (1, 100, 10000, 50, 50000, 100, 2, 1000, 1, 10, 100)
    used = bytes([3, 2]) + bytes(98)
    reserved = bytes([3]) * 100
    data = bytearray(struct.pack("<11i", *header))
    data.extend(struct.pack("<4i", 0, 0, 0, 0))
    data.extend(struct.pack("<4i", 0, 0, 1, 0))
    data.extend(used)
    data.extend(reserved)

    parsed = read_blk_bytes(bytes(data))

    assert len(parsed.cells) == 100
    assert count_floor_cells(parsed) == 2


def test_learned_map_footer_preserves_map_pixels_at_each_width() -> None:
    layouts = (
        [(0, 0)],
        [(0, 0), (1, 0)],
        [(0, 0), (0, 1)],
    )

    for positions in layouts:
        floors = {index: {0: _cell_byte(3)} for index in range(len(positions))}
        map_data = _map_at(positions, floors)
        plain = render_map(map_data)
        framed = render_learned_map(map_data)
        flipped_plain = render_map(
            map_data,
            flip_horizontal=True,
            flip_vertical=True,
        )
        flipped = render_learned_map(
            map_data,
            flip_horizontal=True,
            flip_vertical=True,
        )
        footer = framed.crop((0, plain.size[1], framed.size[0], framed.size[1]))
        flipped_footer = flipped.crop(
            (0, flipped_plain.size[1], flipped.size[0], flipped.size[1])
        )

        assert framed.size[0] == plain.size[0]
        assert footer.size == (plain.size[0], framed.size[1] - plain.size[1])
        assert framed.crop((0, 0, plain.size[0], plain.size[1])).tobytes() == (
            plain.tobytes()
        )
        assert flipped.crop(
            (0, 0, flipped_plain.size[0], flipped_plain.size[1])
        ).tobytes() == flipped_plain.tobytes()
        assert footer.tobytes() == flipped_footer.tobytes()
        assert any(
            footer.getpixel((x_pos, y_pos)) == FOOTER_CARD
            for y_pos in range(footer.size[1])
            for x_pos in range(footer.size[0])
        )
        assert any(
            footer.getpixel((x_pos, y_pos)) == FOOTER_INK
            for y_pos in range(footer.size[1])
            for x_pos in range(footer.size[0])
        )
