"""Rendering cost and size limits of render_map."""

from __future__ import annotations

import hashlib
import random
import struct
import time

import pytest
from PIL import Image

from lg_hombot import map_render
from lg_hombot.blk import CELLS_PER_BLOCK, read_blk_bytes


def _blk(positions: list[int], cell_value: int = 3) -> bytes:
    used = len(positions)
    header = struct.pack("<11i", 1, 0, 0, 0, 0, 0, used, 0, used, 0, 0)
    blocks = b"".join(struct.pack("<4i", 0, 0, posxy, 0) for posxy in positions)
    cells = bytes(
        (cell_value + index) % 256 if index % 7 == 0 else cell_value
        for index in range(used * CELLS_PER_BLOCK)
    )
    return header + blocks + cells


def _random_map(seed: int, positions: list[int]) -> bytes:
    rnd = random.Random(seed)
    used = len(positions)
    header = struct.pack("<11i", 1, 0, 0, 0, 0, 0, used, 0, used, 0, 0)
    blocks = b"".join(struct.pack("<4i", 0, 0, posxy, 0) for posxy in positions)
    return header + blocks + rnd.randbytes(used * CELLS_PER_BLOCK)


# SHA-256 of the RGB pixels rendered by version 2.2.0, before the render was
# made linear. The output must stay identical, pixel for pixel.
_GRID = [y * 100 + x for y in range(3, 9) for x in range(5, 12)]
_EDGE = [7, 8, 9, 108, 109, 208]
_GOLDEN = {
    ("grid", False, False): "35ff69cfc3d26f52c391be3aebadbb93733921c9d17d06984f103789cfef33dc",
    ("grid", False, True): "983fae18db9c24e47e8366950c03356057e7e890b3b364dd5977e6d972a56f19",
    ("grid", True, False): "69b17395159f9dd2ec1233d1fea51af7604d3c04409fa79567efc16c288a5ce2",
    ("grid", True, True): "dcfcb6465d2289c6c03658ef440c19fa4d34c3342896006938bf6cc381ea9dc4",
    ("edge", False, False): "07c056e1b73c2fe48b296decd4a13216877c19b6b3d16009690a83771a2c104f",
    ("edge", False, True): "ca904044d54adfdd947a4ecf303ae79b7b1de59adfeac177bd72b2fe39d1bce3",
    ("edge", True, False): "70fed83ccef4266bd771315397094496faeebedfe5471ee70fd0f3c35dcddabb",
    ("edge", True, True): "cffa3bf03142d40f0c093297d1b4f98df5f2c9ed37fea9661cd833588d75a2dd",
}


@pytest.mark.parametrize(("name", "flip_h", "flip_v"), list(_GOLDEN))
def test_render_is_pixel_identical_to_the_previous_renderer(
    name: str, flip_h: bool, flip_v: bool
) -> None:
    seed, positions = (1, _GRID) if name == "grid" else (2, _EDGE)
    parsed = read_blk_bytes(_random_map(seed, positions))
    image = map_render.render_map(
        parsed, flip_horizontal=flip_h, flip_vertical=flip_v
    )
    assert hashlib.sha256(image.tobytes()).hexdigest() == _GOLDEN[(name, flip_h, flip_v)]


def test_render_time_grows_linearly_with_blocks() -> None:
    """400 blocks took about 8 s before the fix and 0.2 s after. Keep a wide margin."""
    positions = [(i // 20) * 100 + (i % 20) for i in range(400)]
    parsed = read_blk_bytes(_blk(positions))
    started = time.monotonic()
    image = map_render.render_map(parsed)
    assert time.monotonic() - started < 2.0
    assert image.size == (1600, 1600)


def test_extent_beyond_the_limit_is_rejected_before_allocation() -> None:
    parsed = read_blk_bytes(_blk([99, 2_000_000_000]))
    with pytest.raises(ValueError, match="too large"):
        map_render.render_map(parsed)


def test_limit_is_checked_on_the_total_area(monkeypatch) -> None:
    parsed = read_blk_bytes(_blk([0, 1]))
    monkeypatch.setattr(map_render, "MAX_IMAGE_PIXELS", 100)
    with pytest.raises(ValueError, match="too large"):
        map_render.render_map(parsed)
    monkeypatch.setattr(map_render, "MAX_IMAGE_PIXELS", 160 * 80)
    assert isinstance(map_render.render_map(parsed), Image.Image)
