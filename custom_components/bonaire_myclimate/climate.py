"""Platform for climate integration."""
import asyncio
import logging
import voluptuous as vol

from homeassistant.components.climate import (
    ATTR_CURRENT_TEMPERATURE, HVAC_MODE_OFF, PLATFORM_SCHEMA, ClimateEntity,)
from homeassistant.components.climate.const import (
    HVAC_MODE_OFF, HVAC_MODE_HEAT, HVAC_MODE_COOL, HVAC_MODE_FAN_ONLY,
    SUPPORT_TARGET_TEMPERATURE, SUPPORT_PRESET_MODE, SUPPORT_FAN_MODE)
from homeassistant.helpers import config_validation as cv, entity_platform, service
from homeassistant.const import TEMP_CELSIUS, CONF_NAME, CONF_ZONE, ATTR_TEMPERATURE, ATTR_ENTITY_ID
from homeassistant.util import get_local_ip
from .BonairePyClimate.bonairepyclimate import BonairePyClimate

_LOGGER = logging.getLogger(__name__)
DEFAULT_NAME = "Bonaire MyClimate"
PRECISION = '1.0'
TEMPERATURE_STEP = 1
TURN_ZONE_OFF = "turn_zone_off"
TURN_ZONE_ON = "turn_zone_on"

# Validation of the user's configuration
PLATFORM_SCHEMA = PLATFORM_SCHEMA.extend({
    vol.Optional(CONF_NAME, default=DEFAULT_NAME): cv.string,
})

SERVICE_SCHEMA = {
    vol.Required(ATTR_ENTITY_ID): cv.entity_ids,
    vol.Required(CONF_ZONE): cv.positive_int,
}

async def async_setup_platform(hass, config, async_add_entities, discovery_info=None):
    """Set up the climate platform."""

    platform = entity_platform.current_platform.get()

    platform.async_register_entity_service(
        TURN_ZONE_ON,
        SERVICE_SCHEMA,
        "async_turn_zone_on",
    )

    platform.async_register_entity_service(
        TURN_ZONE_OFF,
        SERVICE_SCHEMA,
        "async_turn_zone_off",
    )
    
    climate = BonairePyClimate(hass.loop, get_local_ip())
    async_add_entities([BonaireMyClimateClimate(climate, config)])

class BonaireMyClimateClimate(ClimateEntity):
    """Representation of a Bonaire MyClimate Climate."""

    def __init__(self, climate, config):
        """Initialize the climate device."""
        self._climate = climate
        self._config = config

    async def async_added_to_hass(self):
        """Register callbacks."""
        self._climate.register_update_callback(self.update_callback)

    @property
    def name(self):
        """Return the name of the climate device."""
        return self._config[CONF_NAME]

    @property
    def current_temperature(self):
        return self._climate.get_current_temperature()

    @property
    def hvac_mode(self):
        """Return current operation ie. heat, cool, idle."""
        return self._climate.get_hvac_mode()

    @property
    def hvac_modes(self):
        """Return the list of available operation modes."""
        return self._climate.get_hvac_modes()

    @property
    def max_temp(self):
        """Return the maximum temperature."""
        return self._climate.get_max_temp()

    @property
    def min_temp(self):
        """Return the minimum temperature."""
        return self._climate.get_min_temp()

    @property
    def precision(self):
        """Return the precision of the system."""
        return PRECISION

    @property
    def preset_mode(self):
        """Return current preset mode"""
        return self._climate.get_preset_mode()

    @property
    def preset_modes(self):
        """Return the list of available preset modes."""
        return self._climate.get_preset_modes()

    @property
    def should_poll(self):
        """Return the polling state."""
        return False

    @property
    def supported_features(self):
        """Return the list of supported features."""
        return SUPPORT_TARGET_TEMPERATURE | SUPPORT_PRESET_MODE | SUPPORT_FAN_MODE

    @property
    def fan_mode(self):
        """Return current fan mode"""
        return self._climate.get_fan_mode()

    @property
    def fan_modes(self):
        """Return the list of available fan modes."""
        return self._climate.get_fan_modes()

    @property
    def target_temperature(self):
        """Return the temperature we try to reach."""
        return self._climate.get_target_temperature()

    @property
    def target_temperature_step(self):
        """Return the supported step of target temperature."""
        return TEMPERATURE_STEP

    @property
    def temperature_unit(self):
        """Return the unit of measurement."""
        return TEMP_CELSIUS

    async def async_set_hvac_mode(self, hvac_mode):
        """Set new target hvac mode."""
        await self._climate.set_hvac_mode(hvac_mode)

    async def async_set_preset_mode(self, preset_mode):
        """Set new target preset mode."""
        await self._climate.set_preset_mode(preset_mode)

    async def async_set_fan_mode(self, fan_mode):
        """Set new target fan operation."""
        await self._climate.set_fan_mode(fan_mode)

    async def async_set_temperature(self, **kwargs):
        """Set new target temperature."""
        temperature = kwargs.get(ATTR_TEMPERATURE)
        temperature = int(temperature)
        await self._climate.set_temperature(temperature)

    async def async_turn_zone_off(self, zone):
        await self._climate.turn_zone_off(zone)
        
    async def async_turn_zone_on(self, zone):
        await self._climate.turn_zone_on(zone)
        
    def update_callback(self):
        """Call update method."""
        self.async_schedule_update_ha_state(False)
