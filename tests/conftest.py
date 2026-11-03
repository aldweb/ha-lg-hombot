"""Pytest path setup and lightweight Home Assistant stubs for unit tests."""

from __future__ import annotations

import sys
import types
from enum import IntFlag, StrEnum
from pathlib import Path
from unittest.mock import MagicMock

import pytest

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
    components.__path__ = []  # type: ignore[attr-defined]
    camera_mod = types.ModuleType("homeassistant.components.camera")
    image_mod = types.ModuleType("homeassistant.components.image")
    select_mod = types.ModuleType("homeassistant.components.select")
    switch_mod = types.ModuleType("homeassistant.components.switch")
    sensor_mod = types.ModuleType("homeassistant.components.sensor")
    vacuum_mod = types.ModuleType("homeassistant.components.vacuum")
    helpers = types.ModuleType("homeassistant.helpers")
    entity = types.ModuleType("homeassistant.helpers.entity")
    entity_platform = types.ModuleType("homeassistant.helpers.entity_platform")
    aiohttp_client = types.ModuleType("homeassistant.helpers.aiohttp_client")
    exceptions_mod = types.ModuleType("homeassistant.exceptions")

    class Platform:
        VACUUM = "vacuum"
        SENSOR = "sensor"
        CAMERA = "camera"
        IMAGE = "image"
        SELECT = "select"
        SWITCH = "switch"

    class VacuumEntityFeature(IntFlag):
        STATE = 1
        FAN_SPEED = 2
        START = 4
        PAUSE = 8
        RETURN_HOME = 16
        STOP = 32

    class VacuumActivity(StrEnum):
        CLEANING = "cleaning"
        DOCKED = "docked"
        IDLE = "idle"
        PAUSED = "paused"
        RETURNING = "returning"

    class StateVacuumEntity:
        """Minimal stand-in for homeassistant.components.vacuum.StateVacuumEntity."""

        hass = None
        _attr_available = True

        def async_write_ha_state(self) -> None:
            self.state_writes = getattr(self, "state_writes", 0) + 1

        @property
        def available(self):
            return self._attr_available

    class SensorDeviceClass:
        BATTERY = "battery"

    class SensorStateClass:
        MEASUREMENT = "measurement"

    class SensorEntity:
        """Minimal stand-in for homeassistant.components.sensor.SensorEntity."""

        _attr_available = True
        _attr_native_value = None

        async def async_added_to_hass(self) -> None:
            return None

        async def async_will_remove_from_hass(self) -> None:
            return None

        def async_write_ha_state(self) -> None:
            self.state_writes = getattr(self, "state_writes", 0) + 1

        @property
        def available(self):
            return self._attr_available

        @property
        def native_value(self):
            return self._attr_native_value

    class Camera:
        def __init__(self) -> None:
            pass

    class ImageEntity:
        """Minimal stand-in for homeassistant.components.image.ImageEntity."""

        _attr_content_type = "image/jpeg"
        _attr_should_poll = False
        _attr_image_last_updated = None

        def __init__(self, hass, verify_ssl: bool = False) -> None:
            self.hass = hass

        async def async_image(self):
            return None

        async def async_added_to_hass(self) -> None:
            return None

        async def async_will_remove_from_hass(self) -> None:
            return None

        def async_write_ha_state(self) -> None:
            self.state_writes = getattr(self, "state_writes", 0) + 1

        @property
        def content_type(self):
            return self._attr_content_type

    class SelectEntity:
        """Minimal stand-in for homeassistant.components.select.SelectEntity."""

        _attr_current_option = None
        _attr_options: list[str] = []
        _attr_available = True
        _attr_should_poll = True

        async def async_added_to_hass(self) -> None:
            return None

        async def async_internal_added_to_hass(self) -> None:
            await self.async_added_to_hass()

        async def async_will_remove_from_hass(self) -> None:
            return None

        def async_write_ha_state(self) -> None:
            self.state_writes = getattr(self, "state_writes", 0) + 1

        @property
        def options(self) -> list[str]:
            return self._attr_options

        @property
        def current_option(self) -> str | None:
            return self._attr_current_option

        @property
        def available(self):
            return self._attr_available

    class SwitchEntity:
        """Minimal stand-in for homeassistant.components.switch.SwitchEntity."""

        _attr_is_on = None
        _attr_available = True
        _attr_should_poll = True

        async def async_added_to_hass(self) -> None:
            return None

        async def async_will_remove_from_hass(self) -> None:
            return None

        def async_write_ha_state(self) -> None:
            self.state_writes = getattr(self, "state_writes", 0) + 1

        @property
        def is_on(self):
            return self._attr_is_on

        @property
        def available(self):
            return self._attr_available

    class HomeAssistantError(Exception):
        """Stand-in for homeassistant.exceptions.HomeAssistantError."""

    class DeviceInfo(dict):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            self.__dict__.update(kwargs)

    class ConfigFlowResult(dict):
        pass

    class ConfigFlow:
        """Minimal stand-in for homeassistant.config_entries.ConfigFlow."""

        configured_ids: set = set()

        def __init_subclass__(cls, domain=None, **kwargs) -> None:
            super().__init_subclass__(**kwargs)

        async def async_set_unique_id(self, unique_id):
            self.unique_id = unique_id

        def _abort_if_unique_id_configured(self) -> None:
            if self.unique_id in self.configured_ids:
                raise AbortFlow("already_configured")

        def async_show_form(self, *, step_id, data_schema=None, errors=None):
            return ConfigFlowResult(
                type="form", step_id=step_id, errors=errors or {}
            )

        def async_create_entry(self, *, title, data):
            return ConfigFlowResult(type="create_entry", title=title, data=data)

    class AbortFlow(Exception):
        pass

    config_entries.ConfigFlow = ConfigFlow
    config_entries.ConfigFlowResult = ConfigFlowResult
    const.CONF_NAME = "name"
    const.CONF_URL = "url"
    const.Platform = Platform
    const.PERCENTAGE = "%"
    vacuum_mod.VacuumEntityFeature = VacuumEntityFeature
    vacuum_mod.VacuumActivity = VacuumActivity
    vacuum_mod.StateVacuumEntity = StateVacuumEntity
    sensor_mod.SensorDeviceClass = SensorDeviceClass
    sensor_mod.SensorStateClass = SensorStateClass
    sensor_mod.SensorEntity = SensorEntity
    camera_mod.Camera = Camera
    image_mod.ImageEntity = ImageEntity
    select_mod.SelectEntity = SelectEntity
    switch_mod.SwitchEntity = SwitchEntity
    entity.DeviceInfo = DeviceInfo
    entity_platform.AddEntitiesCallback = object
    exceptions_mod.HomeAssistantError = HomeAssistantError
    aiohttp_client.async_get_clientsession = MagicMock()
    config_entries.ConfigEntry = object
    core.HomeAssistant = object

    sys.modules["homeassistant"] = homeassistant
    sys.modules["homeassistant.core"] = core
    sys.modules["homeassistant.config_entries"] = config_entries
    sys.modules["homeassistant.const"] = const
    sys.modules["homeassistant.components"] = components
    sys.modules["homeassistant.components.vacuum"] = vacuum_mod
    sys.modules["homeassistant.components.camera"] = camera_mod
    sys.modules["homeassistant.components.image"] = image_mod
    sys.modules["homeassistant.components.select"] = select_mod
    sys.modules["homeassistant.components.switch"] = switch_mod
    sys.modules["homeassistant.components.sensor"] = sensor_mod
    sys.modules["homeassistant.helpers"] = helpers
    sys.modules["homeassistant.helpers.entity"] = entity
    sys.modules["homeassistant.helpers.entity_platform"] = entity_platform
    sys.modules["homeassistant.exceptions"] = exceptions_mod
    sys.modules["homeassistant.helpers.aiohttp_client"] = aiohttp_client


_ensure_homeassistant_stubs()


@pytest.fixture(autouse=True)
def _no_status_read_delay(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the post-command settle delay out of the unit tests."""
    monkeypatch.setattr("lg_hombot.entity.STATUS_READ_DELAY", 0)
