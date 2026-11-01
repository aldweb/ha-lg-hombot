"""Reader for LG HomBot BBL cleaning logs.

Parsing is read-only and does not import Home Assistant.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


POSITION_RECORD_TYPES = {"POSI", "POSF"}


@dataclass(frozen=True)
class BblRecord:
    """One parsed record from a HomBot BBL log."""

    timestamp: str
    record_type: str
    subtype: str
    values: tuple[str, ...]
    raw_line: str
    line_number: int

    x: Optional[float] = None
    y: Optional[float] = None
    heading: Optional[float] = None

    @property
    def is_position_record(self) -> bool:
        return (
            self.record_type in POSITION_RECORD_TYPES
            and self.x is not None
            and self.y is not None
            and self.heading is not None
        )


@dataclass(frozen=True)
class BblLog:
    """Parsed contents of one HomBot BBL log."""

    log_id: str
    records: tuple[BblRecord, ...]
    map_references: tuple[str, ...]
    source: Optional[Path] = None

    def records_by_type(self, record_type: str) -> tuple[BblRecord, ...]:
        return tuple(
            record
            for record in self.records
            if record.record_type == record_type
        )

    def records_by_subtype(self, subtype: str) -> tuple[BblRecord, ...]:
        return tuple(
            record
            for record in self.records
            if record.subtype == subtype
        )

    @property
    def robot_poses(self) -> tuple[BblRecord, ...]:
        return self.records_by_subtype("RobotPose")


def _parse_position(
    record_type: str,
    values: tuple[str, ...],
) -> tuple[Optional[float], Optional[float], Optional[float]]:
    """Extract x, y and heading from a positional record."""

    if record_type not in POSITION_RECORD_TYPES or len(values) < 3:
        return None, None, None

    try:
        x = float(values[0])
        y = float(values[1])
        heading = float(values[2])
    except (ValueError, TypeError):
        return None, None, None

    return x, y, heading


def _parse_record(line: str, line_number: int) -> Optional[BblRecord]:
    """Parse one CSV-style BBL record."""

    if not line.strip():
        return None

    try:
        fields = next(csv.reader([line], skipinitialspace=True))
    except csv.Error:
        return None

    if len(fields) < 3:
        return None

    timestamp = fields[0].strip()
    record_type = fields[1].strip()
    subtype = fields[2].strip()
    values = tuple(value.strip() for value in fields[3:])

    x, y, heading = _parse_position(record_type, values)

    return BblRecord(
        timestamp=timestamp,
        record_type=record_type,
        subtype=subtype,
        values=values,
        raw_line=line,
        line_number=line_number,
        x=x,
        y=y,
        heading=heading,
    )


def read_bbl(path: str | Path) -> BblLog:
    """Read and parse a HomBot BBL log file."""

    source = Path(path)
    text = source.read_text(encoding="ascii", errors="replace")
    return _parse_bbl_text(text, source)


def read_bbl_bytes(data: bytes) -> BblLog:
    """Read and parse a HomBot BBL log from bytes.

    ``read_bbl`` uses this same implementation after loading the file.
    """

    text = data.decode("ascii", errors="replace")
    return _parse_bbl_text(text, None)


def _parse_bbl_text(text: str, source: Optional[Path]) -> BblLog:
    """Parse BBL text already loaded from a path or a byte string."""

    lines = text.splitlines()

    if not lines:
        if source is None:
            raise ValueError("Empty BBL log")
        raise ValueError(f"Empty BBL log: {source}")

    log_id = lines[0].strip()
    records: list[BblRecord] = []
    map_references: list[str] = []

    for line_number, line in enumerate(lines[1:], start=2):
        record = _parse_record(line, line_number)

        if record is None:
            continue

        records.append(record)

        if record.record_type == "BULK" and record.subtype == "MAPDATA":
            if record.values and record.values[0]:
                map_references.append(record.values[0])

    return BblLog(
        log_id=log_id,
        records=tuple(records),
        map_references=tuple(map_references),
        source=source,
    )
