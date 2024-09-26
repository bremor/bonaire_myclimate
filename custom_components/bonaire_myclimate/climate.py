"""Platform for climate integration."""
import voluptuous as vol

from homeassistant.components.climate import (
    ClimateEntity,
)
from homeassistant.const import (
    ATTR_TEMPERATURE, UnitOfTemperature,
)
from .const import (
    DEVICE_MANUFACTURER, DEVICE_MODEL, DEVICE_NAME, DOMAIN,
    MAX_TEMP, MIN_TEMP, PRECISION, TEMPERATURE_STEP,
)

async def async_setup_entry(hass, config_entry, async_add_devices):
    """Add climate for passed config_entry in HA."""
    hub = hass.data[DOMAIN][config_entry.entry_id]

    new_devices = []
    new_devices.append(BonaireMyClimateClimate(hub))

    if new_devices:
        async_add_devices(new_devices)


class BonaireMyClimateClimate(ClimateEntity):
    """Representation of a climate entity."""

    should_poll = False

    def __init__(self, hub):
        """Initialize the climate entity."""
        self._hub = hub

    async def async_added_to_hass(self):
        """Run when this Entity has been added to HA."""
        self._hub.register_callback(self.async_write_ha_state)

    async def async_will_remove_from_hass(self):
        """Entity being removed from hass."""
        self._hub.remove_callback(self.async_write_ha_state)
        await self._hub.async_teardown()

    @property
    def unique_id(self):
        """Return Unique ID string."""
        return DEVICE_NAME

    @property
    def device_info(self):
        """Information about this device."""
        return {
            "identifiers": {(DOMAIN, DEVICE_NAME)},
            "name": DEVICE_NAME,
            "model": DEVICE_MODEL,
            "manufacturer": DEVICE_MANUFACTURER,
        }

    @property
    def available(self) -> bool:
        """Return True if device is available."""
        return self._hub.available

    @property
    def current_temperature(self):
        return self._hub.current_temperature

    @property
    def fan_mode(self):
        """Return current fan mode"""
        return self._hub.fan_mode

    @property
    def fan_modes(self):
        """Return the list of available fan modes."""
        return self._hub.fan_modes

    @property
    def hvac_mode(self):
        """Return current operation ie. heat, cool, idle."""
        return self._hub.hvac_mode

    @property
    def hvac_modes(self):
        """Return the list of available operation modes."""
        return self._hub.hvac_modes

    @property
    def max_temp(self):
        """Return the maximum temperature."""
        return MAX_TEMP

    @property
    def min_temp(self):
        """Return the minimum temperature."""
        return MIN_TEMP

    @property
    def name(self):
        """Return the name of the climate entity."""
        return DEVICE_NAME

    @property
    def precision(self):
        """Return the precision of the system."""
        return PRECISION

    @property
    def preset_mode(self):
        """Return current preset mode"""
        return self._hub.preset_mode

    @property
    def preset_modes(self):
        """Return the list of available preset modes."""
        return self._hub.preset_modes

    @property
    def supported_features(self):
        """Return the list of supported features."""
        return self._hub.supported_features

    @property
    def target_temperature(self):
        """Return the temperature we try to reach."""
        return self._hub.target_temperature

    @property
    def target_temperature_step(self):
        """Return the supported step of target temperature."""
        return TEMPERATURE_STEP

    @property
    def temperature_unit(self):
        """Return the unit of measurement."""
        return UnitOfTemperature.CELSIUS

    async def async_set_fan_mode(self, fan_mode):
        """Set new target fan operation."""
        await self._hub.async_set_fan_mode(fan_mode)

    async def async_set_hvac_mode(self, hvac_mode):
        """Set new target hvac mode."""
        await self._hub.async_set_hvac_mode(hvac_mode)

    async def async_turn_off(self) -> None:
        await self._hub.async_turn_off()

    async def async_set_preset_mode(self, preset_mode):
        """Set new target preset mode."""
        await self._hub.async_set_preset_mode(preset_mode)

    async def async_set_temperature(self, **kwargs):
        """Set new target temperature."""
        temperature = kwargs.get(ATTR_TEMPERATURE)
        temperature = int(temperature)
        await self._hub.async_set_temperature(temperature)