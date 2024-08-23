"""The Bonaire MyClimate integration."""
import asyncio

from homeassistant.components.network import async_get_source_ip
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .BonairePyClimate import hub
from .const import DOMAIN

PLATFORMS = ["climate"]


async def async_setup(hass: HomeAssistant, config: dict):
    """Set up the Bonaire MyClimate component."""
    hass.data.setdefault(DOMAIN, {})
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up the Bonaire MyClimate from a config entry."""
    local_ip = await async_get_source_ip(hass)
    hass.data[DOMAIN][entry.entry_id] = hub.Hub(hass, local_ip)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    async def send_raw_command(call):
        """Handle the service call."""
        command = call.data.get("raw_command")

        await hass.data[DOMAIN][entry.entry_id].async_send_commands(command)

    hass.services.async_register(DOMAIN, "send_raw_command", send_raw_command)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry):
    """Unload a config entry."""
    unload_ok = all(
        await asyncio.gather(
            *[
                hass.config_entries.async_forward_entry_unload(entry, component)
                for component in PLATFORMS
            ]
        )
    )
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)

    return unload_ok
