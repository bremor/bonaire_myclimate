"""Offline protocol, recovery, command, lifecycle and real-loopback tests."""

import asyncio
import ast
import enum
import importlib.util
import pathlib
import sys
import types
import unittest
import xml.etree.ElementTree as ET
from dataclasses import replace

ROOT = (
    pathlib.Path(__file__).resolve().parents[1] / "custom_components/bonaire_myclimate"
)
for name in (
    "homeassistant",
    "homeassistant.components",
    "homeassistant.components.climate",
    "homeassistant.components.climate.const",
    "homeassistant.exceptions",
    "repair",
):
    mod = types.ModuleType(name)
    mod.__path__ = []
    sys.modules[name] = mod
c = sys.modules["homeassistant.components.climate.const"]
c.ClimateEntityFeature = enum.IntFlag(
    "ClimateEntityFeature",
    {
        "TARGET_TEMPERATURE": 1,
        "PRESET_MODE": 2,
        "FAN_MODE": 4,
        "TURN_OFF": 8,
        "TURN_ON": 16,
    },
)
c.HVACMode = enum.Enum(
    "HVACMode", {x: x.lower() for x in ("OFF", "FAN_ONLY", "HEAT", "COOL")}, type=str
)


class HAError(Exception):
    pass


sys.modules["homeassistant.exceptions"].HomeAssistantError = HAError
for name in ("const", "helpers", "hub"):
    spec = importlib.util.spec_from_file_location(
        "repair." + name, ROOT / "BonairePyClimate" / (name + ".py")
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
hub = sys.modules["repair.hub"]
helpers = sys.modules["repair.helpers"]
D = b"<myclimate><response>discovery</response><ip>192.0.2.10</ip></myclimate>"
I = b"<myclimate><response>installation</response><appliance><type>heat</type><zoneList></zoneList></appliance><appliance><type>cool</type><zoneList></zoneList></appliance><appliance><type>evap</type><zoneList>Common</zoneList></appliance></myclimate>"
ACK = b"<myclimate><response>postzoneinfo</response><result>ok</result></myclimate>"


def zone(
    system="off", speed="4", mode="manual", kind="evap", temperature="22", post=False
):
    tag = "<post>postzoneinfo</post>" if post else "<response>getzoneinfo</response>"
    return (
        "<myclimate>" + tag + "<system>" + system + "</system><type>" + kind + "</type>"
        "<mode>"
        + mode
        + "</mode><fanSpeed>"
        + speed
        + "</fanSpeed><roomTemp>"
        + temperature
        + "</roomTemp><zoneList>Common</zoneList></myclimate>"
    ).encode()


Z = zone()
FAST = hub.Timings(
    discovery=0.03,
    phase=0.012,
    session=0.5,
    settle=0.001,
    command=0.24,
    acknowledgement=0.018,
    verification=0.012,
)


class Transport:
    def __init__(self, loop=None):
        self.writes = []
        self.closing = False
        self.protocol = None
        self.handler = None
        self.loop = loop
        self.raise_write = False

    def write(self, data):
        if self.closing or self.raise_write:
            raise OSError("closed test transport")
        self.writes.append(data)
        if self.handler:
            self.handler(data)

    def close(self):
        if not self.closing:
            self.closing = True
            if self.protocol and self.loop:
                self.loop.call_soon(self.protocol.connection_lost, None)

    def is_closing(self):
        return self.closing

    def get_extra_info(self, name):
        return {
            "peername": ("192.0.2.10", 12345),
            "sockname": ("192.0.2.20", 10003),
        }.get(name)

    def get_write_buffer_size(self):
        return 0


class Server:
    sockets = []

    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True

    async def wait_closed(self):
        pass


class Network:
    def __init__(self, h, *, miss_discovery=0, miss_installation=0, miss_state=0):
        self.h = h
        self.loop = h._hass.loop
        self.udp = []
        self.transports = []
        self.discovery = self.installation = self.states = 0
        self.miss_discovery = miss_discovery
        self.miss_installation = miss_installation
        self.miss_state = miss_state
        self.apply = True
        self.ack = True
        self.reply = True
        self.confirm = True
        self.system = "off"
        self.speed = "4"
        self.mode = "manual"
        self.delay_ack = 0
        self.delay_state = 0
        self.server = Server()

    def frame(self, wire, transport=None):
        t = transport or self.transports[-1]
        if not t.closing:
            t.protocol.data_received(wire)

    def connect(self):
        t = Transport(self.loop)
        p = helpers.HandleServer(
            self.h.server_connection_made,
            self.h.server_data_received,
            self.h.server_connection_lost,
            self.h._trace,
        )
        t.protocol = p
        t.handler = lambda data: self.handle(t, data)
        self.transports.append(t)
        p.connection_made(t)
        self.frame(D, t)
        return t

    def later(self, delay, wire, t):
        self.loop.call_later(delay, self.frame, wire, t)

    def handle(self, t, data):
        root = ET.fromstring(data)
        query = root.findtext("get")
        if query == "installation":
            self.installation += 1
            if self.installation > self.miss_installation:
                self.later(0, I, t)
        elif query == "getzoneinfo":
            self.states += 1
            if self.reply and self.states > self.miss_state:
                wire = zone(
                    self.system if self.confirm else "off", self.speed, self.mode
                )
                self.later(self.delay_state, wire, t)
        elif root.findtext("post") == "postzoneinfo":
            if self.apply:
                self.system = root.findtext("system") or self.system
                self.speed = root.findtext("fanSpeed") or self.speed
                self.mode = root.findtext("mode") or self.mode
            if self.ack:
                self.later(self.delay_ack, ACK, t)

    async def datagram(self, *args, **kwargs):
        network = self

        class UDP:
            closed = False

            def sendto(self, data):
                network.discovery += 1
                if network.discovery > network.miss_discovery:
                    network.loop.call_soon(network.connect)

            def close(self):
                self.closed = True

        udp = UDP()
        self.udp.append(udp)
        return udp

    async def listener(self, *args, **kwargs):
        return self.server


class Tests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.hubs = []
        self.original_factories = hub.create_datagram_transport, hub.create_server

    async def asyncTearDown(self):
        for h in self.hubs:
            await h.async_teardown()
        hub.create_datagram_transport, hub.create_server = self.original_factories
        await asyncio.sleep(0)

    def make(self, timings=FAST):
        h = hub.Hub(
            types.SimpleNamespace(loop=asyncio.get_running_loop()),
            "192.0.2.20",
            timings=timings,
            start=False,
        )
        self.hubs.append(h)
        return h

    def ready(self, h):
        n = Network(h)
        t = n.connect()
        n.frame(I)
        n.frame(Z)
        return n, t

    async def until(self, predicate, timeout=0.5):
        async with asyncio.timeout(timeout):
            while not predicate():
                await asyncio.sleep(0.001)

    async def run_network(self, **kwargs):
        h = self.make()
        n = Network(h, **kwargs)
        hub.create_datagram_transport, hub.create_server = n.datagram, n.listener
        h._start_task = asyncio.create_task(h.async_start())
        await self.until(lambda: h._ready)
        return h, n

    async def test_normal_handshake(self):
        h, n = await self.run_network()
        self.assertTrue(h.available)
        self.assertEqual(h.current_temperature, 22)
        self.assertEqual((n.installation, n.states), (1, 1))
        self.assertTrue(all(x.closed for x in n.udp))

    async def test_lost_discovery_uses_fresh_socket(self):
        h, n = await self.run_network(miss_discovery=1)
        self.assertEqual(n.discovery, 2)
        self.assertEqual(len(n.udp), 2)
        self.assertIsNot(n.udp[0], n.udp[1])
        self.assertTrue(all(x.closed for x in n.udp))

    async def test_lost_installation_retried(self):
        h, n = await self.run_network(miss_installation=1)
        self.assertEqual(n.installation, 2)
        self.assertEqual(n.discovery, 1)

    async def test_lost_state_retried(self):
        h, n = await self.run_network(miss_state=2)
        self.assertEqual(n.states, 3)
        self.assertEqual(n.discovery, 1)

    async def test_both_phases_can_retry_without_reconnect(self):
        h, n = await self.run_network(miss_installation=2, miss_state=2)
        self.assertEqual((n.installation, n.states, n.discovery), (3, 3, 1))

    async def test_exhausted_phase_reconnects_and_recovers(self):
        h, n = await self.run_network(miss_installation=3)
        self.assertGreaterEqual(n.discovery, 2)
        self.assertTrue(n.transports[0].closing)

    async def test_unexpected_disconnect_wakes_immediately(self):
        h, n = await self.run_network()
        old = n.transports[-1]
        old.close()
        await self.until(lambda: len(n.transports) >= 2 and h._ready)
        self.assertLess(n.discovery, 4)
        self.assertTrue(h.available)

    async def test_scheduled_renewal_and_fresh_ready(self):
        h = self.make(replace(FAST, session=0.025))
        n = Network(h)
        hub.create_datagram_transport, hub.create_server = n.datagram, n.listener
        h._start_task = asyncio.create_task(h.async_start())
        await self.until(lambda: n.discovery >= 2 and h._ready)
        self.assertTrue(
            any(b"<delete>connection</delete>" in w for w in n.transports[0].writes)
        )

    async def test_failed_discovery_is_bounded_and_teardown_interrupts_backoff(self):
        h = self.make()
        n = Network(h, miss_discovery=999)
        hub.create_datagram_transport, hub.create_server = n.datagram, n.listener
        h._start_task = asyncio.create_task(h.async_start())
        await self.until(lambda: n.discovery >= 2)
        await h.async_teardown()
        self.assertTrue(h._start_task.done())
        self.assertFalse(h.available)
        self.assertTrue(n.server.closed)
        self.assertTrue(all(x.closed for x in n.udp))

    async def test_listener_failure_retries(self):
        h = self.make()
        n = Network(h)
        calls = 0

        async def listener(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise OSError("address busy")
            return n.server

        hub.create_datagram_transport, hub.create_server = n.datagram, listener
        h._start_task = asyncio.create_task(h.async_start())
        await self.until(lambda: h._ready)
        self.assertEqual(calls, 2)

    async def test_early_push_waits_for_current_installation(self):
        h = self.make()
        n = Network(h)
        n.connect()
        n.frame(zone(post=True))
        self.assertFalse(h._ready)
        n.frame(I)
        self.assertTrue(h._ready)

    async def test_previous_installation_cannot_authorize_new_session(self):
        h = self.make()
        n, old = self.ready(h)
        n.connect()
        n.frame(zone(post=True))
        self.assertFalse(h._ready)
        n.frame(I)
        self.assertTrue(h._ready)

    async def test_stale_loss_callback_does_not_clear_new_session(self):
        h = self.make()
        n, old = self.ready(h)
        new = n.connect()
        n.frame(I)
        n.frame(Z)
        old.protocol.connection_lost(None)
        self.assertTrue(h._ready)
        self.assertIs(h._server_transport, new)

    async def test_stale_data_cannot_change_state(self):
        h = self.make()
        n, old = self.ready(h)
        new = n.connect()
        n.frame(I)
        n.frame(Z)
        h.server_data_received(zone("on"), transport=old)
        self.assertEqual(h._zone_info["system"], "off")
        self.assertTrue(h._ready)

    async def test_duplicate_connection_closes_old_transport(self):
        h = self.make()
        n, old = self.ready(h)
        n.connect()
        self.assertTrue(old.closing)
        self.assertFalse(h._ready)
        self.assertTrue(h.available)

    async def test_framing_all_split_positions(self):
        h = self.make()
        for wire in (D, I, Z, zone(temperature="22.5")):
            for cut in range(1, len(wire)):
                frames = []
                t = Transport()
                p = helpers.HandleServer(
                    lambda t: None, lambda f, **kw: frames.append(f), lambda t: None
                )
                p.connection_made(t)
                p.data_received(wire[:cut])
                p.data_received(wire[cut:])
                self.assertEqual(frames, [wire], (cut, wire))
                self.assertFalse(t.closing)

    async def test_coalescing_whitespace_and_partial_tail(self):
        frames = []
        t = Transport()
        p = helpers.HandleServer(
            lambda t: None, lambda f, **kw: frames.append(f), lambda t: None
        )
        p.connection_made(t)
        p.data_received(b" \r\n" + D + b"\n" + I + Z[:17])
        p.data_received(Z[17:])
        self.assertEqual(frames, [D, I, Z])
        self.assertFalse(t.closing)

    async def test_utf8_fragmentation(self):
        wire = "<myclimate><name>Кухня</name></myclimate>".encode()
        for cut in range(1, len(wire)):
            frames = []
            t = Transport()
            p = helpers.HandleServer(
                lambda t: None, lambda f, **kw: frames.append(f), lambda t: None
            )
            p.connection_made(t)
            p.data_received(wire[:cut])
            p.data_received(wire[cut:])
            self.assertEqual(frames, [wire])

    async def test_large_burst_of_small_valid_frames(self):
        frames = []
        t = Transport()
        p = helpers.HandleServer(
            lambda t: None, lambda f, **kw: frames.append(f), lambda t: None
        )
        p.connection_made(t)
        p.data_received(D * 1400)
        self.assertEqual(len(frames), 1400)
        self.assertFalse(t.closing)

    async def test_malformed_and_oversized_frames_fail_closed(self):
        for wire in (
            b"garbage",
            b"<myclimate><x></myclimate>",
            b"<myclimate>" + b"x" * helpers.MAX_FRAME_BYTES,
            b"<myclimate><!DOCTYPE x></myclimate>",
        ):
            h = self.make()
            n = Network(h)
            t = n.connect()
            t.protocol.data_received(wire)
            self.assertTrue(t.closing, wire[:50])
            self.assertFalse(h.available)

    async def test_invalid_state_does_not_refresh_ready(self):
        for wire in (
            zone(temperature="NaN"),
            zone(temperature="999"),
            zone("on", speed="99"),
            zone("on", kind="invalid"),
            Z.replace(
                b"<system>off</system>", b"<system>off</system><system>on</system>"
            ),
        ):
            h = self.make()
            n = Network(h)
            t = n.connect()
            n.frame(I)
            baseline = h._state_sequence
            t.protocol.data_received(wire)
            self.assertFalse(h._ready)
            self.assertEqual(h._state_sequence, baseline)

    async def test_installation_validation(self):
        for zones in ("", ",".join(str(i) for i in range(13)), "Common,Common"):
            h = self.make()
            n = Network(h)
            t = n.connect()
            t.protocol.data_received(
                I.replace(
                    b"<zoneList>Common</zoneList>",
                    ("<zoneList>" + zones + "</zoneList>").encode(),
                )
            )
            self.assertTrue(t.closing)

    async def test_zone_resource_bounds(self):
        self.assertEqual(helpers.zone_combinations("A,B"), ["A", "B", "A,B"])
        for value in ("A,A", ",A", "A,", "x" * 65, ",".join(str(i) for i in range(13))):
            with self.assertRaises(ValueError):
                helpers.zone_combinations(value)

    async def test_confirmed_off_requires_fresh_state(self):
        h = self.make()
        n, t = self.ready(h)
        before = h._state_sequence
        await h.async_send_commands({"system": "off"})
        self.assertGreater(h._state_sequence, before)
        self.assertFalse(h._queued_commands)

    async def test_stale_ack_does_not_suppress_retries(self):
        h = self.make()
        n, t = self.ready(h)
        n.ack = False
        n.reply = False
        n.apply = False
        h._postzoneinfo_response_ok = True
        with self.assertRaises(HAError):
            await h.async_send_commands({"system": "on"})
        posts = [w for w in t.writes if b"<post>postzoneinfo</post>" in w]
        self.assertEqual(len(posts), 3)
        self.assertFalse(h._queued_commands)

    async def test_ack_without_requested_state_fails(self):
        h = self.make()
        n, t = self.ready(h)
        n.apply = False
        with self.assertRaises(HAError):
            await h.async_send_commands({"system": "on"})
        self.assertEqual(len([w for w in t.writes if b"<post>" in w]), 1)

    async def test_lost_ack_but_verified_state_succeeds(self):
        h = self.make()
        n, t = self.ready(h)
        n.ack = False
        await h.async_send_commands({"system": "on"})
        self.assertEqual(h._zone_info["system"], "on")

    async def test_unsolicited_matching_state_can_confirm_command(self):
        h = self.make()
        n, t = self.ready(h)
        n.ack = False
        n.reply = False
        task = asyncio.create_task(h.async_send_commands({"system": "on"}))
        await self.until(lambda: h._active_command is not None)
        n.frame(zone("on", post=True))
        await task

    async def test_old_matching_state_does_not_count_as_confirmation(self):
        h = self.make()
        n, t = self.ready(h)
        n.reply = False
        with self.assertRaises(HAError):
            await h.async_send_commands({"system": "off"})

    async def test_delayed_ack_and_delayed_state(self):
        h = self.make()
        n, t = self.ready(h)
        n.delay_ack = 0.008
        n.delay_state = 0.007
        await h.async_send_commands({"system": "on"})
        self.assertEqual(h._zone_info["system"], "on")

    async def test_readiness_deadline_removes_pending_command(self):
        h = self.make(replace(FAST, command=0.025))
        with self.assertRaises(HAError):
            await h.async_send_commands({"system": "off"})
        self.assertFalse(h._queued_commands)

    async def test_command_cancellation_removes_queue_and_active(self):
        h = self.make()
        n, t = self.ready(h)
        n.ack = False
        n.reply = False
        task = asyncio.create_task(h.async_send_commands({"system": "on"}))
        await self.until(lambda: h._active_command is not None)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertFalse(h._queued_commands)
        self.assertIsNone(h._active_command)

    async def test_session_loss_fails_inflight_without_replay(self):
        h = self.make()
        n, t = self.ready(h)
        n.ack = False
        n.reply = False
        task = asyncio.create_task(h.async_send_commands({"system": "on"}))
        await self.until(lambda: h._active_command is not None)
        t.close()
        with self.assertRaises(HAError):
            await task
        new = n.connect()
        n.frame(I)
        n.frame(Z)
        await asyncio.sleep(0.005)
        self.assertFalse(any(b"<post>" in w for w in new.writes))

    async def test_write_failure_invalidates_readiness(self):
        h = self.make()
        n, t = self.ready(h)
        t.raise_write = True
        with self.assertRaises(HAError):
            await h.async_send_commands({"system": "off"})
        self.assertFalse(h._ready)
        self.assertTrue(t.closing)

    async def test_off_preempts_older_on_requests(self):
        h = self.make()
        n, t = self.ready(h)
        n.ack = False
        n.reply = False
        a = asyncio.create_task(h.async_send_commands({"system": "on"}))
        await self.until(lambda: h._active_command is not None)
        b = asyncio.create_task(h.async_send_commands({"fanSpeed": "5"}))
        await asyncio.sleep(0)
        n.ack = True
        n.reply = True
        stop = asyncio.create_task(h.async_send_commands({"system": "off"}))
        results = await asyncio.gather(a, b, stop, return_exceptions=True)
        self.assertIsInstance(results[0], HAError)
        self.assertIsInstance(results[1], HAError)
        self.assertIsNone(results[2])
        self.assertEqual(h._zone_info["system"], "off")
        self.assertFalse(h._queued_commands)

    async def test_concurrent_commands_serialized(self):
        h = self.make()
        n, t = self.ready(h)
        await asyncio.gather(
            h.async_send_commands({"fanSpeed": "5"}),
            h.async_send_commands({"fanSpeed": "6"}),
        )
        self.assertEqual(h._zone_info["fanSpeed"], "6")
        self.assertFalse(h._queued_commands)

    async def test_bad_commands_never_write(self):
        h = self.make()
        n, t = self.ready(h)
        before = len(t.writes)
        for command in (
            None,
            {},
            [],
            {"system": "bogus"},
            {"type": "invalid"},
            {"fanSpeed": "99"},
            {"mode": "bogus"},
            {"setPoint": "NaN"},
            {"setPoint": 1000},
            {"system": True},
            {"x><system": "on"},
            {"zoneList": "A,A"},
            {"zoneList": ""},
            {"system": "on", "type": "heat"},
        ):
            with self.assertRaises(HAError, msg=str(command)):
                await h.async_send_commands(command)
        self.assertEqual(len(t.writes), before)

    async def test_boost_state_alias(self):
        h = self.make()
        n, t = self.ready(h)
        n.frame(zone("on", "8", "boost"))
        self.assertTrue(
            h._matches(
                {"system": "on", "type": "evap", "mode": "manual", "fanSpeed": "8"},
                h._state_sequence - 1,
            )
        )

    async def test_unmatched_ack_ignored(self):
        h = self.make()
        n, t = self.ready(h)
        n.frame(ACK)
        self.assertFalse(h._postzoneinfo_response_ok)

    async def test_teardown_idempotent_and_pending_command_fails(self):
        h = self.make()
        task = asyncio.create_task(h.async_send_commands({"system": "off"}))
        await asyncio.sleep(0)
        await asyncio.gather(h.async_teardown(), h.async_teardown())
        with self.assertRaises(HAError):
            await task
        self.assertTrue(h._closing)
        self.assertFalse(h._queued_commands)

    async def test_callback_failure_does_not_block_others(self):
        h = self.make()
        calls = []

        def bad():
            raise ValueError("expected callback fixture failure")

        async def good():
            calls.append(True)

        h.register_callback(bad)
        h.register_callback(good)
        await h.publish_updates()
        self.assertEqual(calls, [True])

    async def test_real_loopback_stream_and_disconnect(self):
        h = self.make()
        loop = asyncio.get_running_loop()
        errors = []
        previous = loop.get_exception_handler()
        loop.set_exception_handler(lambda lp, ctx: errors.append(ctx))
        server = await loop.create_server(
            lambda: helpers.HandleServer(
                h.server_connection_made,
                h.server_data_received,
                h.server_connection_lost,
                h._trace,
            ),
            "127.0.0.1",
            0,
        )
        reader, writer = await asyncio.open_connection(
            "127.0.0.1", server.sockets[0].getsockname()[1]
        )
        try:
            writer.write(D[:12])
            await writer.drain()
            await asyncio.sleep(0.001)
            writer.write(D[12:] + b"\n" + I + Z)
            await writer.drain()
            await self.until(lambda: h._ready)
            self.assertTrue(h.available)
            writer.close()
            await writer.wait_closed()
            await self.until(lambda: not h._ready)
            self.assertFalse(errors)
        finally:
            writer.close()
            await writer.wait_closed()
            server.close()
            await server.wait_closed()
            loop.set_exception_handler(previous)

    async def test_real_loopback_malformed_xml_closes_without_event_loop_exception(
        self,
    ):
        h = self.make()
        loop = asyncio.get_running_loop()
        errors = []
        previous = loop.get_exception_handler()
        loop.set_exception_handler(lambda lp, ctx: errors.append(ctx))
        server = await loop.create_server(
            lambda: helpers.HandleServer(
                h.server_connection_made,
                h.server_data_received,
                h.server_connection_lost,
                h._trace,
            ),
            "127.0.0.1",
            0,
        )
        reader, writer = await asyncio.open_connection(
            "127.0.0.1", server.sockets[0].getsockname()[1]
        )
        try:
            writer.write(b"<myclimate><bad></myclimate>")
            await writer.drain()
            self.assertEqual(await asyncio.wait_for(reader.read(), 0.5), b"")
            self.assertFalse(errors)
        finally:
            writer.close()
            await writer.wait_closed()
            server.close()
            await server.wait_closed()
            loop.set_exception_handler(previous)

    async def test_queued_command_from_old_session_is_not_replayed(self):
        h = self.make()
        n, old = self.ready(h)
        n.ack = False
        n.reply = False
        first = asyncio.create_task(h.async_send_commands({"system": "on"}))
        await self.until(lambda: h._active_command is not None)
        queued = asyncio.create_task(h.async_send_commands({"fanSpeed": "5"}))
        await self.until(lambda: len(h._queued_commands) == 2)
        new = n.connect()
        n.frame(I)
        n.frame(Z)
        n.ack = True
        n.reply = True
        results = await asyncio.gather(first, queued, return_exceptions=True)
        self.assertTrue(all(isinstance(x, HAError) for x in results))
        self.assertFalse(any(b"<post>" in w for w in new.writes))

    async def test_cancelled_queued_command_does_not_clear_active_command(self):
        h = self.make()
        n, t = self.ready(h)
        n.ack = False
        n.reply = False
        active = asyncio.create_task(h.async_send_commands({"system": "on"}))
        await self.until(lambda: h._active_command is not None)
        original = h._active_command
        queued = asyncio.create_task(h.async_send_commands({"fanSpeed": "5"}))
        await self.until(lambda: len(h._queued_commands) == 2)
        queued.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await queued
        self.assertIs(h._active_command, original)
        n.frame(zone("on", post=True))
        await active

    async def test_command_after_deadline_still_succeeds(self):
        h = self.make(replace(FAST, command=0.025))
        with self.assertRaises(HAError):
            await h.async_send_commands({"system": "off"})
        h._timings = FAST
        n, t = self.ready(h)
        await h.async_send_commands({"system": "off"})
        self.assertFalse(h._queued_commands)

    async def test_pending_queue_is_bounded_and_off_has_priority(self):
        h = self.make(replace(FAST, command=0.035))
        tasks = [
            asyncio.create_task(h.async_send_commands({"fanSpeed": "4"}))
            for _ in range(32)
        ]
        await self.until(lambda: len(h._queued_commands) == 32)
        with self.assertRaises(HAError):
            await h.async_send_commands({"fanSpeed": "5"})
        stop = asyncio.create_task(h.async_send_commands({"system": "off"}))
        results = await asyncio.gather(*tasks, stop, return_exceptions=True)
        self.assertTrue(all(isinstance(x, HAError) for x in results))
        self.assertFalse(h._queued_commands)

    async def test_off_with_stale_unsupported_heater_mode_stays_available(self):
        h = self.make()
        n = Network(h)
        n.connect()
        n.frame(I)
        n.frame(zone(kind="heat"))
        self.assertTrue(h._ready)
        self.assertEqual(h.hvac_mode, c.HVACMode.OFF)
        self.assertNotIn(c.HVACMode.HEAT, h.hvac_modes)
        await h.async_set_fan_mode("5")
        self.assertEqual(h._zone_info["fanSpeed"], "5")

    async def test_active_unsupported_appliance_fails_closed(self):
        h = self.make()
        n = Network(h)
        t = n.connect()
        n.frame(I)
        n.frame(
            zone("on", kind="heat").replace(
                b"</myclimate>", b"<setPoint>22</setPoint></myclimate>"
            )
        )
        self.assertTrue(t.closing)
        self.assertFalse(h._ready)

    async def test_unknown_message_does_not_fabricate_state(self):
        h = self.make()
        n, t = self.ready(h)
        before = h._state_sequence
        n.frame(b"<myclimate><response>future_extension</response></myclimate>")
        self.assertEqual(h._state_sequence, before)
        self.assertTrue(h._ready)

    async def test_zone_whitespace_and_xml_escaping(self):
        h = self.make()
        n = Network(h)
        t = n.connect()
        n.frame(I.replace(b"Common", b"A, B&amp;C"))
        n.frame(Z.replace(b"Common", b"A, B&amp;C"))
        self.assertTrue(h._ready)
        self.assertEqual(h.preset_mode, "A,B&C")

        def reply(data):
            if b"<post>" in data:
                n.later(0, ACK, t)
            elif b"<get>getzoneinfo" in data:
                n.later(0, Z.replace(b"Common", b"B&amp;C,A"), t)

        t.handler = reply
        await h.async_send_commands({"zoneList": "A, B&C"})
        self.assertTrue(any(b"<zoneList>A,B&amp;C</zoneList>" in w for w in t.writes))

    async def test_frame_exact_size_and_one_over(self):
        for extra, accepted in ((0, True), (1, False)):
            size = helpers.MAX_FRAME_BYTES - len(b"<myclimate></myclimate>") + extra
            wire = b"<myclimate>" + b"x" * size + b"</myclimate>"
            frames = []
            t = Transport()
            p = helpers.HandleServer(
                lambda t: None, lambda f, **kw: frames.append(f), lambda t: None
            )
            p.connection_made(t)
            p.data_received(wire)
            self.assertEqual(bool(frames), accepted)
            self.assertEqual(t.closing, not accepted)

    async def test_interleaved_partial_documents_never_share_buffers(self):
        h = self.make()
        n = Network(h)
        old = n.connect()
        old.protocol.data_received(Z[:20])
        new = n.connect()
        n.frame(I)
        n.frame(Z)
        old.protocol.data_received(Z[20:])
        self.assertTrue(h._ready)
        self.assertIs(h._server_transport, new)
        self.assertEqual(h._zone_info["system"], "off")

    async def test_callbacks_can_remove_themselves(self):
        h = self.make()
        calls = []

        def remove():
            h.remove_callback(remove)
            calls.append(True)

        h.register_callback(remove)
        await h.publish_updates()
        await h.publish_updates()
        self.assertEqual(calls, [True])

    async def test_all_public_evap_command_methods(self):
        h = self.make()
        n, t = self.ready(h)
        await h.async_set_hvac_mode(c.HVACMode.COOL)
        self.assertEqual(h._zone_info["system"], "on")
        await h.async_set_hvac_mode(c.HVACMode.FAN_ONLY)
        self.assertEqual(h.hvac_mode, c.HVACMode.FAN_ONLY)
        await h.async_turn_off()
        self.assertEqual(h.hvac_mode, c.HVACMode.OFF)
        await h.async_turn_on()
        self.assertEqual(h._zone_info["system"], "on")
        await h.async_set_preset_mode("Common")
        await h.async_set_fan_mode("6")
        self.assertEqual(h._zone_info["fanSpeed"], "6")

    async def test_planned_renewal_does_not_abort_a_completing_command(self):
        h = self.make(replace(FAST, session=0.04, acknowledgement=0.10, command=0.4))
        n = Network(h)
        hub.create_datagram_transport, hub.create_server = n.datagram, n.listener
        h._start_task = asyncio.create_task(h.async_start())
        await self.until(lambda: h._ready)
        n.delay_ack = 0.06
        await h.async_send_commands({"system": "on"})
        await self.until(lambda: n.discovery >= 2 and h._ready)
        self.assertEqual(h._zone_info["system"], "on")

    async def test_planned_renewal_drain_is_bounded(self):
        h = self.make(
            replace(
                FAST,
                session=0.025,
                acknowledgement=0.10,
                renewal_drain=0.015,
                command=0.3,
            )
        )
        n = Network(h)
        hub.create_datagram_transport, hub.create_server = n.datagram, n.listener
        h._start_task = asyncio.create_task(h.async_start())
        await self.until(lambda: h._ready)
        n.ack = False
        n.apply = False
        with self.assertRaises(HAError):
            await h.async_send_commands({"system": "on"})
        await self.until(lambda: n.discovery >= 2 and h._ready)
        self.assertFalse(h._queued_commands)
        self.assertEqual(h._zone_info["system"], "off")

    async def test_queued_command_cannot_cross_planned_renewal(self):
        h = self.make(replace(FAST, session=0.04, acknowledgement=0.10, command=0.4))
        n = Network(h)
        hub.create_datagram_transport, hub.create_server = n.datagram, n.listener
        h._start_task = asyncio.create_task(h.async_start())
        await self.until(lambda: h._ready)
        n.delay_ack = 0.06
        active = asyncio.create_task(h.async_send_commands({"system": "on"}))
        await self.until(lambda: h._active_command is not None)
        queued = asyncio.create_task(h.async_send_commands({"fanSpeed": "7"}))
        results = await asyncio.gather(active, queued, return_exceptions=True)
        self.assertIsNone(results[0])
        self.assertIsInstance(results[1], HAError)
        self.assertFalse(
            any(
                b"<fanSpeed>7</fanSpeed>" in wire
                for t in n.transports
                for wire in t.writes
            )
        )

    async def test_short_recovery_keeps_cached_entity_state_but_not_readiness(self):
        h = self.make()
        n, old = self.ready(h)
        old.close()
        await asyncio.sleep(0)
        self.assertFalse(h._ready)
        self.assertTrue(h.available)
        new = n.connect()
        self.assertFalse(h._ready)
        self.assertTrue(h.available)
        n.frame(I)
        n.frame(Z)
        self.assertTrue(h._ready)
        self.assertTrue(h.available)
        self.assertIsNone(h._availability_timer)

    async def test_extended_outage_expires_cached_availability(self):
        h = self.make(replace(FAST, availability_grace=0.02))
        n, old = self.ready(h)
        old.close()
        await self.until(lambda: not h.available)
        self.assertFalse(h._ready)
        self.assertIsNone(h._availability_timer)
        n.connect()
        n.frame(I)
        n.frame(Z)
        self.assertTrue(h.available)

    async def test_repeated_connections_do_not_extend_availability_grace(self):
        h = self.make(replace(FAST, availability_grace=0.035))
        n, old = self.ready(h)
        old.close()
        await asyncio.sleep(0)
        deadline = h._availability_timer.when()
        n.connect()
        n.connect()
        self.assertEqual(h._availability_timer.when(), deadline)
        await self.until(lambda: not h.available)
        self.assertFalse(h._ready)

    async def test_teardown_cancels_cached_availability_timer(self):
        h = self.make()
        n, old = self.ready(h)
        old.close()
        await asyncio.sleep(0)
        self.assertIsNotNone(h._availability_timer)
        await h.async_teardown()
        self.assertIsNone(h._availability_timer)
        self.assertFalse(h.available)

    async def test_normal_renewal_does_not_publish_unavailable_off_transition(self):
        h = self.make(replace(FAST, session=0.025))
        n = Network(h)
        observations = []
        h.register_callback(lambda: observations.append((h.available, h.hvac_mode)))
        hub.create_datagram_transport, hub.create_server = n.datagram, n.listener
        h._start_task = asyncio.create_task(h.async_start())
        await self.until(lambda: h._ready)
        observations.clear()
        await self.until(lambda: n.discovery >= 2 and h._ready)
        self.assertTrue(observations)
        self.assertTrue(all(available for available, mode in observations))
        self.assertTrue(all(mode == c.HVACMode.OFF for available, mode in observations))

    async def test_readiness_is_false_while_last_ha_state_is_cached(self):
        h = self.make()
        n, old = self.ready(h)
        old.close()
        await asyncio.sleep(0)
        self.assertFalse(h._ready)
        self.assertFalse(h._connected)
        self.assertTrue(h.available)

    async def test_delete_waits_for_peer_close_and_quiet_period(self):
        h = self.make(replace(FAST, settle=0.08))
        n, t = self.ready(h)
        peer_closed = []

        def handle(data):
            if b"<delete>" in data:
                self.assertFalse(t.closing)

                def peer_close():
                    peer_closed.append(not t.closing)
                    t.close()

                h._hass.loop.call_later(0.005, peer_close)

        t.handler = handle
        start = h._hass.loop.time()
        await h._retire(h._generation)
        self.assertEqual(peer_closed, [True])
        self.assertTrue(t.closing)
        # Windows' monotonic clock/event-loop timers can resolve at ~16 ms.
        self.assertGreaterEqual(h._hass.loop.time() - start, 0.06)

    async def test_delete_without_peer_close_is_forced_after_bound(self):
        h = self.make(replace(FAST, settle=0.012))
        n, t = self.ready(h)
        await asyncio.wait_for(h._retire(h._generation), 0.5)
        self.assertTrue(t.closing)
        self.assertIsNone(h._server_transport)

    async def test_teardown_sends_delete_before_close(self):
        h = self.make()
        n, t = self.ready(h)
        seen = []

        def handle(data):
            if b"<delete>" in data:
                seen.append(not t.closing)

        t.handler = handle
        await h.async_teardown()
        self.assertEqual(seen, [True])
        self.assertTrue(t.closing)
        self.assertFalse(h.available)
        with self.assertRaises(HAError):
            await h.async_send_commands({"system": "on"})

    async def test_retiring_old_generation_cannot_delete_new_session(self):
        h = self.make()
        n, old = self.ready(h)
        generation = h._generation
        new = n.connect()
        n.frame(I)
        n.frame(Z)
        before = len(new.writes)
        await h._retire(generation)
        self.assertEqual(len(new.writes), before)
        self.assertFalse(new.closing)
        self.assertTrue(h._ready)

    async def test_cancelled_main_loop_closes_resources_without_teardown(self):
        h, n = await self.run_network()
        h._start_task.cancel()
        await asyncio.gather(h._start_task, return_exceptions=True)
        self.assertTrue(n.server.closed)
        self.assertTrue(n.transports[-1].closing)

    async def test_real_main_loop_teardown_with_active_tcp_client(self):
        h = self.make(replace(FAST, settle=0.025))
        loop = asyncio.get_running_loop()
        clients = []

        async def listener(*args, **kwargs):
            return await loop.create_server(
                lambda: helpers.HandleServer(
                    h.server_connection_made,
                    h.server_data_received,
                    h.server_connection_lost,
                    h._trace,
                ),
                "127.0.0.1",
                0,
            )

        async def controller():
            reader, writer = await asyncio.open_connection(
                "127.0.0.1", h._tcp_server.sockets[0].getsockname()[1]
            )
            clients.append(writer)
            writer.write(D + I + Z)
            await writer.drain()
            try:
                data = await asyncio.wait_for(reader.readuntil(b"</myclimate>"), 0.5)
                self.assertIn(b"<delete>connection</delete>", data)
            finally:
                writer.close()
                await writer.wait_closed()

        class UDP:
            def sendto(self, data):
                clients.append(asyncio.create_task(controller()))

            def close(self):
                pass

        async def datagram(*args, **kwargs):
            return UDP()

        hub.create_server, hub.create_datagram_transport = listener, datagram
        h._start_task = asyncio.create_task(h.async_start())
        await self.until(lambda: h._ready)
        server = h._tcp_server
        try:
            await asyncio.wait_for(h.async_teardown(), 0.5)
            self.assertFalse(server.is_serving())
            self.assertIsNone(h._tcp_server)
            await asyncio.gather(*(x for x in clients if isinstance(x, asyncio.Task)))
        finally:
            for x in clients:
                if not isinstance(x, asyncio.Task):
                    x.close()
            server.close()
            await server.wait_closed()

    async def test_cancelled_teardown_still_closes_listener_and_transport(self):
        h, n = await self.run_network()
        h._timings = replace(FAST, settle=0.1)
        task = asyncio.create_task(h.async_teardown())
        await self.until(lambda: any(b"<delete>" in w for w in n.transports[-1].writes))
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertTrue(n.transports[-1].closing)
        self.assertTrue(n.server.closed)
        self.assertIsNone(h._tcp_server)
        self.assertFalse(h.available)


class Lifecycle(unittest.IsolatedAsyncioTestCase):
    async def test_disabled_entity_unload_still_tears_down_and_removes_service(self):
        source = ast.parse((ROOT / "__init__.py").read_text(encoding="utf8"))
        function = next(
            x
            for x in source.body
            if isinstance(x, ast.AsyncFunctionDef) and x.name == "async_unload_entry"
        )
        scope = {
            "asyncio": asyncio,
            "DOMAIN": "bonaire_myclimate",
            "PLATFORMS": ["climate"],
            "HomeAssistant": object,
            "ConfigEntry": object,
        }
        exec(
            compile(
                ast.Module(body=[function], type_ignores=[]),
                "<async_unload_entry>",
                "exec",
            ),
            scope,
        )
        for success in (False, True):
            calls = []

            async def unload(*args):
                return success

            async def teardown():
                calls.append("teardown")

            controller = types.SimpleNamespace(async_teardown=teardown)
            hass = types.SimpleNamespace(
                data={"bonaire_myclimate": {"test": controller}},
                config_entries=types.SimpleNamespace(async_forward_entry_unload=unload),
                services=types.SimpleNamespace(
                    async_remove=lambda *args: calls.append("service_removed")
                ),
            )
            result = await scope["async_unload_entry"](
                hass, types.SimpleNamespace(entry_id="test")
            )
            self.assertEqual(result, success)
            self.assertEqual(calls, ["teardown", "service_removed"] if success else [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
