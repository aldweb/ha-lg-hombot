"""HTTP download of one historical HomBot cleaning log and its BLK map.

The learned-map client is not used. A failure raises HomBotRouteError and
does not return a partial run. This module does not import Home Assistant.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from typing import Any

from aiohttp import ClientError, ClientPayloadError, ClientSession, ClientTimeout

from .bbl import BblLog, read_bbl_bytes
from .blk import BlkMap, read_blk_bytes
from .map_client import MAX_MAP_BYTES, REQUEST_TIMEOUT_SECONDS


BLACKBOX_DIRECTORY = ".../usr/data/blackbox/"
# Filesystem form stored in BULK,MAPDATA, and the HTTP directory prefix.
_MAPDATA_DIRECTORIES = ("/usr/data/blackbox/", ".../usr/data/blackbox/")
# Studied logs are about 25 KiB. Keep a wide margin without an open-ended read.
MAX_BBL_BYTES = 256 * 1024
_FILENAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,254}$")


class HomBotRouteError(Exception):
    """Raised when a historical cleaning run cannot be downloaded."""


@dataclass(frozen=True)
class CleaningRun:
    """One downloaded BBL log and the BLK map named by its MAPDATA record."""

    bbl_name: str
    bbl_bytes: bytes
    log: BblLog
    blk_name: str
    blk_bytes: bytes
    map_data: BlkMap


def blackbox_url(base_url: str, filename: str) -> str:
    """Append a single blackbox file name to the configured robot URL.

    The name is a file name, not a path. A leading slash is not used,
    because ``urljoin`` would replace the base path.
    """

    if (
        not isinstance(filename, str)
        or not filename
        or filename.strip() != filename
        or ".." in filename
        or any(mark in filename for mark in ("/", "\\", "\x00", ":"))
    ):
        raise HomBotRouteError("HomBot cleaning file name is invalid")

    if base_url.endswith("/"):
        return base_url + BLACKBOX_DIRECTORY + filename
    return base_url + "/" + BLACKBOX_DIRECTORY + filename


def _require_filename(name: str, *, suffix: str) -> str:
    """Accept one blackbox file name and reject paths and empty values."""

    if (
        not isinstance(name, str)
        or not name
        or name.strip() != name
        or len(name) > 255
        or ".." in name
        or any(mark in name for mark in ("/", "\\", "\x00", ":"))
        or not name.endswith(suffix)
        or _FILENAME.fullmatch(name) is None
    ):
        raise HomBotRouteError("HomBot cleaning file name is invalid")
    return name


def _blk_name_from_map_reference(reference: str) -> str:
    """Take the BLK file name from an exact MAPDATA reference.

    A bare file name is accepted. A directory is accepted only when it is
    the known blackbox directory. Timestamps and log ids are not used.
    """

    if (
        not isinstance(reference, str)
        or not reference
        or reference.strip() != reference
        or "\\" in reference
        or "\x00" in reference
        or ".." in reference
        or reference.endswith("/")
    ):
        raise HomBotRouteError("HomBot map reference is invalid")

    filename = reference
    for directory in _MAPDATA_DIRECTORIES:
        if reference.startswith(directory):
            filename = reference[len(directory) :]
            break
    else:
        if "/" in reference or reference.startswith("."):
            raise HomBotRouteError("HomBot map reference is invalid")

    if "/" in filename or filename in {"", ".", ".."}:
        raise HomBotRouteError("HomBot map reference is invalid")

    try:
        return _require_filename(filename, suffix=".blk")
    except HomBotRouteError:
        raise HomBotRouteError("HomBot map reference is invalid") from None


def _blk_name_from_log(log: BblLog) -> str:
    """Resolve the single exact MAPDATA file name in a parsed log."""

    if not log.map_references:
        raise HomBotRouteError("HomBot log has no MAPDATA reference")

    names = {
        _blk_name_from_map_reference(reference)
        for reference in log.map_references
    }
    if len(names) != 1:
        raise HomBotRouteError("HomBot log MAPDATA reference is ambiguous")
    return names.pop()


async def _async_read_body(response: Any, max_bytes: int, label: str) -> bytes:
    """Read at most max_bytes + 1 bytes and accept only a bounded body.

    Content-Type is ignored. Content-Length is an early reject when it
    already exceeds the cap, and it must match the bytes actually read.
    """

    raw_length = response.headers.get("Content-Length")
    declared: int | None = None
    if raw_length is not None:
        try:
            declared = int(raw_length)
        except ValueError as err:
            raise HomBotRouteError(
                f"Invalid {label} Content-Length: {raw_length!r}"
            ) from err
        if declared > max_bytes:
            raise HomBotRouteError(
                f"{label} is larger than {max_bytes} bytes: "
                f"Content-Length {declared}"
            )
        if declared < 1:
            raise HomBotRouteError(f"{label} response is empty")

    max_read = max_bytes + 1
    chunks: list[bytes] = []
    total = 0
    while total < max_read:
        chunk = await response.content.read(max_read - total)
        if not chunk:
            break
        chunks.append(chunk)
        total += len(chunk)

    if total > max_bytes:
        raise HomBotRouteError(f"{label} is larger than {max_bytes} bytes")
    if total < 1:
        raise HomBotRouteError(f"{label} response is empty")
    if declared is not None and total != declared:
        raise HomBotRouteError(
            f"{label} body length {total} does not match "
            f"Content-Length {declared}"
        )
    return b"".join(chunks)


async def _async_download(
    session: ClientSession,
    url: str,
    *,
    timeout_seconds: float,
    max_bytes: int,
    label: str,
) -> bytes:
    """GET one blackbox file. Errors do not include the request URL."""

    timeout = ClientTimeout(total=timeout_seconds)
    try:
        async with session.get(url, timeout=timeout) as response:
            if response.status != 200:
                raise HomBotRouteError(f"{label} returned HTTP {response.status}")
            return await _async_read_body(response, max_bytes, label)
    except HomBotRouteError:
        raise
    except asyncio.TimeoutError:
        raise HomBotRouteError(f"{label} request timed out") from None
    except TimeoutError:
        raise HomBotRouteError(f"{label} request timed out") from None
    except ClientPayloadError:
        raise HomBotRouteError(
            f"{label} body length does not match Content-Length"
        ) from None
    except ClientError:
        raise HomBotRouteError(f"{label} connection error") from None
    except OSError:
        raise HomBotRouteError(f"{label} OS error") from None


async def async_download_cleaning_run(
    session: ClientSession,
    base_url: str,
    bbl_name: str,
    *,
    timeout_seconds: float = REQUEST_TIMEOUT_SECONDS,
    max_bbl_bytes: int = MAX_BBL_BYTES,
    max_blk_bytes: int = MAX_MAP_BYTES,
) -> CleaningRun:
    """Download one BBL log and the BLK file named by its MAPDATA record.

    The BLK name comes only from that record. A missing file, an invalid
    name, or a failed parse raises HomBotRouteError. No partial run is
    returned when the BLK download or parse fails.
    """

    safe_bbl_name = _require_filename(bbl_name, suffix=".bbl")
    bbl_bytes = await _async_download(
        session,
        blackbox_url(base_url, safe_bbl_name),
        timeout_seconds=timeout_seconds,
        max_bytes=max_bbl_bytes,
        label="HomBot cleaning log",
    )
    try:
        log = read_bbl_bytes(bbl_bytes)
    except ValueError:
        raise HomBotRouteError("HomBot cleaning log is invalid") from None
    if not log.log_id:
        raise HomBotRouteError("HomBot cleaning log is invalid")

    blk_name = _blk_name_from_log(log)
    blk_bytes = await _async_download(
        session,
        blackbox_url(base_url, blk_name),
        timeout_seconds=timeout_seconds,
        max_bytes=max_blk_bytes,
        label="HomBot cleaning map",
    )
    try:
        map_data = read_blk_bytes(blk_bytes)
    except ValueError:
        raise HomBotRouteError("HomBot cleaning map is invalid") from None

    return CleaningRun(
        bbl_name=safe_bbl_name,
        bbl_bytes=bbl_bytes,
        log=log,
        blk_name=blk_name,
        blk_bytes=blk_bytes,
        map_data=map_data,
    )
