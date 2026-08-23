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
    """Handle the MyClimate TCP stream and emit complete XML messages."""

    _MESSAGE_START = b"<myclimate"
    _MESSAGE_END = b"</myclimate>"
    _MAX_BUFFER_SIZE = 64 * 1024

    def __init__(self, connection_made, data_received, connection_lost):
        self._connection_made_callback = connection_made
        self._data_received_callback = data_received
        self._connection_lost_callback = connection_lost
        self._buffer = bytearray()

    def connection_made(self, transport):
        self._connection_made_callback(transport)

    def data_received(self, data):
        self._buffer.extend(data)

        while self._buffer:
            message_start = self._buffer.find(self._MESSAGE_START)

            if message_start == -1:
                if len(self._buffer) > self._MAX_BUFFER_SIZE:
                    _LOGGER.warning(
                        "Discarding %s bytes without a MyClimate XML start tag",
                        len(self._buffer),
                    )
                    self._buffer.clear()
                return

            if message_start:
                _LOGGER.warning(
                    "Discarding %s bytes before a MyClimate XML message",
                    message_start,
                )
                del self._buffer[:message_start]

            message_end = self._buffer.find(self._MESSAGE_END)
            if message_end == -1:
                if len(self._buffer) > self._MAX_BUFFER_SIZE:
                    _LOGGER.warning(
                        "Discarding oversized incomplete MyClimate XML message"
                    )
                    self._buffer.clear()
                return

            message_end += len(self._MESSAGE_END)
            message = bytes(self._buffer[:message_end])
            del self._buffer[:message_end]
            self._data_received_callback(message)

    def connection_lost(self, exc):
        self._connection_lost_callback()
