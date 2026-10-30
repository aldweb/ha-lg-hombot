"""Camera entity for the LG HomBot onboard snapshot."""

from __future__ import annotations

import logging

from homeassistant.components.camera import Camera
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .snapshot import JpegSnapshotCache, async_fetch_jpeg_snapshot

_LOGGER = logging.getLogger(__name__)

# HA still-to-MJPEG polling interval, independent of the snapshot cache TTL.
STREAM_INTERVAL_SECONDS = 1.0


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the LG HomBot camera from a config entry."""
    url = config_entry.data.get("url")
    name = config_entry.data.get("name")
    async_add_entities([HombotCamera(name, url)])


class HombotCamera(Camera):
    """Still-image camera backed by the HomBot YUV snapshot endpoint."""

    _attr_has_entity_name = True
    _attr_name = "Camera"
    _attr_brand = "LG"
    _attr_model = "Hombot"
    _attr_frame_interval = STREAM_INTERVAL_SECONDS

    def __init__(self, name: str, url: str) -> None:
        """Initialize the camera."""
        super().__init__()
        self._name = name
        self._url = url
        self._attr_unique_id = f"hombot_{url}_camera"
        self._snapshot_cache = JpegSnapshotCache(self._async_fetch_jpeg)

    @property
    def device_info(self) -> DeviceInfo:
        """Attach the camera to the same device as vacuum and battery."""
        return DeviceInfo(
            identifiers={(DOMAIN, f"hombot_{self._url}")},
            name=self._name,
            manufacturer="LG",
            model="Hombot",
            configuration_url=self._url,
        )

    async def async_camera_image(
        self,
        width: int | None = None,
        height: int | None = None,
    ) -> bytes | None:
        """Return a JPEG snapshot, using a short cache and coalesced fetches."""
        del width, height  # Always native 320x240; HA may scale JPEG later.
        try:
            return await self._snapshot_cache.async_get()
        except Exception:  # noqa: BLE001 - never leak camera faults into the event loop
            _LOGGER.exception("Unexpected HomBot camera error")
            return None

    async def _async_fetch_jpeg(self) -> bytes:
        """Download one YUV frame and convert it to JPEG off the event loop."""
        session = async_get_clientsession(self.hass)
        return await async_fetch_jpeg_snapshot(
            session,
            self._url,
            run_in_executor=self.hass.async_add_executor_job,
        )
