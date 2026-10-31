"""Turn a HomBot BBL log into the route used by the original web interface.

The state machine follows ``sites/maps/logreader.js``. It does not import
Home Assistant and it does not draw.

``Input Homing Command`` is ordinary STRN text. It does not start homing.
Homing starts only from the same checks as the JavaScript source.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

from .bbl import BblLog, BblRecord
from .blk import BlkMap, count_floor_cells


@dataclass(frozen=True)
class RouteWaypoint:
    """One POS record that the original reader draws."""

    record: BblRecord
    x: float
    y: float
    heading: float
    rotation_degrees: int
    state: str


@dataclass(frozen=True)
class RoutePath:
    """One colored fragment.

    ``state`` is the mapped route state from ``getRouteState``. A waypoint
    that changes state is stored on the previous path and again as the
    first point of this path, matching the JavaScript route builder.
    """

    state: str
    waypoints: tuple[RouteWaypoint, ...]


@dataclass(frozen=True)
class RouteMeasurement:
    """Distance, duration and bumps gathered during the route walk.

    ``duration_seconds`` is ``None`` when the first or last POS clock
    cannot be read. Zero means both clocks are the same second.
    ``distance_units`` is the sum of raw pose segments, before dividing
    by 1000.
    """

    distance_units: float
    bumps: int
    duration_seconds: int | None


@dataclass(frozen=True)
class RouteStatistics:
    """The four figures shown under a cleaning route."""

    floor_cells: int
    duration_seconds: int | None
    distance_m: float
    bumps: int

    @property
    def area_m2(self) -> float:
        return self.floor_cells / 100

    @property
    def area_text(self) -> str:
        return format_area(self.floor_cells)

    @property
    def duration_text(self) -> str:
        return format_duration(self.duration_seconds)

    @property
    def distance_text(self) -> str:
        return format_distance(self.distance_m)

    @property
    def bumps_text(self) -> str:
        return str(self.bumps)


@dataclass(frozen=True)
class RouteTrace:
    """Every drawn POS point, in log order, and the colored fragments."""

    waypoints: tuple[RouteWaypoint, ...]
    paths: tuple[RoutePath, ...]
    measurement: RouteMeasurement

    @property
    def line_segment_count(self) -> int:
        """Number of line pieces the original ``drawRoute`` would stroke."""

        return sum(max(len(path.waypoints) - 1, 0) for path in self.paths)


@dataclass
class _Event:
    entry: str
    event_type: str
    info: str
    state: str | None = None
    waypoint: RouteWaypoint | None = None


def read_route(log: BblLog, *, time_zone: str | None = None) -> RouteTrace:
    """Read every POS record with the original route state machine.

    A log that produces no drawable POS point is invalid.

    Raises:
        ValueError: The log contains no POS record with coordinates.
    """

    previous: list[_Event] = []
    current: list[RouteWaypoint] | None = None
    current_state: str | None = None
    built: list[tuple[str, list[RouteWaypoint]]] = []
    ordered: list[RouteWaypoint] = []
    pos_timestamps: list[str] = []
    distance_units = 0.0
    bumps = 0

    for record in log.records:
        event = _read_event(record, previous)
        if event.entry.startswith("POS"):
            pos_timestamps.append(record.timestamp)
            if record.subtype == "Bumping":
                bumps += 1
            if event.waypoint is not None:
                anchor_x, anchor_y = _distance_anchor(previous)
                distance_units += math.hypot(
                    event.waypoint.x - anchor_x,
                    event.waypoint.y - anchor_y,
                )
                ordered.append(event.waypoint)
                if current is not None:
                    current.append(event.waypoint)
                if current is None or event.state != current_state:
                    current = [event.waypoint]
                    current_state = _route_state(event.state or "CLEAN")
                    built.append((current_state, current))
            previous = []
        previous.append(event)

    if not ordered:
        raise ValueError("BBL log contains no POS records")

    return RouteTrace(
        waypoints=tuple(ordered),
        paths=tuple(
            RoutePath(state, tuple(waypoints))
            for state, waypoints in built
        ),
        measurement=RouteMeasurement(
            distance_units=distance_units,
            bumps=bumps,
            duration_seconds=_duration_seconds(pos_timestamps, time_zone),
        ),
    )


def route_statistics(
    map_data: BlkMap,
    log: BblLog,
    *,
    time_zone: str | None = None,
) -> RouteStatistics:
    """Return area, duration, distance and bumps for one cleaning run.

    Area counts every used-block cell whose floor is carpet or normal.
    The log figures come from the same walk as :func:`read_route`.
    ``time_zone`` is an IANA name such as ``Europe/Amsterdam``. ``None``
    keeps the civil-clock difference.
    """

    return statistics_from_trace(map_data, read_route(log, time_zone=time_zone))


def statistics_from_trace(map_data: BlkMap, trace: RouteTrace) -> RouteStatistics:
    """Build the four figures from a map and a route already read."""

    return RouteStatistics(
        floor_cells=count_floor_cells(map_data),
        duration_seconds=trace.measurement.duration_seconds,
        distance_m=trace.measurement.distance_units / 1000,
        bumps=trace.measurement.bumps,
    )


def format_area(floor_cells: int) -> str:
    """Format the cell count as square metres with at most two decimals."""

    whole, frac = divmod(floor_cells, 100)
    if frac == 0:
        return f"{whole}m²"
    if frac % 10 == 0:
        return f"{whole}.{frac // 10}m²"
    return f"{whole}.{frac:02d}m²"


def format_duration(duration_seconds: int | None) -> str:
    """Format a duration the way ``formatDuration`` does.

    ``None`` is an unreadable clock and stays empty. Any other value is
    shown as at least one second. Parts that are zero are omitted.
    """

    if duration_seconds is None:
        return ""

    duration = max(duration_seconds, 1)
    hours = duration // 3600
    minutes = duration // 60 - hours * 60
    seconds = duration - minutes * 60 - hours * 3600
    parts: list[str] = []
    if hours > 0:
        parts.append(f"{hours}h")
    if minutes > 0:
        parts.append(f"{minutes}min")
    if seconds > 0:
        parts.append(f"{seconds}s")
    return " ".join(parts)


def format_distance(distance_m: float) -> str:
    """Format metres with a decimal point and at most two decimals."""

    return f"{_format_decimal(distance_m)}m"


def _format_decimal(value: float) -> str:
    """Round half up to two decimals and drop trailing zeros."""

    scaled = int(math.floor(value * 100 + 0.5))
    whole, frac = divmod(scaled, 100)
    if frac == 0:
        return str(whole)
    if frac % 10 == 0:
        return f"{whole}.{frac // 10}"
    return f"{whole}.{frac:02d}"


def _duration_seconds(
    timestamps: list[str],
    time_zone: str | None,
) -> int | None:
    """Return the seconds between the first and last POS records.

    Only the ``YYYYMMDDHHMMSS`` prefix is used. Without ``time_zone`` the
    result is the civil clock difference, so a daylight-saving boundary
    does not add or remove an hour.

    With a timezone the clocks are local times in that zone and the
    result is the Unix-time difference. ``fold=0`` selects the first
    occurrence of a repeated local time. A time that does not exist in
    the spring-forward gap is not shifted: ``zoneinfo`` supplies its
    offset, while JavaScript ``Date`` can move that clock forward by the
    size of the gap. This function does not add that correction.
    """

    if not timestamps:
        return None
    start = _clock(timestamps[0])
    end = _clock(timestamps[-1])
    if start is None or end is None:
        return None
    if time_zone is None:
        return round((end - start).total_seconds())
    zone = ZoneInfo(time_zone)
    start_unix = start.replace(tzinfo=zone, fold=0).timestamp()
    end_unix = end.replace(tzinfo=zone, fold=0).timestamp()
    return round(end_unix - start_unix)


def _clock(timestamp: str) -> datetime | None:
    if len(timestamp) < 14 or not timestamp[:14].isdigit():
        return None
    text = timestamp[:14]
    try:
        return datetime(
            int(text[0:4]),
            int(text[4:6]),
            int(text[6:8]),
            int(text[8:10]),
            int(text[10:12]),
            int(text[12:14]),
        )
    except ValueError:
        return None


def _distance_anchor(previous: list[_Event]) -> tuple[float, float]:
    """Return the pose anchor for the current POS record.

    A window that starts with STRN, or that has no usable pose, measures
    from ``(0, 0)``. An empty window does the same and does not raise.
    The JavaScript reader throws when that window is empty.
    """

    if not previous or previous[0].entry == "STRN":
        return (0.0, 0.0)
    oldest = previous[0]
    if (
        oldest.entry.startswith("POS")
        and oldest.waypoint is not None
    ):
        return (oldest.waypoint.x, oldest.waypoint.y)
    return (0.0, 0.0)


def first_block_origin(map_data: BlkMap) -> tuple[int, int]:
    """Return the grid position of the first used BLK block.

    That block is the pose origin. A block stored as the ``-1`` sentinel
    has no grid position.
    """

    if not map_data.blocks:
        raise ValueError("BLK map contains no used blocks")

    block = map_data.blocks[0]
    if block.posxy == -1:
        raise ValueError("First BLK block has no coordinates")

    return block.x, block.y


def pose_to_cell(
    pose_x: float,
    pose_y: float,
    origin_x: int,
    origin_y: int,
) -> tuple[float, float]:
    """Convert one robot pose into absolute BLK cell coordinates.

    One hundred pose units are one cell. The origin is the first used
    block, expressed in cells as ``origin * 10``.
    """

    return (
        pose_x / 100 + origin_x * 10,
        pose_y / 100 + origin_y * 10,
    )


def _read_event(record: BblRecord, previous: list[_Event]) -> _Event:
    """Classify one record the way ``readEvent`` does.

    ``previous`` is the events collected since the previous POS record.
    Its first item is the oldest event in that window.
    """

    info = _event_info(record)
    event = _Event(record.record_type, record.subtype, info)
    if not record.record_type.startswith("POS"):
        return event

    if record.x is None or record.y is None or record.heading is None:
        return event

    path_events = _path_events(previous)
    state = "CLEAN"
    # ``indexOf`` is used as a boolean. Zero is false and -1 is true,
    # so the check passes unless the word starts at the first character.
    if record.subtype == "EventPose" and _index_of_is_truthy(info, "Floor"):
        state = "FLOOR_CARPET"
    if record.subtype == "EventPose" and _index_of_is_truthy(info, "Carpet"):
        state = "FLOOR_HARD"
    if record.subtype == "TrapStateStarts":
        state = "TRAPPED"
        if info == "Homing" and not _has_state(previous, ("HOMING",)):
            state = "TRAPPED_END"
    if _has_state(previous, ("TRAPPED",)):
        state = "TRAPPED"
    if _has_state(previous, ("TRAPPED_HOMING", "TRAPPED_END")):
        state = "TRAPPED_HOMING"
    if record.subtype == "TrapStateEnds":
        state = "CLEAN"
        if info == "Homing":
            state = "HOMING"
    if not previous:
        state = "START"
    if state not in ("TRAPPED_END", "TRAPPED_HOMING"):
        if _has_info(path_events, "End Cleaning"):
            state = "END"
        if _has_state(previous, ("END", "HOMING")):
            state = "HOMING"
    if _has_info(previous, "Begin (Docked"):
        state = "START"

    event.state = state
    event.waypoint = RouteWaypoint(
        record=record,
        x=record.x,
        y=record.y,
        heading=record.heading,
        rotation_degrees=int(record.heading / 100) + 90,
        state=state,
    )
    return event


def _event_info(record: BblRecord) -> str:
    """Return the info field ``readEvent`` reads.

    For a POS record that field is the text after x, y and heading.
    For every other record it is the first value. A missing field is
    empty; the JavaScript reader would throw on that line.
    """

    if record.record_type.startswith("POS"):
        if len(record.values) >= 4:
            return record.values[3]
        return ""
    if record.values:
        return record.values[0]
    return ""


def _path_events(previous: list[_Event]) -> list[_Event] | None:
    """Return the events ``checkEventInfo`` may search.

    When the window starts with STRN, the original code searches the
    whole window and drops the distance anchor. Otherwise it skips the
    oldest event. An empty window leaves the search list unset.
    """

    if not previous:
        return None
    slice_at = 0 if previous[0].entry == "STRN" else 1
    if len(previous) > slice_at:
        return previous[slice_at:]
    return None


def _route_state(event_state: str) -> str:
    """Map a raw event state to the color state stored on a path."""

    if event_state in ("TRAPPED_END", "TRAPPED_HOMING"):
        return "TRAPPED_HOMING"
    if event_state == "END":
        return "HOMING"
    if event_state == "FLOOR_CARPET":
        return "CARPET"
    if event_state == "FLOOR_HARD":
        return "CLEAN"
    return event_state


def _has_state(events: list[_Event], states: tuple[str, ...]) -> bool:
    return any(event.state in states for event in events)


def _has_info(events: list[_Event] | None, text: str) -> bool:
    if not events:
        return False
    return any(text in event.info for event in events)


def _index_of_is_truthy(text: str, needle: str) -> bool:
    """Reproduce JavaScript truthiness of ``String.indexOf``.

    ``indexOf`` returns -1 when the needle is absent. -1 is truthy and
    0 is falsy, so the condition fails only when the needle starts the
    text.
    """

    return text.find(needle) != 0
