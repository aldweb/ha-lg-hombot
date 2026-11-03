"""Shared device description and URL handling for the LG HomBot."""

from __future__ import annotations

from urllib.parse import urlsplit

from homeassistant.helpers.entity import DeviceInfo

from .const import DOMAIN


def normalize_url(url: str) -> str:
    """Return the robot URL with surrounding spaces removed and a final slash.

    Every request path of the integration (``status.txt``, ``json.cgi``,
    ``images/snapshot.yuv``) is appended to this URL.
    """

    url = str(url).strip()
    if not url.endswith("/"):
        url += "/"
    return url


def validate_url(url: str) -> str:
    """Return the normalized URL, or raise ValueError for an unusable one."""

    normalized = normalize_url(url)
    try:
        parts = urlsplit(normalized)
        host = parts.hostname
        parts.port  # noqa: B018 - raises ValueError for an invalid port
    except ValueError as err:
        raise ValueError("Invalid HomBot URL") from err
    if parts.scheme not in ("http", "https") or not host:
        raise ValueError("Invalid HomBot URL")
    return normalized


def url_unique_id(url: str) -> str:
    """Return a config entry unique id (host and port) for a valid URL."""

    parts = urlsplit(normalize_url(url))
    host = (parts.hostname or "").lower()
    return f"{host}:{parts.port}" if parts.port else host


def hombot_device_info(name: str, url: str) -> DeviceInfo:
    """Return the one device every HomBot entity is attached to."""

    return DeviceInfo(
        identifiers={(DOMAIN, f"hombot_{url}")},
        name=name,
        manufacturer="LG",
        model="Hombot",
        configuration_url=url,
    )
