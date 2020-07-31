"""Platform for climate integration."""
import asyncio
import logging
import homeassistant.helpers.config_validation as cv
import voluptuous as vol

from homeassistant.components.climate import (
    ATTR_CURRENT_TEMPERATURE, HVAC_MODE_OFF, PLATFORM_SCHEMA, ClimateEntity,)
from homeassistant.components.climate.const import (
    HVAC_MODE_OFF, HVAC_MODE_HEAT, HVAC_MODE_COOL, HVAC_MODE_FAN_ONLY,
    SUPPORT_TARGET_TEMPERATURE, SUPPORT_PRESET_MODE,)
from homeassistant.const import TEMP_CELSIUS, CONF_NAME, ATTR_TEMPERATURE
from homeassistant.util import get_local_ip
from .BonairePyClimate.bonairepyclimate import BonairePyClimate

_LOGGER = logging.getLogger(__name__)
DEFAULT_NAME = "Bonaire MyClimate"

# Validation of the user's configuration
PLATFORM_SCHEMA = PLATFORM_SCHEMA.extend({
    vol.Optional(CONF_NAME, default=DEFAULT_NAME): cv.string,
})

async def async_setup_platform(hass, config, async_add_entities, discovery_info=None):
    """Set up the climate platform."""

    local_ip = get_local_ip()
    climate = BonairePyClimate(hass, local_ip)
    async_add_entities([BonaireMyClimateClimate(climate, config)])

class BonaireMyClimateClimate(ClimateEntity):
    """Representation of a Bonaire MyClimate Climate."""

    def __init__(self, climate, config):
        """Initialize the climate device."""
        self._climate = climate
        self._config = config

    @property
    def supported_features(self):
        """Return the list of supported features."""
        return SUPPORT_TARGET_TEMPERATURE

    @property
    def name(self):
        """Return the name of the climate device."""
        return self._config[CONF_NAME]

    @property
    def temperature_unit(self):
        """Return the unit of measurement."""
        return TEMP_CELSIUS

    @property
    def current_temperature(self):
        return self._climate.get_current_temperature()

    @property
    def target_temperature(self):
        """Return the temperature we try to reach."""
        return self._climate.get_target_temperature()

    @property
    def target_temperature_step(self):
        """Return the supported step of target temperature."""
        return 1

    @property
    def hvac_mode(self):
        """Return current operation ie. heat, cool, idle."""
        return self._climate.get_hvac_mode()
        
    @property
    def hvac_modes(self):
        """Return the list of available operation modes."""
        return [HVAC_MODE_OFF, HVAC_MODE_HEAT, 
                HVAC_MODE_COOL, HVAC_MODE_FAN_ONLY,]

    @property
    def precision(self):
        """Return the precision of the system."""
        return '1.0'

    async def async_set_temperature(self, **kwargs):
        temperature = kwargs.get(ATTR_TEMPERATURE)
        temperature = int(temperature)
        await self._climate.set_temperature(temperature)

    async def async_update(self):
        pass