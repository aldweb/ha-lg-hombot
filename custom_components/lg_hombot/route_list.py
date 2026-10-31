"""Parse the HomBot web interface cleaning-run list.

The browser reads ``sites/maps/status.html`` as JSON and uses ``blackbox``,
``global_map``, ``maps`` and ``logs``. Option 0 is the learned map and is
not a cleaning run. This module only parses that document. It does not
download files and does not import Home Assistant.

When several logs match one run, the last match is kept. That follows the
active browser loop, which overwrites the chosen log on every hit. The
match is tighter than that loop: ``U_475`` does not match ``U_4750``.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass, replace


_ALLOWED_BLACKBOX = (
    "/.../usr/data/blackbox/",
    "/usr/data/blackbox/",
)
_MAX_MAPS = 10
_FILENAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,254}$")
# Fourteen digits are YYYYMMDDHHMMSS. The browser reads the run id from
# the final path segment with this MAPDATA layout. The dot is literal.
_MAPDATA_NAME = re.compile(
    r"^MAPDATA(?P<stamp>[0-9]{14})_[0-9]{6}_(?P<run_id>[^_]+)\.blk$"
)


@dataclass(frozen=True)
class HistoricalRun:
    """One cleaning run named by the web interface list."""

    bbl_name: str
    blk_name: str
    run_id: str
    label: str


@dataclass(frozen=True)
class RouteList:
    """Cleaning runs parsed from a status document.

    The learned map is not included. At most ten map entries are read.
    """

    runs: tuple[HistoricalRun, ...]


def parse_route_list(data: str | bytes) -> RouteList:
    """Parse a status document into cleaning runs.

    Raises:
        ValueError: The document is not JSON or does not have the
            fields the browser reads, or ``blackbox`` is not a known path.
    """

    document = _require_document_shape(_load_document(data))
    runs = [
        run
        for run in (
            _run_from_map_name(name, document["logs"])
            for name in document["maps"][:_MAX_MAPS]
        )
        if run is not None
    ]
    return RouteList(runs=_unique_labels(runs))


def _load_document(data: str | bytes) -> object:
    if isinstance(data, bytes):
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError as err:
            raise ValueError("HomBot route list is not valid JSON") from err
    elif isinstance(data, str):
        text = data
    else:
        raise ValueError("HomBot route list is not valid JSON")

    try:
        return json.loads(text)
    except json.JSONDecodeError as err:
        raise ValueError("HomBot route list is not valid JSON") from err


def _require_document_shape(document: object) -> dict:
    if not isinstance(document, dict):
        raise ValueError("HomBot route list is invalid")

    missing = [
        key
        for key in ("blackbox", "global_map", "maps", "logs")
        if key not in document
    ]
    if missing:
        raise ValueError("HomBot route list is invalid")

    if document["blackbox"] not in _ALLOWED_BLACKBOX:
        raise ValueError("HomBot route list blackbox path is invalid")
    if not isinstance(document["global_map"], str):
        raise ValueError("HomBot route list is invalid")
    if not isinstance(document["maps"], list) or not isinstance(document["logs"], list):
        raise ValueError("HomBot route list is invalid")
    return document


def _run_from_map_name(name: object, logs: list[object]) -> HistoricalRun | None:
    """Build one run, or skip a slot the browser could not use safely."""

    if not _is_safe_filename(name, ".blk") or "${" in name:
        return None

    match = _MAPDATA_NAME.fullmatch(name)
    if match is None:
        return None

    run_id = match.group("run_id")
    bbl_name = _last_matching_log(run_id, logs)
    if bbl_name is None:
        return None

    return HistoricalRun(
        bbl_name=bbl_name,
        blk_name=name,
        run_id=run_id,
        label=_date_label(match.group("stamp")),
    )


def _last_matching_log(run_id: str, logs: list[object]) -> str | None:
    """Return the last safe log whose name contains this run id.

    The browser keeps the last hit. ``U_{run_id}`` must end before another
    digit, so run 475 does not select a log for run 4750.
    """

    pattern = re.compile(rf"U_{re.escape(run_id)}(?!\d)")
    found: str | None = None
    for name in logs:
        if not _is_safe_filename(name, ".bbl") or "${" in name:
            continue
        if pattern.search(name):
            found = name
    return found


def _date_label(stamp: str) -> str:
    """Format YYYYMMDDHHMMSS as ``DD.MM.YYYY - HH:MM``."""

    year = stamp[0:4]
    month = stamp[4:6]
    day = stamp[6:8]
    hour = stamp[8:10]
    minute = stamp[10:12]
    return f"{day}.{month}.{year} - {hour}:{minute}"


def _is_safe_filename(name: object, suffix: str) -> bool:
    """Accept one file name and reject paths, placeholders and empty values."""

    return (
        isinstance(name, str)
        and bool(name)
        and name.strip() == name
        and len(name) <= 255
        and ".." not in name
        and "${" not in name
        and not any(mark in name for mark in ("/", "\\", "\x00", ":"))
        and name.endswith(suffix)
        and _FILENAME.fullmatch(name) is not None
    )


def _unique_labels(runs: list[HistoricalRun]) -> tuple[HistoricalRun, ...]:
    """Append the run id when several runs share a date label.

    A repeated run id still gets the BLK file name, so every label stays
    unique.
    """

    counts = Counter(run.label for run in runs)
    labeled = [
        run
        if counts[run.label] == 1
        else replace(run, label=f"{run.label} ({run.run_id})")
        for run in runs
    ]
    suffixed = Counter(run.label for run in labeled)
    return tuple(
        run
        if suffixed[run.label] == 1
        else replace(run, label=f"{run.label} [{run.blk_name}]")
        for run in labeled
    )
