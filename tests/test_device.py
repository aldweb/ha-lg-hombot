"""URL handling and device info shared by every HomBot entity."""

from __future__ import annotations

import pytest

from lg_hombot.device import (
    hombot_device_info,
    normalize_url,
    url_unique_id,
    validate_url,
)


@pytest.mark.parametrize(
    ("given", "expected"),
    [
        ("http://192.168.1.5:6260", "http://192.168.1.5:6260/"),
        ("http://192.168.1.5:6260/", "http://192.168.1.5:6260/"),
        ("  http://robot.local:6260  ", "http://robot.local:6260/"),
    ],
)
def test_normalize_url_adds_the_final_slash(given: str, expected: str) -> None:
    assert normalize_url(given) == expected
    assert normalize_url(expected) == expected


@pytest.mark.parametrize(
    "bad",
    ["", "robot", "ftp://192.168.1.5/", "http://", "http://host:notaport/", "//x/"],
)
def test_validate_url_rejects_unusable_urls(bad: str) -> None:
    with pytest.raises(ValueError):
        validate_url(bad)


def test_validate_url_returns_the_normalized_url() -> None:
    assert validate_url("http://192.168.1.5:6260") == "http://192.168.1.5:6260/"


def test_unique_id_ignores_slash_and_case() -> None:
    assert url_unique_id("http://Robot.local:6260") == "robot.local:6260"
    assert url_unique_id("http://robot.local:6260/") == "robot.local:6260"
    assert url_unique_id("http://robot.local/") == "robot.local"


def test_device_info_is_the_same_device_for_every_entity() -> None:
    info = hombot_device_info("Living room", "http://r:6260/")
    assert info["identifiers"] == {("lg_hombot", "hombot_http://r:6260/")}
    assert info["manufacturer"] == "LG"
    assert info["model"] == "Hombot"
    assert info["configuration_url"] == "http://r:6260/"
