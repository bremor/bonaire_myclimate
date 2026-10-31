"""Bonaire MyClimate protocol helpers with per-connection XML framing."""

import asyncio
import logging
import socket

from .const import PORT_DISCOVERY, PORT_LOCAL

_LOGGER = logging.getLogger(__name__)
MAX_FRAME_BYTES = 65536
_WHITESPACE = b" \t\r\n"
_OPEN = b"<myclimate>"
_CLOSE = b"</myclimate>"


async def create_datagram_transport(event_loop, trace=None):
    transport, _ = await event_loop.create_datagram_endpoint(
        lambda: HandleUDPBroadcast(trace),
        remote_addr=("255.255.255.255", PORT_DISCOVERY),
        allow_broadcast=True,
    )
    # Each discovery attempt owns a fresh socket. Do not share a stale UDP peer.
    sock = transport.get_extra_info("socket")
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    return transport


async def create_server(
    event_loop, connection_made, data_received, connection_lost, trace=None
):
    return await event_loop.create_server(
        lambda: HandleServer(connection_made, data_received, connection_lost, trace),
        port=PORT_LOCAL,
    )


def zone_combinations(zone_string):
    if not zone_string:
        return None
    zones = [x.strip() for x in zone_string.split(",")]
    if (
        len(zones) > 12
        or any(not x or len(x) > 64 for x in zones)
        or len(set(zones)) != len(zones)
    ):
        raise ValueError("Invalid or excessive zone list")
    return [
        ",".join(z for i, z in enumerate(zones) if mask & (1 << i))
        for mask in range(1, 1 << len(zones))
    ]


def emit(trace, event, **fields):
    if trace:
        try:
            trace(event, **fields)
        except Exception:
            _LOGGER.exception("Bonaire diagnostic callback failed: %s", event)


class HandleUDPBroadcast:
    def __init__(self, trace=None):
        self._trace = trace
        self._transport = None

    def connection_made(self, transport):
        self._transport = transport
        emit(self._trace, "udp_connection_made", transport=transport)

    def error_received(self, exc):
        emit(
            self._trace,
            "udp_error_received",
            error_type=type(exc).__name__,
            error=str(exc),
            errno=getattr(exc, "errno", None),
        )

    def datagram_received(self, data, addr):
        emit(
            self._trace,
            "udp_datagram_received",
            peer=addr,
            payload={"bytes": len(data)},
        )

    def connection_lost(self, exc):
        emit(
            self._trace,
            "udp_connection_lost",
            error_type=type(exc).__name__ if exc else None,
            error=str(exc) if exc else None,
            transport=self._transport,
        )


class HandleServer(asyncio.Protocol):
    """Keep framing and callbacks bound to the transport that produced them."""

    def __init__(self, connection_made, data_received, connection_lost, trace=None):
        self._connection_made_callback = connection_made
        self._data_received_callback = data_received
        self._connection_lost_callback = connection_lost
        self._trace = trace
        self._transport = None
        self._buffer = bytearray()
        self._failed = False

    def connection_made(self, transport):
        self._transport = transport
        emit(self._trace, "tcp_connection_made", transport=transport)
        self._connection_made_callback(transport)
        emit(self._trace, "tcp_connected", transport=transport)

    def _fail(self, reason, error=None):
        if self._failed:
            return
        self._failed = True
        self._buffer.clear()
        emit(
            self._trace,
            "tcp_frame_error",
            reason=reason,
            error_type=type(error).__name__ if error else None,
            transport=self._transport,
        )
        # Invalidate readiness now, rather than waiting for the OS callback.
        self._connection_lost_callback(self._transport)
        self._transport.close()

    def data_received(self, data):
        emit(
            self._trace,
            "tcp_rx",
            transport=self._transport,
            payload={"bytes": len(data)},
        )
        if self._failed:
            return
        # Bound an individual incomplete document, not a burst of valid documents.
        offset = 0
        while offset < len(data):
            take = min(len(data) - offset, MAX_FRAME_BYTES + 1 - len(self._buffer))
            self._buffer.extend(data[offset : offset + take])
            offset += take
            while self._buffer:
                self._buffer = self._buffer.lstrip(_WHITESPACE)
                if not self._buffer:
                    break
                if len(self._buffer) < len(_OPEN) and _OPEN.startswith(self._buffer):
                    break
                if not self._buffer.startswith(_OPEN):
                    self._fail("Invalid XML document prefix")
                    return
                end = self._buffer.find(_CLOSE)
                if end < 0:
                    break
                end += len(_CLOSE)
                if end > MAX_FRAME_BYTES:
                    self._fail("XML document exceeds size limit")
                    return
                frame = bytes(self._buffer[:end])
                del self._buffer[:end]
                try:
                    self._data_received_callback(frame, transport=self._transport)
                except Exception as exc:
                    self._fail("XML handler rejected document", exc)
                    return
                if self._transport.is_closing():
                    self._buffer.clear()
                    return
            if len(self._buffer) > MAX_FRAME_BYTES:
                self._fail("Incomplete XML document exceeds size limit")
                return
        emit(self._trace, "tcp_rx_processed", transport=self._transport)

    def connection_lost(self, exc):
        self._buffer.clear()
        self._failed = True
        emit(
            self._trace,
            "tcp_connection_lost",
            transport=self._transport,
            error_type=type(exc).__name__ if exc else None,
            error=str(exc) if exc else None,
            errno=getattr(exc, "errno", None),
        )
        self._connection_lost_callback(self._transport)
        emit(self._trace, "tcp_lost_callback_done", transport=self._transport)

    def eof_received(self):
        if self._buffer.strip(_WHITESPACE):
            emit(self._trace, "tcp_incomplete_eof", buffered_bytes=len(self._buffer))
        emit(self._trace, "tcp_eof_received", transport=self._transport)

    def pause_writing(self):
        emit(self._trace, "tcp_pause_writing", transport=self._transport)

    def resume_writing(self):
        emit(self._trace, "tcp_resume_writing", transport=self._transport)
