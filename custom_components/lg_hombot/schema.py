from homeassistant.const import CONF_NAME, CONF_URL

import voluptuous as vol

USER_DATA_SCHEMA = {
    vol.Required(CONF_NAME, default="Hombot"): str,
    vol.Required(CONF_URL, default="http://192.168.0.0:6260/"): str
}
