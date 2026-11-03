"""HTTP download of the HomBot cleaning-run list.

The list is the JSON document at ``sites/maps/status.html``. This module
does not parse it and does not import Home Assistant. Error messages do
not include the robot address or the request URL.
"""

from __future__ import annotations

import asyncio
from typing import Any

from aiohttp import ClientError, ClientPayloadError, ClientSession, ClientTimeout


ROUTE_LIST_PATH = "sites/maps/status.html"
# Older firmware has no status document. Its maps page lists the same runs.
MAPS_PAGE_PATH = "sites/maps.html"
# The web interface uses an 8 second timeout for this document.
REQUEST_TIMEOUT_SECONDS = 8.0
# Ten file names fit in a few kilobytes. 64 KiB rejects an unexpected body.
MAX_ROUTE_LIST_BYTES = 64 * 1024


class HomBotRouteListError(Exception):
    """Raised when the cleaning-run list cannot be downloaded."""


def route_list_url(base_url: str, path: str = ROUTE_LIST_PATH) -> str:
    """Append a page path to the configured robot URL.

    The path is appended. A leading slash is not used, because ``urljoin``
    would replace the base path.
    """

    if base_url.endswith("/"):
        return base_url + path
    return base_url + "/" + path


async def _async_read_body(response: Any, max_bytes: int) -> bytes:
    """Read at most max_bytes + 1 bytes and accept only a bounded body.

    Content-Type is ignored. Content-Length is an early reject when it
    already exceeds the cap, and it must match the bytes actually read.
    """

    label = "HomBot route list"
    raw_length = response.headers.get("Content-Length")
    declared: int | None = None
    if raw_length is not None:
        try:
            declared = int(raw_length)
        except ValueError as err:
            raise HomBotRouteListError(
                f"Invalid {label} Content-Length: {raw_length!r}"
            ) from err
        if declared > max_bytes:
            raise HomBotRouteListError(
                f"{label} is larger than {max_bytes} bytes: "
                f"Content-Length {declared}"
            )
        if declared < 1:
            raise HomBotRouteListError(f"{label} response is empty")

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
        raise HomBotRouteListError(f"{label} is larger than {max_bytes} bytes")
    if total < 1:
        raise HomBotRouteListError(f"{label} response is empty")
    if declared is not None and total != declared:
        raise HomBotRouteListError(
            f"{label} body length {total} does not match "
            f"Content-Length {declared}"
        )
    return b"".join(chunks)


async def async_download_route_list(
    session: ClientSession,
    base_url: str,
    *,
    path: str = ROUTE_LIST_PATH,
    timeout_seconds: float = REQUEST_TIMEOUT_SECONDS,
    max_bytes: int = MAX_ROUTE_LIST_BYTES,
) -> bytes:
    """Download ``sites/maps/status.html``, or another page given by ``path``.

    Only HTTP 200 is accepted. The caller parses the bytes. An empty body
    is an error. Raises HomBotRouteListError without the request URL.
    """

    url = route_list_url(base_url, path)
    timeout = ClientTimeout(total=timeout_seconds)
    try:
        async with session.get(url, timeout=timeout) as response:
            if response.status != 200:
                raise HomBotRouteListError(
                    f"HomBot route list returned HTTP {response.status}"
                )
            return await _async_read_body(response, max_bytes)
    except HomBotRouteListError:
        raise
    except asyncio.TimeoutError:
        raise HomBotRouteListError("HomBot route list request timed out") from None
    except TimeoutError:
        raise HomBotRouteListError("HomBot route list request timed out") from None
    except ClientPayloadError:
        raise HomBotRouteListError(
            "HomBot route list body length does not match Content-Length"
        ) from None
    except ClientError:
        raise HomBotRouteListError("HomBot route list connection error") from None
    except OSError:
        raise HomBotRouteListError("HomBot route list OS error") from None
