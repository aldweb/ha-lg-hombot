"""Synthetic tests for the HomBot BLK reader."""

from __future__ import annotations

import struct
from pathlib import Path

import pytest

from lg_hombot.blk import CELLS_PER_BLOCK, HEADER_SIZE, read_blk, read_blk_bytes


def _encode_blk(
    header: tuple[int, ...],
    block_records: list[tuple[int, int, int, int]],
    cells_by_slot: list[bytes],
    trailer: bytes = b"",
) -> bytes:
    """Build one BLK image. Slots follow block_max, not used_blocks."""

    block_max = header[6]
    if len(header) != 11:
        raise ValueError("header must contain 11 integers")
    if len(block_records) != block_max or len(cells_by_slot) != block_max:
        raise ValueError("fixtures must reserve every block slot")

    data = bytearray(struct.pack("<11i", *header))
    for turn, move, posxy, distance in block_records:
        data.extend(struct.pack("<4i", turn, move, posxy, distance))
    for cells in cells_by_slot:
        if len(cells) != CELLS_PER_BLOCK:
            raise ValueError("each slot needs 100 cell bytes")
        data.extend(cells)
    data.extend(trailer)
    return bytes(data)


def _sample_header(**overrides: int) -> tuple[int, ...]:
    values = {
        "map_count": 1,
        "ceil_min": 100,
        "ceil_max": 10000,
        "climb_max": 50,
        "unknown": 50000,
        "cell_bytes": 100,
        "block_max": 2,
        "flag_max": 1000,
        "used_blocks": 1,
        "cell_dim": 10,
        "cell_count": 100,
    }
    values.update(overrides)
    return tuple(values[name] for name in (
        "map_count",
        "ceil_min",
        "ceil_max",
        "climb_max",
        "unknown",
        "cell_bytes",
        "block_max",
        "flag_max",
        "used_blocks",
        "cell_dim",
        "cell_count",
    ))


def _used_cells() -> bytearray:
    cells = bytearray(CELLS_PER_BLOCK)
    cells[0] = 0xFF
    cells[1] = 0x80
    cells[2] = 0x40
    cells[3] = 0x20
    cells[4] = 0x10
    cells[5] = 0x08
    cells[6] = 0x04
    cells[7] = 0x03
    cells[8] = 0x02
    cells[9] = 0x01
    cells[15] = 0x00
    return cells


def _write_sample(path: Path, trailer: bytes = b"TRLR") -> None:
    header = _sample_header()
    used = _used_cells()
    unused = bytes([0xA5]) * CELLS_PER_BLOCK
    path.write_bytes(_encode_blk(
        header,
        [
            (7, 11, 4950, 300),
            (9, 9, 9999, 9),
        ],
        [used, unused],
        trailer,
    ))


def test_parser_does_not_import_home_assistant() -> None:
    source = Path(read_blk.__globals__["__file__"]).read_text(encoding="utf-8")
    assert "homeassistant" not in source


def test_header_fields(tmp_path: Path) -> None:
    path = tmp_path / "header.blk"
    _write_sample(path)

    header = read_blk(path).header

    assert header.map_count == 1
    assert header.ceil_min == 100
    assert header.ceil_max == 10000
    assert header.climb_max == 50
    assert header.unknown == 50000
    assert header.cell_bytes == 100
    assert header.block_max == 2
    assert header.flag_max == 1000
    assert header.used_blocks == 1
    assert header.cell_dim == 10
    assert header.cell_count == 100


def test_only_used_block_position_is_returned(tmp_path: Path) -> None:
    path = tmp_path / "blocks.blk"
    _write_sample(path)

    blocks = read_blk(path).blocks

    assert len(blocks) == 1
    block = blocks[0]
    assert (block.turn, block.move, block.posxy, block.distance) == (7, 11, 4950, 300)
    assert block.x == 50
    assert block.y == 49


def test_absolute_cell_coordinates(tmp_path: Path) -> None:
    path = tmp_path / "cells.blk"
    _write_sample(path)
    parsed = read_blk(path)
    block = parsed.blocks[0]
    cell = next(item for item in parsed.cells if item.cell_index == 15)

    # Researched absolute grid: X is the fast index inside the 10-by-10 block.
    grid_x = block.x * 10 + cell.cell_index % 10
    grid_y = block.y * 10 + cell.cell_index // 10

    assert (grid_x, grid_y) == (505, 491)
    assert cell.floor_type == 0


def test_floor_types(tmp_path: Path) -> None:
    path = tmp_path / "floors.blk"
    _write_sample(path)
    cells = {cell.cell_index: cell for cell in read_blk(path).cells}

    assert cells[15].floor_type == 0
    assert cells[9].floor_type == 1
    assert cells[8].floor_type == 2
    assert cells[7].floor_type == 3
    assert cells[1].floor_type == 0


def test_all_sensor_bits(tmp_path: Path) -> None:
    path = tmp_path / "flags.blk"
    _write_sample(path)
    cells = {cell.cell_index: cell for cell in read_blk(path).cells}
    combined = cells[0]

    assert combined.collision is True
    assert combined.infrared is True
    assert combined.abyss is True
    assert combined.ultrasonic is True
    assert combined.sneaking is True
    assert combined.screwing is True
    assert combined.floor_type == 3

    assert cells[1].collision is True
    assert cells[1].infrared is False
    assert cells[2].infrared is True
    assert cells[2].collision is False
    assert cells[3].abyss is True
    assert cells[4].ultrasonic is True
    assert cells[5].sneaking is True
    assert cells[6].screwing is True
    assert cells[7].collision is False
    assert cells[7].infrared is False
    assert cells[7].abyss is False
    assert cells[7].ultrasonic is False
    assert cells[7].sneaking is False
    assert cells[7].screwing is False


def test_unused_block_slot_is_not_parsed(tmp_path: Path) -> None:
    path = tmp_path / "unused.blk"
    _write_sample(path)
    parsed = read_blk(path)

    assert len(parsed.blocks) == 1
    assert len(parsed.cells) == CELLS_PER_BLOCK
    assert all(cell.value != 0xA5 for cell in parsed.cells)
    assert all(block.posxy != 9999 for block in parsed.blocks)


def test_trailer_is_bytes_after_reserved_cell_area(tmp_path: Path) -> None:
    path = tmp_path / "trailer.blk"
    trailer = b"TRLR-EXTRA"
    _write_sample(path, trailer)
    parsed = read_blk(path)

    reserved_end = HEADER_SIZE + 2 * 16 + 2 * CELLS_PER_BLOCK
    assert parsed.file_size == reserved_end + len(trailer)
    assert parsed.trailer_size == len(trailer)
    assert path.read_bytes()[reserved_end:] == trailer


def test_short_header_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "short.blk"
    path.write_bytes(b"\x00" * 20)

    with pytest.raises(ValueError, match="44-byte header"):
        read_blk(path)


def test_reserved_area_uses_block_max_not_used_blocks(tmp_path: Path) -> None:
    path = tmp_path / "truncated.blk"
    header = _sample_header(block_max=2, used_blocks=1)
    data = bytearray(struct.pack("<11i", *header))
    data.extend(struct.pack("<4i", 1, 2, 5050, 3))
    data.extend(b"\x00" * 16)
    # Enough cells for the one used block, not for both reserved slots.
    data.extend(bytes(CELLS_PER_BLOCK))
    path.write_bytes(data)

    with pytest.raises(ValueError, match="truncated"):
        read_blk(path)


def test_negative_block_count_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "negative.blk"
    header = _sample_header(block_max=-1, used_blocks=0)
    path.write_bytes(struct.pack("<11i", *header))

    with pytest.raises(ValueError, match="Negative block count"):
        read_blk(path)


def test_read_blk_bytes_matches_read_blk(tmp_path: Path) -> None:
    path = tmp_path / "same.blk"
    _write_sample(path)
    payload = path.read_bytes()

    from_path = read_blk(path)
    from_bytes = read_blk_bytes(payload)

    assert from_bytes == from_path
    assert from_bytes.header.used_blocks == 1
    assert from_bytes.header.block_max == 2
    assert len(from_bytes.blocks) == len(from_path.blocks)
    assert from_bytes.cells == from_path.cells
    assert from_bytes.file_size == len(payload)
    assert from_bytes.trailer_size == from_path.trailer_size


def test_read_blk_bytes_rejects_truncated_payload() -> None:
    header = _sample_header(block_max=2, used_blocks=1)
    data = bytearray(struct.pack("<11i", *header))
    data.extend(struct.pack("<4i", 1, 2, 5050, 3))
    data.extend(b"\x00" * 16)
    data.extend(bytes(CELLS_PER_BLOCK))

    with pytest.raises(ValueError, match="truncated"):
        read_blk_bytes(bytes(data))


def test_used_blocks_above_block_max_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "too-many.blk"
    header = _sample_header(block_max=1, used_blocks=2)
    path.write_bytes(struct.pack("<11i", *header))

    with pytest.raises(ValueError, match="used_blocks exceeds block_max"):
        read_blk(path)
