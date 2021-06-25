"""Config flow for Bonaire MyClimate integration."""
import logging
import voluptuous as vol

from homeassistant import config_entries#, core
from .const import DEVICE_NAME, DOMAIN, CONF_STAY_CONNECTED

_LOGGER = logging.getLogger(__name__)


class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Bonaire MyClimate."""

    VERSION = 1
    CONNECTION_CLASS = config_entries.CONN_CLASS_LOCAL_PUSH

    async def async_step_user(self, user_input=None):
        """Handle the initial step."""
        if self._async_current_entries():
            return self.async_abort(reason="single_instance_allowed")

        if user_input is not None:
            return self.async_create_entry(title=DEVICE_NAME, data=user_input)

        return self.async_show_form(step_id="user")