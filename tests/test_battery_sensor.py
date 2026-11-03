"""Battery sensor mapping. The sensor follows the vacuum poll."""

from __future__ import annotations

import pytest

from lg_hombot.sensor import HombotBatterySensor

URL = "http://robot:6260/"


def _sensor() -> HombotBatterySensor:
    return HombotBatterySensor(object(), "Living room", URL)


def test_identity_is_unchanged() -> None:
    sensor = _sensor()
    assert sensor._attr_unique_id == f"hombot_{URL}_battery"
    assert sensor._attr_name == "Battery"
    assert sensor._attr_has_entity_name is True
    assert sensor._attr_should_poll is False
    assert sensor.device_info["identifiers"] == {("lg_hombot", f"hombot_{URL}")}


@pytest.mark.parametrize("level", ["0", "55", "100"])
def test_valid_level(level: str) -> None:
    sensor = _sensor()
    sensor._apply_status({"JSON_BATTPERC": level, "JSON_ROBOT_STATE": "CHARGING"})
    assert sensor.native_value == int(level)
    assert sensor.available is True
    assert sensor._attr_extra_state_attributes == {"charging": True}


@pytest.mark.parametrize("level", [None, "", "abc", "-1", "101", "50.5"])
def test_missing_or_invalid_level_is_unavailable(level) -> None:
    sensor = _sensor()
    sensor._apply_status({"JSON_BATTPERC": "40"})
    status = {} if level is None else {"JSON_BATTPERC": level}
    sensor._apply_status(status)
    assert sensor.native_value is None
    assert sensor.available is False


def test_charging_attribute_follows_the_state() -> None:
    sensor = _sensor()
    sensor._apply_status({"JSON_BATTPERC": "40", "JSON_ROBOT_STATE": "WORKING"})
    assert sensor._attr_extra_state_attributes == {"charging": False}
