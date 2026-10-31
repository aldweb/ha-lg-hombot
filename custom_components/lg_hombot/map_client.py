"""HTTP download of the persistent HomBot learned map."""

from __future__ import annotations

import asyncio
from typing import Any

from aiohttp import ClientError, ClientSession, ClientTimeout


MAP_PATH = ".../usr/data/MapReuseNavi"
# Safety cap for the response body. Studied maps are 136044 bytes.
MAX_MAP_BYTES = 1_048_576
REQUEST_TIMEOUT_SECONDS = 30.0


class HomBotMapError(Exception):
    """Raised when the learned HomBot map cannot be downloaded."""


def map_url(base_url: str) -> str:
    """Join the configured robot URL with the learned-map path.

    The path is appended. A leading slash is not used, because
    ``urljoin`` would replace the base path with ``/...``.
    """

    if base_url.endswith("/"):
        return base_url + MAP_PATH
    return base_url + "/" + MAP_PATH


async def _async_read_map_body(response: Any, max_bytes: int) -> bytes:
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
            raise HomBotMapError(
                f"Invalid HomBot map Content-Length: {raw_length!r}"
            ) from err
        if declared > max_bytes:
            raise HomBotMapError(
                f"HomBot map is larger than {max_bytes} bytes: "
                f"Content-Length {declared}"
            )
        if declared < 1:
            raise HomBotMapError("HomBot map response is empty")

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
        raise HomBotMapError(f"HomBot map is larger than {max_bytes} bytes")
    if total < 1:
        raise HomBotMapError("HomBot map response is empty")
    if declared is not None and total != declared:
        raise HomBotMapError(
            f"HomBot map body length {total} does not match "
            f"Content-Length {declared}"
        )
    return b"".join(chunks)


async def async_download_learned_map(
    session: ClientSession,
    base_url: str,
    *,
    timeout_seconds: float = REQUEST_TIMEOUT_SECONDS,
    max_bytes: int = MAX_MAP_BYTES,
) -> bytes:
    """Download MapReuseNavi from the HomBot HTTP interface.

    Raises HomBotMapError for HTTP, network, timeout, and size problems.
    A missing map is an error, not an empty map.
    """

    url = map_url(base_url)
    timeout = ClientTimeout(total=timeout_seconds)

    try:
        async with session.get(url, timeout=timeout) as response:
            if response.status != 200:
                raise HomBotMapError(f"HomBot map returned HTTP {response.status}")
            return await _async_read_map_body(response, max_bytes)
    except HomBotMapError:
        raise
    except asyncio.TimeoutError:
        # Suppress cause: aiohttp/timeout traces may include host or URL details.
        raise HomBotMapError("HomBot map request timed out") from None
    except TimeoutError:
        raise HomBotMapError("HomBot map request timed out") from None
    except ClientError:
        # Suppress cause: ClientError messages often include host/IP/URL.
        raise HomBotMapError("HomBot map connection error") from None
    except OSError:
        raise HomBotMapError("HomBot map OS error") from None
