"""Constants for LG Hombot integration."""

from homeassistant.components.vacuum import VacuumEntityFeature


DOMAIN = "lg_hombot"


SUPPORTED_SERVICES = (
    VacuumEntityFeature.STATUS
    | VacuumEntityFeature.BATTERY
    | VacuumEntityFeature.TURN_ON
    | VacuumEntityFeature.TURN_OFF
)
