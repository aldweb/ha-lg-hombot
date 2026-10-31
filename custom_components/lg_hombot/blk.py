"""Reader for LG HomBot .blk map files.

The format implementation is based on the researched Java and JavaScript
readers. Parsing is deliberately read-only and does not import Home Assistant.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from pathlib import Path


HEADER_SIZE = 44
BLOCK_RECORD_SIZE = 16
CELLS_PER_BLOCK = 100


@dataclass(frozen=True)
class BlkHeader:
    map_count: int
    ceil_min: int
    ceil_max: int
    climb_max: int
    unknown: int
    cell_bytes: int
    block_max: int
    flag_max: int
    used_blocks: int
    cell_dim: int
    cell_count: int


@dataclass(frozen=True)
class MapBlock:
    turn: int
    move: int
    posxy: int
    distance: int

    @property
    def x(self) -> int:
        return self.posxy % 100

    @property
    def y(self) -> int:
        return self.posxy // 100


@dataclass(frozen=True)
class MapCell:
    block_index: int
    cell_index: int
    value: int

    @property
    def collision(self) -> bool:
        return bool(self.value & 0x80)

    @property
    def infrared(self) -> bool:
        return bool(self.value & 0x40)

    @property
    def abyss(self) -> bool:
        return bool(self.value & 0x20)

    @property
    def ultrasonic(self) -> bool:
        return bool(self.value & 0x10)

    @property
    def sneaking(self) -> bool:
        return bool(self.value & 0x08)

    @property
    def screwing(self) -> bool:
        return bool(self.value & 0x04)

    @property
    def floor_type(self) -> int:
        return self.value & 0x03


@dataclass(frozen=True)
class BlkMap:
    header: BlkHeader
    blocks: tuple[MapBlock, ...]
    cells: tuple[MapCell, ...]
    file_size: int
    trailer_size: int


def count_floor_cells(map_data: BlkMap) -> int:
    """Count carpet and normal cells in the used blocks.

    Floor types 2 and 3 count. Types 0 and 1 do not. Flag bits in the
    same cell byte are ignored. Cells that were not read for a used
    block are not present on ``map_data`` and therefore do not count.
    """

    return sum(1 for cell in map_data.cells if cell.floor_type in (2, 3))


def _read_header(data: bytes) -> BlkHeader:
    if len(data) < HEADER_SIZE:
        raise ValueError("BLK file is smaller than its 44-byte header")

    values = struct.unpack_from("<11i", data, 0)

    return BlkHeader(*values)


def read_blk(path: str | Path) -> BlkMap:
    """Read and validate the known portions of a HomBot BLK file."""

    path = Path(path)
    return read_blk_bytes(path.read_bytes())


def read_blk_bytes(data: bytes) -> BlkMap:
    """Read and validate the known portions of a HomBot BLK image.

    ``read_blk`` uses this same implementation after loading the file.
    """

    header = _read_header(data)

    if header.block_max < 0 or header.used_blocks < 0:
        raise ValueError("Negative block count in BLK header")

    if header.used_blocks > header.block_max:
        raise ValueError("used_blocks exceeds block_max")

    block_data_offset = HEADER_SIZE

    # The cell area is reserved for block_max blocks, not just used_blocks.
    cell_data_offset = (
        HEADER_SIZE + header.block_max * BLOCK_RECORD_SIZE
    )

    reserved_cell_data_end = (
        cell_data_offset + header.block_max * CELLS_PER_BLOCK
    )

    if len(data) < reserved_cell_data_end:
        raise ValueError(
            "BLK file is truncated: reserved cell area extends beyond file size"
        )

    blocks: list[MapBlock] = []

    for index in range(header.used_blocks):
        offset = block_data_offset + index * BLOCK_RECORD_SIZE

        turn, move, posxy, distance = struct.unpack_from(
            "<4i", data, offset
        )

        blocks.append(
            MapBlock(
                turn=turn,
                move=move,
                posxy=posxy,
                distance=distance,
            )
        )

    cells: list[MapCell] = []

    for block_index in range(header.used_blocks):
        offset = cell_data_offset + block_index * CELLS_PER_BLOCK

        for cell_index in range(CELLS_PER_BLOCK):
            value = data[offset + cell_index]

            cells.append(
                MapCell(
                    block_index=block_index,
                    cell_index=cell_index,
                    value=value,
                )
            )

    return BlkMap(
        header=header,
        blocks=tuple(blocks),
        cells=tuple(cells),
        file_size=len(data),
        trailer_size=len(data) - reserved_cell_data_end,
    )
