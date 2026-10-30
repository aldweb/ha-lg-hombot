"""Pytest path setup and lightweight Home Assistant stubs for unit tests."""

from __future__ import annotations

import sys
import types
from pathlib import Path
from unittest.mock import MagicMock

ROOT = Path(__file__).resolve().parents[1]
CUSTOM_COMPONENTS = ROOT / "custom_components"

if str(CUSTOM_COMPONENTS) not in sys.path:
    sys.path.insert(0, str(CUSTOM_COMPONENTS))


def _ensure_homeassistant_stubs() -> None:
    """Allow importing lg_hombot helpers without installing Home Assistant."""
    if "homeassistant" in sys.modules and not isinstance(
        sys.modules["homeassistant"], MagicMock
    ):
        return

    homeassistant = types.ModuleType("homeassistant")
    core = types.ModuleType("homeassistant.core")
    config_entries = types.ModuleType("homeassistant.config_entries")
    const = types.ModuleType("homeassistant.const")
    components = types.ModuleType("homeassistant.components")
    camera_mod = types.ModuleType("homeassistant.components.camera")
    helpers = types.ModuleType("homeassistant.helpers")
    entity = types.ModuleType("homeassistant.helpers.entity")
    entity_platform = types.ModuleType("homeassistant.helpers.entity_platform")
    aiohttp_client = types.ModuleType("homeassistant.helpers.aiohttp_client")

    class Platform:
        VACUUM = "vacuum"
        SENSOR = "sensor"
        CAMERA = "camera"

    class Camera:
        def __init__(self) -> None:
            pass

    class DeviceInfo(dict):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            self.__dict__.update(kwargs)

    const.Platform = Platform
    camera_mod.Camera = Camera
    entity.DeviceInfo = DeviceInfo
    entity_platform.AddEntitiesCallback = object
    aiohttp_client.async_get_clientsession = MagicMock()
    config_entries.ConfigEntry = object
    core.HomeAssistant = object

    sys.modules["homeassistant"] = homeassistant
    sys.modules["homeassistant.core"] = core
    sys.modules["homeassistant.config_entries"] = config_entries
    sys.modules["homeassistant.const"] = const
    sys.modules["homeassistant.components"] = components
    sys.modules["homeassistant.components.camera"] = camera_mod
    sys.modules["homeassistant.helpers"] = helpers
    sys.modules["homeassistant.helpers.entity"] = entity
    sys.modules["homeassistant.helpers.entity_platform"] = entity_platform
    sys.modules["homeassistant.helpers.aiohttp_client"] = aiohttp_client


_ensure_homeassistant_stubs()
