"""LG Hombot integration."""

from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform

from .const import DOMAIN
from .route_history import ROUTE_HISTORY_KEY, RouteHistoryState

PLATFORMS = [
    Platform.VACUUM,
    Platform.SENSOR,
    Platform.CAMERA,
    Platform.IMAGE,
    Platform.SELECT,
]


async def async_setup_entry(hass: HomeAssistant, config_entry: ConfigEntry) -> bool:
    """Create route state, then load every platform."""

    domain_data = hass.data.setdefault(DOMAIN, {})
    entry_data = domain_data.setdefault(config_entry.entry_id, {})
    entry_data[ROUTE_HISTORY_KEY] = RouteHistoryState()
    await hass.config_entries.async_forward_entry_setups(config_entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, config_entry: ConfigEntry) -> bool:
    """Unload platforms and drop this entry's shared route state."""

    unloaded = await hass.config_entries.async_unload_platforms(
        config_entry,
        PLATFORMS,
    )
    if unloaded:
        domain_data = hass.data.get(DOMAIN)
        if isinstance(domain_data, dict):
            domain_data.pop(config_entry.entry_id, None)
            if not domain_data:
                hass.data.pop(DOMAIN, None)
    return unloaded
