"""Image entity for the persistent HomBot learned map."""

from __future__ import annotations

from inspect import isawaitable
import logging
from datetime import datetime, timedelta, timezone
from io import BytesIO

from homeassistant.components.image import ImageEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .blk import read_blk_bytes
from .const import DOMAIN
from .map_client import HomBotMapError, async_download_learned_map
from .route_render import render_learned_map
from .route_history import RouteHistoryState, get_route_history

_LOGGER = logging.getLogger(__name__)

# Home Assistant's image component polls every 30 seconds and image entities
# opt out by default. This platform opts in at a slow interval instead.
SCAN_INTERVAL = timedelta(minutes=5)


def render_learned_map_png(data: bytes) -> bytes:
    """Parse BLK bytes and return a PNG. Intended to run in an executor."""

    try:
        parsed = read_blk_bytes(data)
        image = render_learned_map(parsed)
    except ValueError as err:
        raise HomBotMapError("HomBot map file is invalid") from err

    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


async def _add_platform_entities(
    async_add_entities: AddEntitiesCallback,
    entities: list,
    *,
    update_before_add: bool,
) -> None:
    """Register entities with Home Assistant.

    The real callback schedules work and returns immediately. A coroutine
    is awaited so an initial update performed by that callback finishes
    before setup returns.
    """

    scheduled = async_add_entities(entities, update_before_add)
    if isawaitable(scheduled):
        await scheduled


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the learned map and the separate historical-route image.

    Only the learned map asks for an update before it is added. The route
    image has no map of its own until a historical run is chosen.
    """

    url = config_entry.data.get("url")
    name = config_entry.data.get("name")
    state = get_route_history(hass, config_entry.entry_id)
    await _add_platform_entities(
        async_add_entities,
        [HombotMapImage(hass, name, url)],
        update_before_add=True,
    )
    await _add_platform_entities(
        async_add_entities,
        [HombotRouteImage(hass, name, url, state)],
        update_before_add=False,
    )


class HombotMapImage(ImageEntity):
    """Learned-map image, separate from the onboard camera."""

    _attr_has_entity_name = True
    _attr_name = "Learned map"
    _attr_content_type = "image/png"
    _attr_should_poll = True

    def __init__(self, hass: HomeAssistant, name: str, url: str) -> None:
        """Initialize the map image."""
        super().__init__(hass)
        self.hass = hass
        self._name = name
        self._url = url
        self._attr_unique_id = f"hombot_{url}_map"
        self._png: bytes | None = None

    @property
    def device_info(self) -> DeviceInfo:
        """Attach the map to the same device as vacuum, battery, and camera."""
        return DeviceInfo(
            identifiers={(DOMAIN, f"hombot_{self._url}")},
            name=self._name,
            manufacturer="LG",
            model="Hombot",
            configuration_url=self._url,
        )

    async def async_update(self) -> None:
        """Download and render MapReuseNavi.

        A failed update clears the previous PNG. Home Assistant's image proxy
        calls ``async_image`` and returns HTTP 500 when that is None, so the
        previous map is not served. ``image_url`` is left unset because the
        base class would cache that response and keep serving it.
        """

        try:
            session = async_get_clientsession(self.hass)
            data = await async_download_learned_map(session, self._url)
            png = await self.hass.async_add_executor_job(
                render_learned_map_png,
                data,
            )
        except HomBotMapError as err:
            _LOGGER.warning("HomBot map update failed: %s", err)
            self._clear_map()
            return
        except Exception:  # noqa: BLE001 - keep map faults off other entities
            _LOGGER.exception("Unexpected HomBot map update error")
            self._clear_map()
            return

        self._png = png
        self._attr_image_last_updated = datetime.now(timezone.utc)
        # image_last_updated is a cached_property on ImageEntity.
        self.__dict__.pop("image_last_updated", None)

    async def async_image(self) -> bytes | None:
        """Return the PNG from the last successful update, or None."""
        return self._png

    def _clear_map(self) -> None:
        """Drop the last PNG so a failed update cannot be served."""
        self._png = None
        self._attr_image_last_updated = None
        self.__dict__.pop("image_last_updated", None)


class HombotRouteImage(ImageEntity):
    """Historical cleaning route. This entity does not poll or store the map PNG."""

    _attr_has_entity_name = True
    _attr_name = "Route"
    _attr_content_type = "image/png"
    _attr_should_poll = False

    def __init__(
        self,
        hass: HomeAssistant,
        name: str,
        url: str,
        state: RouteHistoryState,
    ) -> None:
        """Initialize the route image from the shared route state."""
        super().__init__(hass)
        self.hass = hass
        self._name = name
        self._url = url
        self._state = state
        self._attr_unique_id = f"hombot_{url}_route"

    @property
    def device_info(self) -> DeviceInfo:
        """Attach the route to the same device as vacuum, camera, and map."""
        return DeviceInfo(
            identifiers={(DOMAIN, f"hombot_{self._url}")},
            name=self._name,
            manufacturer="LG",
            model="Hombot",
            configuration_url=self._url,
        )

    async def async_added_to_hass(self) -> None:
        """Follow route renders that happen after this entity exists."""
        await super().async_added_to_hass()
        self._sync_timestamp()
        self._state.add_listener(self._on_route_changed)

    async def async_will_remove_from_hass(self) -> None:
        """Stop following the shared route state."""
        self._state.remove_listener(self._on_route_changed)
        remove = getattr(super(), "async_will_remove_from_hass", None)
        if remove is not None:
            await remove()

    async def async_image(self) -> bytes | None:
        """Return the shared route PNG, or None when no route is rendered."""
        return self._state.png

    def _on_route_changed(self) -> None:
        """Copy the shared timestamp and ask Home Assistant to refresh."""
        self._sync_timestamp()
        writer = getattr(self, "async_write_ha_state", None)
        if writer is not None:
            writer()

    def _sync_timestamp(self) -> None:
        """Publish the last successful route render time."""
        self._attr_image_last_updated = self._state.rendered_at
        # image_last_updated is a cached_property on ImageEntity.
        self.__dict__.pop("image_last_updated", None)
