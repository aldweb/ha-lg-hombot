from homeassistant.components.sensor import SensorEntity
from homeassistant.const import PERCENTAGE

async def async_setup_entry(hass, entry, async_add_entities):
  hombot = hass.data["lg_hombot"][entry.entry_id]
  async_add_entities([LGHombotBatterySensor(hombot)], True)


class LGHombotBatterySensor(SensorEntity):
   _attr_device_class = "battery"
   _attr_native_unit_of_measurement = PERCENTAGE

   def __init__(self, hombot):
       self._hombot = hombot
       self._attr_name = f"{hombot.name} Battery"
       self._attr_unique_id = f"{hombot.unique_id}_battery"

   @property
   def native_value(self):
       return self._hombot.state.battery_level
     
