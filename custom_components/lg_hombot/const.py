"""Constants for LG Hombot integration."""

from homeassistant.components.vacuum import VacuumEntityFeature, StateVacuumEntity

# hass 2025.1
try:
    from homeassistant.components.vacuum import VacuumActivity
except (ModuleNotFoundError, ImportError):
    class VacuumActivity(StrEnum):
        CLEANING = "cleaning"
        DOCKED = "docked"
        PAUSE = "pause"
        RETURNING = "returning"

DOMAIN = "lg_hombot"

SUPPORTED_SERVICES = (
    VacuumEntityFeature.STATE
    | VacuumEntityFeature.BATTERY
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
