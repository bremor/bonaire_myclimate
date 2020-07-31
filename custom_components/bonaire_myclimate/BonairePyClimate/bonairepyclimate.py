import asyncio
import datetime
import logging
import socket
import xml.etree.ElementTree

from homeassistant.components.climate.const import (
    HVAC_MODE_OFF, HVAC_MODE_HEAT, HVAC_MODE_COOL, HVAC_MODE_FAN_ONLY)
from homeassistant.helpers import aiohttp_client

_LOGGER = logging.getLogger(__name__)
DELETE = "<myclimate><delete>connection</delete></myclimate>"
DISCOVERY = "<myclimate><get>discovery</get><ip>{}</ip><platform>android</platform><version>1.0.0</version></myclimate>"
GETZONEINFO = "<myclimate><get>getzoneinfo</get><zoneList>1,2</zoneList></myclimate>"
INSTALLATION = "<myclimate><get>installation</get></myclimate>"
LOCAL_PORT = 10003
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
        _LOGGER.debug("Server data received: " + message)
        root = xml.etree.ElementTree.fromstring(message)

        # Check if the message is an installation response
        if root.find('response') is not None and root.find('response').text == 'installation':
            #self._available_zones = root.find('zoneList').text
            self._transport.write(GETZONEINFO.encode())

        # Check if the message is a discovery response
        if root.find('response') is not None and root.find('response').text == 'discovery':
            self._parent._discovered = True

        # Check if the message is getzoneinfo response
        if root.find('response') is not None and root.find('response').text == 'getzoneinfo':
            self._parent._states['roomTemp'] = int(root.find('roomTemp').text)
            self._parent._states['mode'] = root.find('mode').text
            self._parent._states['system'] = root.find('system').text
            self._parent._states['setPoint'] = int(root.find('setPoint').text)
            self._parent._states['type'] = root.find('type').text
            self._parent._states['zoneList'] = root.find('zoneList').text
            if root.find('system').text == 'off':
                self._parent._hvac_mode = HVAC_MODE_OFF
            elif root.find('mode').text == 'fan':
                self._parent._hvac_mode = HVAC_MODE_FAN_ONLY
            elif root.find('type').text == 'heat':
                self._parent._hvac_mode = HVAC_MODE_HEAT
            else:
                self._parent._hvac_mode = HVAC_MODE_COOL

    def connection_lost(self, exc):
        _LOGGER.debug("Server connection lost")

class BonairePyClimate():

    def __init__(self, hass, local_ip):
        self._available_zones = None
        self._discovered = False
        self._hass = hass
        self._hvac_mode = None
        self._local_ip = local_ip
        self._queued_commands = {'system': None,
                    'type': None,
                    'zoneList': None,
                    'mode': None,
                    'setPoint': None,}
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

        self._hass.loop.create_task(self.start())

    async def set_temperature(self, temperature):
        _LOGGER.debug("Set the temperature to: " + str(temperature))
        self._queued_commands['setPoint'] = str(temperature)
        self._queueing_timer = 5
        self._hass.loop.create_task(self.queue_commands())
        #message = "<myclimate><post>postzoneinfo</post><system>{}</system><type>{}</type><zoneList>{}</zoneList><mode>{}</mode><setPoint>{}</setPoint></myclimate>".format(self._system, self._type, self._zonelist, self._mode, str(self._target_temperature))
        #self._client_transport.write(message.encode())

    def get_current_temperature(self):
        return self._states['roomTemp']

    def get_hvac_mode(self):
        return self._hvac_mode

    def get_target_temperature(self):
        return self._states['setPoint']

    async def queue_commands(self):

        if not self._queueing_commands:
            self._queueing_commands = True

            while self._queueing_timer > 0:
                self._queueing_timer -= 1
                await asyncio.sleep(1)

            for command, value in self._queued_commands.items():
                if value is not None:
                    _LOGGER.debug("{}: {}".format(command, value))
                
            self._queueing_commands = False

    def get_zone_permutations(self, zoneList):
        #itertools.combinations
        pass
    
    async def start(self):

        self._server_socket = await self._hass.loop.create_server(
            lambda: HandleServer(self),
            self._local_ip, LOCAL_PORT)

        while True:

            attempts = 0

            while not self._discovered:

                cooloff_timer = 0 if attempts < 3 else 60 if attempts < 6 else 300
                if attempts > 0: _LOGGER.debug("Discovery failed, retrying in " + str(cooloff_timer + 5) + "s")
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

            self._server_transport.write(DELETE.encode())
            self._discovered = False

            await asyncio.sleep(3)
