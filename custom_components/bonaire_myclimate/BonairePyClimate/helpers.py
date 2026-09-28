"""Helpers functions for BonairePyClimate."""
import asyncio
import logging

from .const import (
    PORT_DISCOVERY,
    PORT_LOCAL,
    XML_DISCOVERY,
)

_LOGGER = logging.getLogger(__name__)


async def async_send_udp_discovery(local_ip: str):
    """Send the UDP discovery payload"""
    # Create an XML payload with the IP address of this integration.
    # Sending the UDP broadcast will cause the Bonaire MyClimate device
    # to establish the connection to this IP address.
    xml_discovery = XML_DISCOVERY.format(local_ip)
    _LOGGER.info("Sending discovery")
    _LOGGER.debug(f"Sending: {xml_discovery}")

    loop = asyncio.get_running_loop()

    transport, protocol = await loop.create_datagram_endpoint(
        lambda: asyncio.DatagramProtocol(),
        remote_addr=("255.255.255.255", PORT_DISCOVERY),
        allow_broadcast=True,
    )

    transport.sendto(xml_discovery.encode())
    transport.close()


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