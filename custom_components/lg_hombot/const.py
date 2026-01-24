"""Constants for LG Hombot integration."""

from homeassistant.components.vacuum import VacuumEntityFeature

# hass 2025.1
try:
    from homeassistant.components.vacuum import VacuumActivity
except (ModuleNotFoundError, ImportError):
    from enum import StrEnum
    
    class VacuumActivity(StrEnum):
        CLEANING = "cleaning"
        DOCKED = "docked"
        PAUSE = "pause"
        RETURNING = "returning"

DOMAIN = "lg_hombot"

SUPPORTED_SERVICES = (
    VacuumEntityFeature.STATE
    | VacuumEntityFeature.FAN_SPEED
    | VacuumEntityFeature.START
    | VacuumEntityFeature.PAUSE
    | VacuumEntityFeature.RETURN_HOME
    | VacuumEntityFeature.STOP
)

SPEED_NORMAL = "Normal"
SPEED_TURBO = "Turbo"

SUPPORTED_SPEEDS = (
    SPEED_NORMAL,
    SPEED_TURBO
)
