"""HTTP download helpers for HomBot camera snapshots."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any

from aiohttp import ClientError, ClientSession, ClientTimeout

from .yuv import FRAME_BYTES, validate_yuv_frame, yuv422_planar_to_jpeg

_LOGGER = logging.getLogger(__name__)

SNAPSHOT_PATH = "images/snapshot.yuv"
REQUEST_TIMEOUT_SECONDS = 5.0
CACHE_SECONDS = 4.0


class HomBotSnapshotError(Exception):
    """Raised when a HomBot snapshot cannot be obtained or converted."""


class JpegSnapshotCache:
    """Coalesce concurrent JPEG fetches and cache successful frames briefly."""

    def __init__(
        self,
        fetch: Callable[[], Awaitable[bytes]],
        *,
        cache_seconds: float = CACHE_SECONDS,
    ) -> None:
        self._fetch = fetch
        self._cache_seconds = cache_seconds
        self._cache_image: bytes | None = None
        self._cache_monotonic: float | None = None
        self._fetch_lock = asyncio.Lock()

    async def async_get(self) -> bytes | None:
        """Return a cached JPEG, fetch a new one, or None after a failed fetch."""
        now = time.monotonic()
        if self._is_cache_valid(now):
            return self._cache_image

        async with self._fetch_lock:
            now = time.monotonic()
            if self._is_cache_valid(now):
                return self._cache_image

            # Temporary DEBUG timing for cache-miss fetch duration (HTTP+JPEG).
            # Do not log URLs, headers, tokens, or exception text here.
            t0 = time.monotonic()
            try:
                jpeg = await self._fetch()
            except HomBotSnapshotError as err:
                _LOGGER.debug(
                    "HomBot snapshot fetch duration_s=%.6f outcome=error",
                    time.monotonic() - t0,
                )
                _LOGGER.warning("%s", err)
                return None
            except Exception:  # noqa: BLE001 - keep camera faults contained
                _LOGGER.debug(
                    "HomBot snapshot fetch duration_s=%.6f outcome=error",
                    time.monotonic() - t0,
                )
                _LOGGER.exception("Unexpected HomBot snapshot fetch error")
                return None

            _LOGGER.debug(
                "HomBot snapshot fetch duration_s=%.6f outcome=ok jpeg_bytes=%d",
                time.monotonic() - t0,
                len(jpeg),
            )
            self._cache_image = jpeg
            self._cache_monotonic = time.monotonic()
            return jpeg

    def _is_cache_valid(self, now: float) -> bool:
        return (
            self._cache_image is not None
            and self._cache_monotonic is not None
            and now - self._cache_monotonic < self._cache_seconds
        )


async def _async_read_yuv_body(response: Any, expected_size: int) -> bytes:
    """Read at most expected_size + 1 bytes and accept only an exact frame.

    Content-Length is used only as an early reject when it already exceeds the
    frame size. Acceptance always depends on the bytes actually read.
    """
    raw_length = response.headers.get("Content-Length")
    if raw_length is not None:
        try:
            declared = int(raw_length)
        except ValueError as err:
            raise HomBotSnapshotError(
                f"Invalid HomBot YUV Content-Length: {raw_length!r}"
            ) from err
        if declared > expected_size:
            raise HomBotSnapshotError(
                f"Invalid HomBot YUV frame size: expected {expected_size} bytes, "
                f"got Content-Length {declared}"
            )

    # Read one extra byte so an oversized body is detected without buffering it all.
    # aiohttp may return partial chunks, so accumulate up to the hard cap.
    max_read = expected_size + 1
    chunks: list[bytes] = []
    total = 0
    while total < max_read:
        chunk = await response.content.read(max_read - total)
        if not chunk:
            break
        chunks.append(chunk)
        total += len(chunk)

    yuv_data = b"".join(chunks)
    try:
        validate_yuv_frame(yuv_data, expected_size=expected_size)
    except ValueError as err:
        raise HomBotSnapshotError(str(err)) from err
    return yuv_data


async def async_download_yuv_frame(
    session: ClientSession,
    base_url: str,
    *,
    timeout_seconds: float = REQUEST_TIMEOUT_SECONDS,
    expected_size: int = FRAME_BYTES,
) -> bytes:
    """Download one YUV frame from the HomBot HTTP interface.

    Raises HomBotSnapshotError for HTTP, network, timeout, and size problems.
    """
    snapshot_url = base_url + SNAPSHOT_PATH
    timeout = ClientTimeout(total=timeout_seconds)

    try:
        async with session.get(snapshot_url, timeout=timeout) as response:
            if response.status != 200:
                raise HomBotSnapshotError(
                    f"HomBot camera returned HTTP {response.status}"
                )
            return await _async_read_yuv_body(response, expected_size)
    except HomBotSnapshotError:
        raise
    except asyncio.TimeoutError:
        # Suppress cause: aiohttp/timeout traces may include host or URL details.
        raise HomBotSnapshotError("HomBot camera request timed out") from None
    except TimeoutError:
        raise HomBotSnapshotError("HomBot camera request timed out") from None
    except ClientError:
        # Suppress cause: ClientError messages often include host/IP/URL.
        raise HomBotSnapshotError("HomBot camera connection error") from None
    except OSError:
        raise HomBotSnapshotError("HomBot camera OS error") from None


async def async_fetch_jpeg_snapshot(
    session: ClientSession,
    base_url: str,
    *,
    run_in_executor: Any,
    timeout_seconds: float = REQUEST_TIMEOUT_SECONDS,
    expected_size: int = FRAME_BYTES,
) -> bytes:
    """Download a YUV frame and convert it to JPEG via run_in_executor."""
    yuv_data = await async_download_yuv_frame(
        session,
        base_url,
        timeout_seconds=timeout_seconds,
        expected_size=expected_size,
    )
    try:
        return await run_in_executor(yuv422_planar_to_jpeg, yuv_data)
    except Exception:  # noqa: BLE001 - wrap conversion failures
        # Suppress cause: avoid leaking paths or library details in HA logs.
        raise HomBotSnapshotError("HomBot YUV to JPEG conversion failed") from None
