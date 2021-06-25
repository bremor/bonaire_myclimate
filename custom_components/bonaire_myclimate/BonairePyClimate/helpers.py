"""Helpers functions for BonairePyClimate."""
import asyncio
import logging
import socket

from .const import (
    PORT_DISCOVERY,
    PORT_LOCAL,
)

_LOGGER = logging.getLogger(__name__)


async def create_datagram_transport(event_loop):

    # Create the UDP Broadcast client
    transport, protocol = await event_loop.create_datagram_endpoint(
        lambda: HandleUDPBroadcast(),
        remote_addr=('255.255.255.255', PORT_DISCOVERY),
        allow_broadcast=True)
    sock = transport.get_extra_info("socket")
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)

    return transport

async def create_server(event_loop, connection_made,
                        data_received, connection_lost):

    # Create the TCP server
    return await event_loop.create_server(
        lambda: HandleServer(connection_made, data_received, connection_lost),
        port=PORT_LOCAL)

def zone_combinations(zone_string):
    """Returns all combinations of zones given a zone string."""
    if zone_string == None: return None

    zone_list = zone_string.split(",")
    zone_combinations = []
    for bitmask in range(1,2**len(zone_list)):
        zone_combination = [zone for (index, zone) in enumerate(zone_list) if (bitmask & 2**index)]
        zone_combinations.append(",".join(zone_combination))

    return zone_combinations

class HandleUDPBroadcast:
    def connection_made(self, transport):
        pass

    def connection_lost(self, exc):
        pass

class HandleServer(asyncio.Protocol):
    def __init__(self, connection_made, data_received, connection_lost):
        self._connection_made_callback = connection_made
        self._data_received_callback = data_received
        self._connection_lost_callback = connection_lost

    def connection_made(self, transport):
        self._connection_made_callback(transport)

    def data_received(self, data):
        self._data_received_callback(data)

    def connection_lost(self, exc):
        self._connection_lost_callback()