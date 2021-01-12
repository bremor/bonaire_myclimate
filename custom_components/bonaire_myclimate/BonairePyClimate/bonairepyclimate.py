import asyncio
import datetime
import logging
import socket
import xml.etree.ElementTree

from homeassistant.components.climate.const import (
    HVAC_MODE_OFF, HVAC_MODE_HEAT, HVAC_MODE_COOL, HVAC_MODE_FAN_ONLY)
from itertools import combinations

_LOGGER = logging.getLogger(__name__)
COOL_FAN_MODES = ['thermo']
DELETE = "<myclimate><delete>connection</delete></myclimate>"
DISCOVERY = "<myclimate><get>discovery</get><ip>{}</ip><platform>android</platform><version>1.0.0</version></myclimate>"
EVAP_FAN_MODES = ['thermo', 'manual']
EVAP_MAX_TEMP = 8
EVAP_MIN_TEMP = 1
FAN_ONLY_FAN_MODES = ['1', '2', '3', '4', '5', '6', '7', '8']
GETZONEINFO = "<myclimate><get>getzoneinfo</get><zoneList>1,2</zoneList></myclimate>"
HEAT_FAN_MODES = ['econ', 'thermo', 'boost']
INSTALLATION = "<myclimate><get>installation</get></myclimate>"
LOCAL_PORT = 10003
MAX_TEMP = 32
MIN_TEMP = 10
POSTZONEINFO = "<myclimate><post>postzoneinfo</post><system>{system}</system><type>{type}</type><zoneList>{zoneList}</zoneList><mode>{mode}</mode><setPoint>{setPoint}</setPoint></myclimate>"
POSTZONEINFOFAN = "<myclimate><post>postzoneinfo</post><system>{system}</system><type>{type}</type><zoneList>{zoneList}</zoneList><mode>{mode}</mode><fanSpeed>{fanSpeed}</fanSpeed></myclimate>"
UDP_DISCOVERY_PORT = 10001

class HandleUDPBroadcast:
    def __init__(self, message):
        self.message = message

    def connection_made(self, transport):
        _LOGGER.debug("Sending discovery")
        sock = transport.get_extra_info("socket")
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        transport.sendto(self.message.encode())
        transport.close()

    def connection_lost(self, exc):
        pass

class HandleServer(asyncio.Protocol):
    def __init__(self, parent, connection_made_callback, data_received_callback, connection_lost_callback):
        self.connection_made_callback = connection_made_callback
        self.data_received_callback = data_received_callback
        self.connection_lost_callback = connection_lost_callback

    def connection_made(self, transport):
        self.connection_made_callback(transport)

    def data_received(self, data):
        self.data_received_callback(data)

    def connection_lost(self, exc):
        self.connection_lost_callback()

class BonairePyClimate():
    def __init__(self, event_loop, local_ip):
        self._appliances = {}
        self._connected = False
        self._event_loop = event_loop
        self._fan_modes = []
        self._hvac_mode = None
        self._local_ip = local_ip
        self._postzoneinfo_response_ok = False
        self._preset_modes = []
        self._queued_commands = {}
        self._queueing_commands = False
        self._queueing_timer = None
        self._server_transport = None
        self._states = {'system': None,
                   'type': None,
                   'zoneList': None,
                   'mode': None,
                   'fanSpeed': None,
                   'setPoint': None,
                   'roomTemp': None,}
        self._update_callback = None

        self._event_loop.create_task(self.start())

    async def set_hvac_mode(self, hvac_mode):
        self._hvac_mode = hvac_mode

        if hvac_mode == HVAC_MODE_OFF:
            self._queued_commands['system'] = 'off'
        elif hvac_mode == HVAC_MODE_HEAT:
            self._fan_modes = HEAT_FAN_MODES
            self._queued_commands['system'] = 'on'
            self._queued_commands['type'] = 'heat'
            self._queued_commands['mode'] = 'thermo'
        elif hvac_mode == HVAC_MODE_COOL:
            self._fan_modes = COOL_FAN_MODES
            self._queued_commands['system'] = 'on'
            self._queued_commands['mode'] = 'thermo'

            # Check if the system does 'evap' or 'cool' and set type to match
            if self._appliances['evap'] is not None:
                self._queued_commands['type'] = 'evap'
            else:
                self._queued_commands['type'] = 'cool'

        else:
            self._fan_modes = FAN_ONLY_FAN_MODES
            self._queued_commands['system'] = 'on'
            self._queued_commands['type'] = 'cool'
            self._queued_commands['mode'] = 'fan'

        self._update_callback()
        self._event_loop.create_task(self.queue_commands())

    async def set_preset_mode(self, preset_mode):
        await self.set_general('zoneList', preset_mode)

    async def set_fan_mode(self, fan_mode):

        # If the fan_mode is a number, this implies that the HVAC is set to
        # fan_only.
        if fan_mode.isnumeric():
            _LOGGER.debug("fan_mode is numeric, setting fanSpeed to {}".format(fan_mode))
            await self.set_general('fanSpeed', fan_mode)
        else:
            _LOGGER.debug("Setting fan_mode to {}".format(fan_mode))
            await self.set_general('mode', fan_mode)
        
    async def set_temperature(self, temperature):
        await self.set_general('setPoint', str(temperature))

    async def set_general(self, key, value):
        self._states[key] = value
        self._update_callback()
        self._queued_commands[key] = value
        self._event_loop.create_task(self.queue_commands())

    async def turn_zone_off(self, zone):
        if str(zone) in self._states['zoneList'] and len(self._states['zoneList']) > 1:
            zoneList = self._states['zoneList']
            zoneList = zoneList.split(',')
            zoneList.remove(str(zone))
            zoneList = ','.join(zoneList)
            if zoneList in self._preset_modes:
                await self.set_general('zoneList', zoneList)

    async def turn_zone_on(self, zone):
        if str(zone) not in self._states['zoneList']:
            zoneList = "{},{}".format(self._states['zoneList'], zone)
            zoneList = zoneList.split(',')
            zoneList = sorted(zoneList)
            zoneList = ','.join(zoneList)
            if zoneList in self._preset_modes:
                await self.set_general('zoneList', zoneList)

    def get_current_temperature(self):
        return int(self._states['roomTemp']) if self._states['roomTemp'] else None

    def get_hvac_mode(self):
        return self._hvac_mode

    def get_hvac_modes(self):
        hvac_modes = [HVAC_MODE_OFF, HVAC_MODE_FAN_ONLY]
        if self._appliances and self._appliances["heat"] is not None:
            hvac_modes.append(HVAC_MODE_HEAT)
        if self._appliances and (self._appliances["cool"] is not None or self._appliances["evap"] is not None):
            hvac_modes.append(HVAC_MODE_COOL)
        _LOGGER.debug(hvac_modes)
        return hvac_modes

    def get_preset_mode(self):
        return self._states['zoneList']

    def get_max_temp(self):
        return EVAP_MAX_TEMP if self._states['type'] == 'evap' else MAX_TEMP

    def get_min_temp(self):
        return EVAP_MIN_TEMP if self._states['type'] == 'evap' else MIN_TEMP

    def get_preset_modes(self):
        return self._preset_modes

    def get_fan_mode(self):
        return self._states['fanSpeed'] if self._hvac_mode == HVAC_MODE_FAN_ONLY else self._states['mode']

    def get_fan_modes(self):
        return self._fan_modes

    def get_target_temperature(self):
        return int(self._states['setPoint']) if self._states['setPoint'] else None

    async def queue_commands(self):

        self._queueing_timer = 5

        if not self._queueing_commands:
            self._queueing_commands = True

            while self._queueing_timer > 0:
                if self._connected:
                    self._queueing_timer -= 1
                await asyncio.sleep(1)

            # If mode is fan_only, the payload looks a little bit different.
            # Inludes fanSpeed and removes setPoint 
            if self._states["mode"] == "fan" or self._states["mode"] == "manual":
                payload = POSTZONEINFOFAN.format_map(SafeDict(self._queued_commands))
            else:
                payload = POSTZONEINFO.format_map(SafeDict(self._queued_commands))
            payload = payload.format_map(self._states)
            self._queued_commands.clear()
            self._queueing_commands = False

            for attempts in range(3):
                if attempts > 0:
                    _LOGGER.debug("Response not received, resending command: {}".format(payload))
                else:
                    _LOGGER.debug("Sending the command: {}".format(payload))
                self._server_transport.write(payload.encode())
                for wait in range(5):
                    await asyncio.sleep(1)
                    if self._postzoneinfo_response_ok: break
                if self._postzoneinfo_response_ok: break
            else:
                _LOGGER.warning("No response received after 3 attempts, aborting")

            self._postzoneinfo_response_ok = False

    def get_zone_combinations(self, zoneList):

        # Installations without multiple zones will have one 'Common' zone
        if zoneList == 'Common':
            return ['Common']

        zoneList = zoneList.replace(',','')
        preset_modes = []
        for i in range(1, len(zoneList)+1):
            preset_modes.extend(combinations(zoneList, i))

        return list(map(lambda x: ','.join(x), preset_modes))

    async def start(self):

        await self._event_loop.create_server(
            lambda: HandleServer(self, self.server_connection_made_callback, self.server_data_received_callback, self.server_connection_lost_callback),
            self._local_ip, LOCAL_PORT)

        while True:

            attempts = 0

            while not self._connected:

                cooloff_timer = 0 if attempts < 3 else 60 if attempts < 6 else 300
                if attempts > 0: _LOGGER.debug("Discovery failed, retrying in {}s".format(cooloff_timer + 5))
                attempts += 1
                await asyncio.sleep(cooloff_timer)

                # Send the UDP discovery broadcast
                transport, protocol = await self._event_loop.create_datagram_endpoint(
                    lambda: HandleUDPBroadcast(DISCOVERY.format(self._local_ip)),
                    remote_addr=('255.255.255.255', UDP_DISCOVERY_PORT),
                    allow_broadcast=True)

                # Wait for 7 seconds for a response to the discovery broadcast
                await asyncio.sleep(7)

            await asyncio.sleep(220)

            self._connected = False
            self._server_transport.write(DELETE.encode())

            await asyncio.sleep(3)

    def register_update_callback(self, method):
        """Public method to add a callback subscriber."""
        self._update_callback = method

    def server_connection_made_callback(self, transport):
        _LOGGER.debug("Connected to Wifi Module")
        self._server_transport = transport
        transport.write(INSTALLATION.encode())

    def server_data_received_callback(self, data):
        message = data.decode()
        _LOGGER.debug("Server data received: {}".format(message))
        root = xml.etree.ElementTree.fromstring(message)

        # Check if the message is an installation response
        if root.find('response') is not None and root.find('response').text == 'installation':
            for appliance in root.findall('appliance'):
                self._appliances[appliance.find('type').text] = appliance.find('zoneList').text
                if appliance.find('zoneList').text is not None:
                    self._preset_modes = self.get_zone_combinations(appliance.find('zoneList').text)
            _LOGGER.debug(self._appliances)
            self._server_transport.write(GETZONEINFO.encode())

        # Check if the message is a discovery response
        if root.find('response') is not None and root.find('response').text == 'discovery':
            self._connected = True

        # Check if the message is a postzoneinfo response
        if root.find('result') is not None and root.find('result').text == 'ok':
            self._postzoneinfo_response_ok = True

        getzoneinfo = root.find('response') is not None and root.find('response').text == 'getzoneinfo'
        postzoneinfo = root.find('post') is not None and root.find('post').text == 'postzoneinfo'

        # Check if the message is getzoneinfo response or postzoneinfo
        if (getzoneinfo or postzoneinfo) and not self._queueing_commands:
            for state in self._states:
                if root.find(state) is not None:
                    self._states[state] = root.find(state).text
                else:
                    self._states[state] = None
            if root.find('system').text == 'off':
                self._hvac_mode = HVAC_MODE_OFF
            elif root.find('mode').text == 'fan':
                self._hvac_mode = HVAC_MODE_FAN_ONLY
                self._fan_modes = FAN_ONLY_FAN_MODES
            elif root.find('type').text == 'heat':
                self._hvac_mode = HVAC_MODE_HEAT
                self._fan_modes = HEAT_FAN_MODES
            elif root.find('type').text == 'cool':
                self._hvac_mode = HVAC_MODE_COOL
                self._fan_modes = COOL_FAN_MODES
            else:
                self._hvac_mode = HVAC_MODE_COOL
                self._fan_modes = EVAP_FAN_MODES

            self._update_callback()

    def server_connection_lost_callback(self):
        _LOGGER.debug("Server connection lost")
        self._connected = False

class SafeDict(dict):
    def __missing__(self, key):
        return '{' + key + '}'
