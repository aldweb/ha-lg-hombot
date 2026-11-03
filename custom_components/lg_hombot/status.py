"""Shared HomBot status.txt parsing and json.cgi commands."""

from __future__ import annotations

from collections.abc import Callable
import logging
from typing import Any
from urllib.parse import quote

from aiohttp import ClientError

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

STATUS_LISTENERS_KEY = "status_listeners"

StatusListener = Callable[[dict[str, str]], None]


class HomBotCommandError(Exception):
    """A json.cgi command could not be sent."""


class HomBotStatusError(Exception):
    """status.txt could not be read."""


def parse_status_text(body: str) -> dict[str, str]:
    """Parse status.txt the same way the vacuum entity always has."""

    attributes: dict[str, str] = {}
    for line in body.splitlines():
        name, var = line.partition("=")[::2]
        attributes[name] = var.strip('"')
    return attributes


def command_url(base_url: str, command: str) -> str:
    """Build the json.cgi URL used for every HomBot command."""

    return base_url + "json.cgi?" + quote(command)


async def async_send_command(
    session: Any,
    base_url: str,
    command: str,
    *,
    require_ok: bool = False,
) -> None:
    """Send one json.cgi command.

    Vacuum commands keep the previous behaviour: the response body is
    read and a non-200 status is ignored. Callers that must not treat a
    rejected command as sent pass ``require_ok``.
    """

    try:
        response = await session.get(command_url(base_url, command))
        await response.read()
    except (ClientError, TimeoutError, OSError) as err:
        if not require_ok:
            raise
        raise HomBotCommandError("HomBot command request failed") from err
    if require_ok and response.status != 200:
        raise HomBotCommandError("HomBot command was rejected")


async def async_fetch_status(session: Any, base_url: str) -> dict[str, str]:
    """Read and parse status.txt. The HTTP result is not a command result."""

    try:
        response = await session.get(base_url + "status.txt")
        body = await response.read()
    except (ClientError, TimeoutError, OSError) as err:
        raise HomBotStatusError("HomBot status request failed") from err
    if response.status != 200:
        raise HomBotStatusError("HomBot status request was rejected")
    try:
        text = body.decode("ascii")
    except UnicodeError as err:
        raise HomBotStatusError("HomBot status response is invalid") from err
    return parse_status_text(text)


def listen_status(hass: Any, url: str, listener: StatusListener) -> Callable[[], None]:
    """Register a listener for status.txt results published by the vacuum."""

    domain = hass.data.setdefault(DOMAIN, {})
    by_url = domain.setdefault(STATUS_LISTENERS_KEY, {})
    bucket: list[StatusListener] = by_url.setdefault(url, [])
    bucket.append(listener)

    def _remove() -> None:
        if listener in bucket:
            bucket.remove(listener)

    return _remove


def prune_status_listeners(domain_data: dict[str, Any]) -> None:
    """Drop empty listener buckets, and the registry itself once it is empty."""

    by_url = domain_data.get(STATUS_LISTENERS_KEY)
    if not isinstance(by_url, dict):
        return
    for url in [url for url, bucket in by_url.items() if not bucket]:
        del by_url[url]
    if not by_url:
        domain_data.pop(STATUS_LISTENERS_KEY, None)


def publish_status(hass: Any, url: str, attributes: dict[str, str]) -> None:
    """Notify listeners. A listener error stays off the vacuum update."""

    try:
        domain = hass.data.get(DOMAIN)
        if not isinstance(domain, dict):
            return
        listeners = domain.get(STATUS_LISTENERS_KEY, {}).get(url, ())
        listeners = tuple(listeners)
    except Exception:  # noqa: BLE001 - publication bookkeeping must not escape
        _LOGGER.exception("HomBot status publication failed")
        return
    for listener in listeners:
        try:
            listener(attributes)
        except Exception:  # noqa: BLE001 - one entity must not break the vacuum poll
            _LOGGER.exception("HomBot status listener failed")
