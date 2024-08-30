"""A hub that connects to the Bonaire MyClimate Wi-Fi device."""
import asyncio
import logging
import xml.etree.ElementTree

<<<<<<< HEAD
from homeassistant.components.climate.const import (
    HVAC_MODE_OFF, HVAC_MODE_HEAT, HVAC_MODE_COOL, HVAC_MODE_FAN_ONLY,
    SUPPORT_TARGET_TEMPERATURE, SUPPORT_PRESET_MODE, SUPPORT_FAN_MODE,

)
=======
from homeassistant.components.climate.const import ClimateEntityFeature, HVACMode
>>>>>>> 8a3f5181a32bce7e4ab26100153fd436859bd975

from .helpers import (
    create_datagram_transport, create_server, zone_combinations
)
from .const import (
    FAN_MODES_COOL, FAN_MODES_EVAP, FAN_MODES_FAN_ONLY, FAN_MODES_HEAT,
    XML_DELETE, XML_DISCOVERY, XML_GETZONEINFO, XML_INSTALLATION,
)

_LOGGER = logging.getLogger(__name__)


class Hub:
    """Hub for Bonaire MyClimate."""

    def __init__(self, hass, local_ip):
        """Init hub."""
        self._hass = hass
        self._local_ip = local_ip

        self._appliances = {}
        self._callbacks = set()
        self._connected = False # When there is an open TCP connection
        self._enable_turn_on_off_backwards_compatibility = False
        self._fan_mode_memory_heat = "thermo"
        self._postzoneinfo_response_ok = False
        self._queued_commands = []
        self._ready = False # Ready to send commands
        self._start_task = None
        self._zone_info = {}

        self._server_transport = None
        self._udp_transport = None

        # Properties
        self.available = False
        self.current_temperature = None
        self.fan_mode = None
        self.fan_modes = None
        self.hvac_mode = None
        self.hvac_modes = None
        self.preset_mode = None
        self.preset_modes = None
        self.supported_features = ClimateEntityFeature.TARGET_TEMPERATURE | ClimateEntityFeature.PRESET_MODE | ClimateEntityFeature.FAN_MODE | ClimateEntityFeature.TURN_OFF | ClimateEntityFeature.TURN_ON
        self.target_temperature = None

        self._start_task = self._hass.loop.create_task(self.async_start())

    async def async_teardown(self):
        """Closes all connections and running tasks."""
        # Cancel the main loop
        if self._start_task is not None:
            self._start_task.cancel()

        # Close the UDP client
        self._udp_transport.close()

        # Send the delete request
        if self._server_transport is not None:
            _LOGGER.info("Sending delete")
            _LOGGER.debug(f"Sending: {XML_DELETE}")
            self._server_transport.write(XML_DELETE.encode())

        # Close the TCP listener
        self._tcp_server.close()
        await self._tcp_server.wait_closed()

    async def async_start(self):
        """Connects to the Wi-Fi module."""
        # Create the UDP client
        self._udp_transport = await create_datagram_transport(self._hass.loop)

        # Create the TCP listener
        self._tcp_server = await create_server(self._hass.loop,
                                               self.server_connection_made,
                                               self.server_data_received,
                                               self.server_connection_lost
        )

        while True:

            attempts = 0

            while not self._connected:

                # Attempt discovery up to 5 times
                cooloff_timer = 0 if attempts < 5 else 60 if attempts < 10 else 300

                if 0 < attempts < 5:
                    _LOGGER.info(f"Discovery attempt #{attempts} failed, retrying")

                elif 5 <= attempts:
                    self.available = False
                    await self.publish_updates()
                    _LOGGER.warning(f"Discovery attempt #{attempts} failed, retrying in {cooloff_timer}s")
                    await asyncio.sleep(cooloff_timer)

                attempts += 1

                # Send the UDP discovery broadcast
                xml_discovery = XML_DISCOVERY.format(self._local_ip)
                _LOGGER.info("Sending discovery")
                _LOGGER.debug(f"Sending: {xml_discovery}")
                self._udp_transport.sendto(xml_discovery.encode())

                # Wait for 10 seconds for a response to the discovery broadcast
                await asyncio.sleep(10)

            await asyncio.sleep(220)
            self._connected = False
            self._ready = False

            # Send the delete request
            _LOGGER.info("Sending delete")
            _LOGGER.debug(f"Sending: {XML_DELETE}")
            self._server_transport.write(XML_DELETE.encode())

            await asyncio.sleep(3)

    def register_callback(self, callback):
        """Register callback, called when device changes state."""
        self._callbacks.add(callback)

    def remove_callback(self, callback):
        """Remove previously registered callback."""
        self._callbacks.discard(callback)

    async def publish_updates(self):
        """Schedule call all registered callbacks."""
        for callback in self._callbacks:
            callback()

    def server_connection_made(self, transport):
        _LOGGER.info("Connected to the Bonaire MyClimate Wi-Fi device")
        self._server_transport = transport

    def server_data_received(self, data):
        message = data.decode()
        _LOGGER.info("Server data received")
        _LOGGER.debug(f"Received: {message}")
        root = xml.etree.ElementTree.fromstring(message)

        # Check if the message is a discovery response
        if root.findtext("response") == "discovery":

            self._connected = True

            # Send the installation request
            _LOGGER.info("Sending installation")
            _LOGGER.debug(f"Sending: {XML_INSTALLATION}")
            self._server_transport.write(XML_INSTALLATION.encode())

        # Check if the message is an installation response
        elif root.findtext("response") == "installation":

            # Store the zoneList for each 'appliance' (i.e. heat, cool, evap)
            for appliance in root.findall('appliance'):
                type = appliance.find('type').text
                zoneList = appliance.find('zoneList').text
                self._appliances[type] = zoneList

            # Build hvac_modes property
            self.hvac_modes = [HVACMode.OFF, HVACMode.FAN_ONLY]
            if self._appliances["heat"] is not None:
                self.hvac_modes += [HVACMode.HEAT]
            if (self._appliances["cool"] is not None or
                    self._appliances["evap"] is not None):
                self.hvac_modes += [HVACMode.COOL]

            # Build the preset modes for each type of appliance
            self._preset_modes_heat = zone_combinations(
                                          self._appliances["heat"])
            self._preset_modes_cool = zone_combinations(
                                          self._appliances["cool"])
            self._preset_modes_evap = zone_combinations(
                                          self._appliances["evap"])

            # Send the getzoneinfo request
            _LOGGER.info("Sending getzoneinfo")
            _LOGGER.debug(f"Sending: {XML_GETZONEINFO}")
            self._server_transport.write(XML_GETZONEINFO.encode())

        # Check if the message is a getzoneinfo response or postzoneinfo
        elif (root.findtext("response") == "getzoneinfo" or
                root.findtext("post") == "postzoneinfo"):

            # Save all the hvac zone info
            self._zone_info = {}
            for item in root.iterfind("./"):
                self._zone_info[item.tag] = item.text

            # Process the zone info, build the hvac properties
            if self._zone_info["system"] == "off":
                self.hvac_mode = HVACMode.OFF
            elif self._zone_info["mode"] == "fan":
                self.fan_mode = self._zone_info["fanSpeed"]
                self.fan_modes = FAN_MODES_FAN_ONLY
                self.hvac_mode = HVACMode.FAN_ONLY
                if self._zone_info["type"] == "heat":
                    self.preset_modes = self._preset_modes_heat
                else:
                    self.preset_modes = self._preset_modes_cool
                self.supported_features = ClimateEntityFeature.PRESET_MODE | ClimateEntityFeature.FAN_MODE | ClimateEntityFeature.TURN_OFF | ClimateEntityFeature.TURN_ON
            elif self._zone_info["type"] == "heat":
                self._fan_mode_memory_heat = self._zone_info["mode"]
                self.fan_mode = self._zone_info["mode"]
                self.fan_modes = FAN_MODES_HEAT
                self.hvac_mode = HVACMode.HEAT
                self.preset_modes = self._preset_modes_heat
                self.supported_features = ClimateEntityFeature.TARGET_TEMPERATURE | ClimateEntityFeature.PRESET_MODE | ClimateEntityFeature.FAN_MODE | ClimateEntityFeature.TURN_OFF | ClimateEntityFeature.TURN_ON
                self.target_temperature = int(self._zone_info["setPoint"])
            elif self._zone_info["type"] == "cool":
                self.fan_mode = self._zone_info["mode"]
                self.fan_modes = FAN_MODES_COOL
                self.hvac_mode = HVACMode.COOL
                self.preset_modes = self._preset_modes_cool
                self.supported_features = ClimateEntityFeature.TARGET_TEMPERATURE | ClimateEntityFeature.PRESET_MODE | ClimateEntityFeature.FAN_MODE | ClimateEntityFeature.TURN_OFF | ClimateEntityFeature.TURN_ON
                self.target_temperature = int(self._zone_info["setPoint"])
            elif self._zone_info["type"] == "evap" and self._zone_info["mode"] == "thermo":
                self.fan_mode = self._zone_info["setPoint"]
                self.fan_modes = FAN_MODES_EVAP
                self.hvac_mode = HVAC_MODE_COOL
                self.preset_modes = self._preset_modes_evap
                self.supported_features = SUPPORT_PRESET_MODE | SUPPORT_FAN_MODE
            elif self._zone_info["type"] == "evap":
                self.fan_mode = self._zone_info["fanSpeed"]
                self.fan_modes = FAN_MODES_EVAP
                self.hvac_mode = HVACMode.COOL
                self.preset_modes = self._preset_modes_evap
                self.supported_features = ClimateEntityFeature.PRESET_MODE | ClimateEntityFeature.FAN_MODE | ClimateEntityFeature.TURN_OFF | ClimateEntityFeature.TURN_ON

            self.current_temperature = int(self._zone_info["roomTemp"])
            self.preset_mode = self._zone_info["zoneList"]

            self.available = True
            self._ready = True
            self._hass.loop.create_task(self.publish_updates())


        # Check if the message is postzoneinfo result 'ok'
        elif (root.findtext("response") == "postzoneinfo" and
                root.findtext("result") == "ok"):

            self._postzoneinfo_response_ok = True
            _LOGGER.info("Sending getzoneinfo")
            _LOGGER.debug(f"Sending: {XML_GETZONEINFO}")
            self._server_transport.write(XML_GETZONEINFO.encode())

    def server_connection_lost(self):
        _LOGGER.debug("Server connection lost")
        self._connected = False
        self._ready = False

    async def async_set_fan_mode(self, fan_mode):
        """Set new target fan operation."""
        if self._zone_info["mode"] == "fan":
            command = {"fanSpeed": fan_mode}
        elif self._zone_info["type"] == "evap" and self._zone_info["mode"] == "thermo":
            command = {"setPoint": fan_mode}
            command["mode"] = "thermo"
        elif self._zone_info["type"] == "evap":
            command = {"fanSpeed": fan_mode}
            if int(fan_mode) < 8:
                command["mode"] = "manual"
            elif int(fan_mode) == 8:
                command["mode"] = "boost"
        else:
            command = {"mode": fan_mode}

        await self.async_send_commands(command)

    async def async_set_hvac_mode(self, hvac_mode):
        """Set new target hvac mode."""
        if hvac_mode == HVACMode.OFF:
            commands = {
                "system": "off"
            }
        elif hvac_mode == HVACMode.FAN_ONLY:
            commands = {
                "system": "on",
                "mode": "fan"
            }
        elif hvac_mode == HVACMode.HEAT:
            commands = {
                "system": "on",
                "type": "heat",
                "mode": self._fan_mode_memory_heat
            }
        elif hvac_mode == HVACMode.COOL:
            if self._appliances["cool"] is not None:
                commands = {
                    "system": "on",
                    "type": "cool",
                    "mode": "thermo"
                }
            elif self._appliances["evap"] is not None:
                commands = {
                    "system": "on",
                    "type": "evap"
                }

        await self.async_send_commands(commands)

    async def async_turn_on(self) -> None:
        command = {"system": "on"}
        await self.async_send_commands(command)

    async def async_turn_off(self) -> None:
        await self.async_set_hvac_mode(HVACMode.OFF)

    async def async_set_preset_mode(self, preset_mode):
        """Set new target preset mode."""
        command = {"zoneList": preset_mode}
        await self.async_send_commands(command)

    async def async_set_temperature(self, temperature):
        """Set new target temperature."""
        command = {"setPoint": str(temperature)}
        await self.async_send_commands(command)

    async def async_send_commands(self, command):
        """Send commands to device."""
        _LOGGER.info(command)
        # Append the given command to the end of the queued commands
        self._queued_commands.append(command)

        if len(self._queued_commands) == 1:
        
            while len(self._queued_commands) > 0:

                # Wait until ready to send commands
                while not self._ready:
                    await asyncio.sleep(1)

                command = self._queued_commands[0]

                postzoneinfo = "<myclimate><post>postzoneinfo</post>"
                for key, value in command.items():
                    postzoneinfo += f"<{key}>{value}</{key}>"
                postzoneinfo += "</myclimate>"

                # Attempt to send the command up to 3 times
                for attempts in range(3):
                    if attempts > 0:
                        _LOGGER.info("Response not received, resending postzoneinfo")
                    else:
                        _LOGGER.info("Sending postzoneinfo")
                    _LOGGER.debug(f"Sending: {postzoneinfo}")
                    self._server_transport.write(postzoneinfo.encode())

                    await asyncio.sleep(3)
                    if self._postzoneinfo_response_ok: break
                else:
                    _LOGGER.warning("No postzoneinfo response received after 3 attempts, aborting")

                self._queued_commands.pop(0)
