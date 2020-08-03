import asyncio
import datetime
import logging
import socket
import xml.etree.ElementTree

from homeassistant.components.climate.const import (
    HVAC_MODE_OFF, HVAC_MODE_HEAT, HVAC_MODE_COOL, HVAC_MODE_FAN_ONLY)
from itertools import combinations

_LOGGER = logging.getLogger(__name__)
DELETE = "<myclimate><delete>connection</delete></myclimate>"
DISCOVERY = "<myclimate><get>discovery</get><ip>{}</ip><platform>android</platform><version>1.0.0</version></myclimate>"
GETZONEINFO = "<myclimate><get>getzoneinfo</get><zoneList>1,2</zoneList></myclimate>"
INSTALLATION = "<myclimate><get>installation</get></myclimate>"
LOCAL_PORT = 10003
POSTZONEINFO = "<myclimate><post>postzoneinfo</post><system>{system}</system><type>{type}</type><zoneList>{zoneList}</zoneList><mode>{mode}</mode><setPoint>{setPoint}</setPoint></myclimate>"
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
    def __init__(self, parent):
        self._parent = parent
        self._transport = None

    def connection_made(self, transport):
        _LOGGER.debug("Connected to Wifi Module")
        self._parent._server_transport = transport
        self._transport = transport
        transport.write(INSTALLATION.encode())

    def data_received(self, data):
        message = data.decode()
        _LOGGER.debug("Server data received: {}".format(message))
        root = xml.etree.ElementTree.fromstring(message)

        # Check if the message is an installation response
        if root.find('response') is not None and root.find('response').text == 'installation':
            self._parent._preset_modes = self._parent.get_zone_combinations(root.find('appliance/zoneList').text)
            self._transport.write(GETZONEINFO.encode())

        # Check if the message is a discovery response
        if root.find('response') is not None and root.find('response').text == 'discovery':
            self._parent._connected = True

        getzoneinfo = root.find('response') is not None and root.find('response').text == 'getzoneinfo'
        postzoneinfo = root.find('post') is not None and root.find('post').text == 'postzoneinfo'

        # Check if the message is getzoneinfo response or postzoneinfo
        if (getzoneinfo or postzoneinfo) and not self._parent._queueing_commands:
            for state in self._parent._states:
                self._parent._states[state] = root.find(state).text
            if root.find('system').text == 'off':
                self._parent._hvac_mode = HVAC_MODE_OFF
            elif root.find('mode').text == 'fan':
                self._parent._hvac_mode = HVAC_MODE_FAN_ONLY
            elif root.find('type').text == 'heat':
                self._parent._hvac_mode = HVAC_MODE_HEAT
            else:
                self._parent._hvac_mode = HVAC_MODE_COOL

            self._parent._update_callback()

    def connection_lost(self, exc):
        _LOGGER.debug("Server connection lost")
        self._parent._connected = False

class BonairePyClimate():

    def __init__(self, hass, local_ip):
        self._available_zones = None
        self._connected = False
        self._hass = hass
        self._hvac_mode = None
        self._local_ip = local_ip
        self._preset_modes = []
        self._queued_commands = {}
        self._queueing_commands = False
        self._queueing_timer = None
        self._server_socket = None
        self._server_transport = None
        self._states = {'system': None,
                   'type': None,
                   'zoneList': None,
                   'mode': None,
                   'setPoint': None,
                   'roomTemp': None,}
        self._update_callback = None

        self._hass.loop.create_task(self.start())

    async def set_hvac_mode(self, hvac_mode):
        self._hvac_mode = hvac_mode
        self._update_callback()

        if hvac_mode == HVAC_MODE_OFF:
            self._queued_commands['system'] = 'off'
        elif hvac_mode == HVAC_MODE_HEAT:
            self._queued_commands['system'] = 'on'
            self._queued_commands['type'] = 'heat'
            self._queued_commands['mode'] = 'thermo'
        elif hvac_mode == HVAC_MODE_COOL:
            self._queued_commands['system'] = 'on'
            self._queued_commands['type'] = 'cool'
            self._queued_commands['mode'] = 'thermo'
        else:
            self._queued_commands['system'] = 'on'
            self._queued_commands['type'] = 'cool'
            self._queued_commands['mode'] = 'fan'

        self._hass.loop.create_task(self.queue_commands())

    async def set_preset_mode(self, preset_mode):
        self._states['zoneList'] = preset_mode
        self._update_callback()

        self._queued_commands['zoneList'] = preset_mode
        self._hass.loop.create_task(self.queue_commands())

    async def set_temperature(self, temperature):
        self._queued_commands['setPoint'] = str(temperature)
        self._hass.loop.create_task(self.queue_commands())

    def get_current_temperature(self):
        return int(self._states['roomTemp']) if self._states['roomTemp'] else None

    def get_hvac_mode(self):
        return self._hvac_mode

    def get_preset_mode(self):
        return self._states['zoneList']

    def get_preset_modes(self):
        return self._preset_modes
        
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

            payload = POSTZONEINFO.format_map(SafeDict(self._queued_commands))
            payload = payload.format_map(self._states)
            self._queued_commands.clear()

            _LOGGER.debug("Sending the command: {}".format(payload))
            self._server_transport.write(payload.encode())

            self._queueing_commands = False

    def get_zone_combinations(self, zoneList):
        zoneList = zoneList.replace(',','')
        preset_modes = []
        for i in range(1, len(zoneList)+1):
            preset_modes.extend(combinations(zoneList, i))

        return list(map(lambda x: ','.join(x), preset_modes))

    async def start(self):

        self._server_socket = await self._hass.loop.create_server(
            lambda: HandleServer(self),
            self._local_ip, LOCAL_PORT)

        while True:

            attempts = 0

            while not self._connected:

                cooloff_timer = 0 if attempts < 3 else 60 if attempts < 6 else 300
                if attempts > 0: _LOGGER.debug("Discovery failed, retrying in {}s".format(cooloff_timer + 5))
                attempts += 1
                await asyncio.sleep(cooloff_timer)

                # Send the UDP discovery broadcast
                transport, protocol = await self._hass.loop.create_datagram_endpoint(
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

class SafeDict(dict):
    def __missing__(self, key):
        return '{' + key + '}'
