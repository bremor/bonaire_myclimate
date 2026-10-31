"""A bounded, session-aware client for the Bonaire MyClimate Wi-Fi module."""

import asyncio
import inspect
import logging
import math
import xml.etree.ElementTree as ET
from dataclasses import dataclass

from homeassistant.components.climate.const import ClimateEntityFeature, HVACMode
from homeassistant.exceptions import HomeAssistantError

from .helpers import create_datagram_transport, create_server, zone_combinations
from .const import (
    FAN_MODES_COOL,
    FAN_MODES_EVAP,
    FAN_MODES_FAN_ONLY,
    FAN_MODES_HEAT,
    XML_DELETE,
    XML_DISCOVERY,
    XML_GETZONEINFO,
    XML_INSTALLATION,
)

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class Timings:
    discovery: float = 10.0
    phase: float = 5.0
    phase_attempts: int = 3
    session: float = 220.0
    settle: float = 3.0
    renewal_drain: float = 10.0
    availability_grace: float = 45.0
    command: float = 30.0
    acknowledgement: float = 6.0
    verification: float = 5.0
    command_attempts: int = 3


class SessionLost(HomeAssistantError):
    """The command or exchange belongs to a session that has ended."""


class Hub:
    def __init__(self, hass, local_ip, *, timings=None, start=True):
        self._hass, self._local_ip = hass, local_ip
        self._timings = timings or Timings()
        self._appliances = {"heat": None, "cool": None, "evap": None}
        self._callbacks = set()
        self._connected = self._ready = self.available = False
        self._enable_turn_on_off_backwards_compatibility = False
        self._fan_mode_memory_heat = "thermo"
        self._postzoneinfo_response_ok = False
        self._queued_commands = []
        self._server_transport = self._udp_transport = self._tcp_server = None
        self._start_task = None
        self._generation = 0
        self._installation_generation = self._state_generation = None
        self._zone_info = {}
        self._pending_zone = None
        self._state_sequence = 0
        self._changed = asyncio.Event()
        self._stop = asyncio.Event()
        self._closing = False
        self._renewing = False
        self._availability_timer = None
        self._teardown_lock = asyncio.Lock()
        self._command_lock = asyncio.Lock()
        self._command_tasks = set()
        self._publish_tasks = set()
        self._stop_epoch = 0
        self._active_command = None
        self.current_temperature = self.fan_mode = self.fan_modes = None
        self.hvac_mode = self.hvac_modes = self.preset_mode = self.preset_modes = None
        self.target_temperature = None
        self.supported_features = (
            ClimateEntityFeature.TARGET_TEMPERATURE
            | ClimateEntityFeature.PRESET_MODE
            | ClimateEntityFeature.FAN_MODE
            | ClimateEntityFeature.TURN_OFF
            | ClimateEntityFeature.TURN_ON
        )
        if start:
            self._start_task = hass.loop.create_task(self.async_start())
            self._start_task.add_done_callback(self._main_done)

    def _trace(self, event, **fields):
        """Use standard HA debug logging; no persistent capture or external probes."""
        _LOGGER.debug("%s: %s", event, fields)

    def _trace_error(self, event, error, **fields):
        _LOGGER.warning("%s: %s (%s)", event, error, fields)

    def _main_done(self, task):
        if not task.cancelled() and (error := task.exception()) is not None:
            _LOGGER.error("Bonaire connection task failed", exc_info=error)

    def register_callback(self, callback):
        self._callbacks.add(callback)

    def remove_callback(self, callback):
        self._callbacks.discard(callback)

    async def publish_updates(self):
        self._trace("publish_updates", callback_count=len(self._callbacks))
        for callback in tuple(self._callbacks):
            try:
                result = callback()
                if inspect.isawaitable(result):
                    await result
            except Exception:
                _LOGGER.exception("Bonaire state callback failed")

    def _publish(self):
        if self._closing:
            return
        task = self._hass.loop.create_task(self.publish_updates())
        self._publish_tasks.add(task)
        task.add_done_callback(self._publish_tasks.discard)

    def _signal(self):
        self._changed.set()

    def _guard(self, generation=None, stop_epoch=None):
        if self._closing:
            raise SessionLost("Bonaire integration is stopping")
        if generation is not None and (
            generation != self._generation or self._server_transport is None
        ):
            raise SessionLost("Bonaire session changed or disconnected")
        if stop_epoch is not None and stop_epoch != self._stop_epoch:
            raise HomeAssistantError(
                "Bonaire command superseded by a newer OFF request"
            )

    async def _wait(self, predicate, timeout, *, generation=None, stop_epoch=None):
        deadline = self._hass.loop.time() + max(0, timeout)
        while True:
            self._changed.clear()
            self._guard(generation, stop_epoch)
            if predicate():
                return True
            left = deadline - self._hass.loop.time()
            if left <= 0:
                return False
            try:
                await asyncio.wait_for(self._changed.wait(), left)
            except TimeoutError:
                # A callback at the deadline may already have supplied the state.
                self._guard(generation, stop_epoch)
                return bool(predicate())

    async def _pause(self, seconds):
        try:
            await asyncio.wait_for(self._stop.wait(), seconds)
        except TimeoutError:
            pass

    def _mark_unready(self):
        self._ready = False
        # Readiness is never cached. Keep only the last confirmed HA entity state
        # briefly, avoiding a false unavailable-to-OFF/manual-stop transition.
        if self._closing:
            self.available = False
            if self._availability_timer is not None:
                self._availability_timer.cancel()
                self._availability_timer = None
        elif self.available and self._availability_timer is None:
            self._availability_timer = self._hass.loop.call_later(
                self._timings.availability_grace, self._expire_availability
            )

    def _expire_availability(self):
        self._availability_timer = None
        if not self._ready:
            self.available = False
            self._signal()
            self._publish()

    def server_connection_made(self, transport):
        if self._closing:
            transport.close()
            return
        previous = self._server_transport
        self._generation += 1
        self._renewing = False
        self._server_transport = transport
        self._connected = False
        self._mark_unready()
        self._installation_generation = self._state_generation = None
        self._pending_zone = None
        self._postzoneinfo_response_ok = False
        self._trace(
            "hub_transport_replaced",
            previous=previous,
            replacement=transport,
            generation=self._generation,
        )
        if previous is not None and previous is not transport:
            previous.close()
        self._signal()
        self._publish()

    def server_connection_lost(self, transport):
        if transport is not self._server_transport:
            self._trace("stale_connection_lost_ignored", transport=transport)
            return
        self._server_transport = None
        self._renewing = False
        self._connected = False
        self._mark_unready()
        self._installation_generation = self._state_generation = None
        self._pending_zone = None
        self._postzoneinfo_response_ok = False
        self._signal()
        self._publish()

    def _drop(self, generation=None, *, delete=False):
        if generation is not None and generation != self._generation:
            return
        transport = self._server_transport
        if transport is None:
            return
        if delete and not transport.is_closing():
            try:
                self._write(XML_DELETE.encode())
            except Exception as exc:
                self._trace_error("delete_send_error", exc)
        self.server_connection_lost(transport)
        transport.close()

    async def _retire(self, generation):
        """Give the module its DELETE/peer-close grace before forcing TCP closed."""
        if generation != self._generation:
            return
        transport = self._server_transport
        if transport is None:
            return
        deadline = self._hass.loop.time() + self._timings.settle
        try:
            if not transport.is_closing():
                self._write_owned(XML_DELETE.encode())
                self._trace("session_delete_sent", generation=generation)
                while self._server_transport is transport:
                    self._changed.clear()
                    if self._server_transport is not transport:
                        break
                    left = deadline - self._hass.loop.time()
                    if left <= 0:
                        break
                    try:
                        await asyncio.wait_for(self._changed.wait(), left)
                    except TimeoutError:
                        break
        except Exception as exc:
            self._trace_error("delete_send_error", exc)
        finally:
            self._drop(generation)
            transport.close()
        left = deadline - self._hass.loop.time()
        if left > 0:
            # Also retain the quiet period on unload, before a new hub can bind.
            await asyncio.sleep(left)

    async def _discover(self, attempt):
        self._guard()
        transport = await create_datagram_transport(self._hass.loop, trace=self._trace)
        self._udp_transport = transport
        try:
            self._trace(
                "discovery_send",
                attempt=attempt,
                destination=["255.255.255.255", 10001],
                callback=[self._local_ip, 10003],
            )
            transport.sendto(XML_DISCOVERY.format(self._local_ip).encode())
            self._trace("discovery_send_returned", attempt=attempt)
            return await self._wait(lambda: self._connected, self._timings.discovery)
        finally:
            transport.close()
            if self._udp_transport is transport:
                self._udp_transport = None

    async def _phase(self, name, xml, predicate, generation):
        for attempt in range(1, self._timings.phase_attempts + 1):
            self._guard(generation)
            if predicate():
                return
            self._trace(
                "handshake_request", phase=name, attempt=attempt, generation=generation
            )
            self._write(xml.encode())
            if await self._wait(predicate, self._timings.phase, generation=generation):
                return
            self._trace(
                "handshake_timeout", phase=name, attempt=attempt, generation=generation
            )
        raise HomeAssistantError("Bonaire " + name + " handshake timed out")

    async def async_start(self):
        self._trace("connection_loop_started")
        failures = 0
        try:
            while not self._closing:
                generation = None
                try:
                    if self._tcp_server is None:
                        self._tcp_server = await create_server(
                            self._hass.loop,
                            self.server_connection_made,
                            self.server_data_received,
                            self.server_connection_lost,
                            trace=self._trace,
                        )
                        self._trace(
                            "tcp_listener_started",
                            sockets=[
                                sock.getsockname() for sock in self._tcp_server.sockets
                            ],
                        )
                    if not self._connected:
                        if not await self._discover(failures + 1):
                            raise HomeAssistantError("Bonaire discovery timed out")
                    generation = self._generation
                    await self._phase(
                        "installation",
                        XML_INSTALLATION,
                        lambda: self._installation_generation == generation,
                        generation,
                    )
                    await self._phase(
                        "state",
                        XML_GETZONEINFO,
                        lambda: self._state_generation == generation,
                        generation,
                    )
                    self._guard(generation)
                    failures = 0
                    self._trace("handshake_complete", generation=generation)
                    self._trace("scheduled_session_wait", seconds=self._timings.session)
                    # A lost/superseded session wakes this immediately; it no longer
                    # waits out the renewal timer before attempting recovery.
                    changed = await self._wait(
                        lambda: generation != self._generation or not self._connected,
                        self._timings.session,
                    )
                    if not changed:
                        # Stop admitting new writes, but give an in-flight command
                        # a short, bounded chance to finish before planned renewal.
                        self._renewing = True
                        self._mark_unready()
                        self._signal()
                        self._publish()
                        if self._active_command is not None:
                            self._trace(
                                "planned_renewal_wait_for_command",
                                seconds=self._timings.renewal_drain,
                            )
                            await self._wait(
                                lambda: self._active_command is None,
                                self._timings.renewal_drain,
                                generation=generation,
                            )
                        self._trace("scheduled_disconnect_requested")
                        await self._retire(generation)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    if self._closing:
                        break
                    failures += 1
                    self._trace_error(
                        "session_recovery",
                        exc,
                        generation=generation,
                        failures=failures,
                    )
                    self._drop(generation)
                    # A new connection may already have superseded the failed one.
                    if not self._connected:
                        delay = min(30.0, self._timings.settle * min(failures, 10))
                        self._trace(
                            "discovery_backoff", attempts=failures, sleep_s=delay
                        )
                        await self._pause(delay)
        finally:
            # Teardown owns graceful retirement. Do not make it await a server
            # whose active client it has not yet been allowed to close.
            if not self._closing:
                self._drop()
                if self._udp_transport is not None:
                    self._udp_transport.close()
                    self._udp_transport = None
                if self._tcp_server is not None:
                    self._tcp_server.close()
                    await self._tcp_server.wait_closed()
                    self._tcp_server = None

    async def async_teardown(self):
        async with self._teardown_lock:
            if self._closing:
                return
            self._closing = True
            self._mark_unready()
            self._stop.set()
            self._signal()
            try:
                if self._start_task is not None and not self._start_task.done():
                    self._start_task.cancel()
                    await asyncio.gather(self._start_task, return_exceptions=True)
                await self._retire(self._generation)
            finally:
                # Cancellation must not leave an occupied listener or live client.
                self._drop()
                if self._udp_transport is not None:
                    self._udp_transport.close()
                    self._udp_transport = None
                if self._tcp_server is not None:
                    self._tcp_server.close()
                    await self._tcp_server.wait_closed()
                    self._tcp_server = None
                tasks = tuple(self._command_tasks | self._publish_tasks)
                if tasks:
                    done, pending = await asyncio.wait(tasks, timeout=2)
                    for task in pending:
                        task.cancel()
                    await asyncio.gather(*tasks, return_exceptions=True)

    @staticmethod
    def _number(value):
        number = float(value)
        if not math.isfinite(number):
            raise ValueError("Non-finite temperature")
        return int(number) if number.is_integer() else number

    def _installation(self, root):
        appliances = {"heat": None, "cool": None, "evap": None}
        seen = set()
        for appliance in root.findall("appliance"):
            kind = appliance.findtext("type")
            if kind not in appliances or kind in seen:
                raise ValueError("Invalid or duplicate appliance type")
            seen.add(kind)
            zones = appliance.findtext("zoneList") or None
            if zones:
                zones = ",".join(x.strip() for x in zones.split(","))
            zone_combinations(zones)  # Validate before publishing any capability.
            appliances[kind] = zones
        if not any(appliances.values()):
            raise ValueError("Installation contains no appliances")
        self._appliances = appliances
        self._preset_modes_heat = zone_combinations(appliances["heat"])
        self._preset_modes_cool = zone_combinations(appliances["cool"])
        self._preset_modes_evap = zone_combinations(appliances["evap"])
        self.hvac_modes = [HVACMode.OFF, HVACMode.FAN_ONLY]
        if appliances["heat"]:
            self.hvac_modes.append(HVACMode.HEAT)
        if appliances["cool"] or appliances["evap"]:
            self.hvac_modes.append(HVACMode.COOL)
        self._installation_generation = self._generation

    def _zone(self, root):
        zone = {item.tag: item.text for item in root}
        if len(zone) != len(root) or zone.get("system") not in ("on", "off"):
            raise ValueError("Invalid or duplicate state fields")
        if zone.get("zoneList"):
            zone["zoneList"] = ",".join(x.strip() for x in zone["zoneList"].split(","))
        temperature = self._number(zone.get("roomTemp"))
        if not -50 <= temperature <= 80 or not zone.get("zoneList"):
            raise ValueError("Invalid room temperature or zone")
        if zone["system"] == "on":
            kind, mode = zone.get("type"), zone.get("mode")
            if kind not in self._appliances or mode not in (
                "fan",
                "manual",
                "thermo",
                "econ",
                "boost",
            ):
                raise ValueError("Invalid active appliance state")
            if mode == "fan" or kind == "evap":
                if zone.get("fanSpeed") not in tuple(str(i) for i in range(9)):
                    raise ValueError("Invalid fan speed")
            if kind in ("heat", "cool") and mode != "fan":
                self._number(zone.get("setPoint"))
        return zone

    def _refresh_ready(self):
        complete = (
            self._connected
            and self._installation_generation == self._generation
            and self._state_generation == self._generation
        )
        if complete:
            zone = self._pending_zone
            kind = zone.get("type") or next(k for k, v in self._appliances.items() if v)
            if not self._appliances.get(kind):
                if zone["system"] == "off":
                    kind = next(k for k, v in self._appliances.items() if v)
                else:
                    raise ValueError("State references an unavailable appliance")
            self._zone_info = zone
            self.current_temperature = self._number(zone["roomTemp"])
            self.preset_mode = zone["zoneList"]
            self.preset_modes = getattr(self, "_preset_modes_" + kind)
            self.target_temperature = None
            self.supported_features = (
                ClimateEntityFeature.PRESET_MODE
                | ClimateEntityFeature.FAN_MODE
                | ClimateEntityFeature.TURN_OFF
                | ClimateEntityFeature.TURN_ON
            )
            if zone.get("mode") == "fan":
                self.fan_mode, self.fan_modes = zone.get("fanSpeed"), FAN_MODES_FAN_ONLY
            elif kind == "evap":
                self.fan_mode, self.fan_modes = zone.get("fanSpeed"), FAN_MODES_EVAP
            else:
                self.fan_mode = zone.get("mode")
                self.fan_modes = FAN_MODES_HEAT if kind == "heat" else FAN_MODES_COOL
                self.supported_features |= ClimateEntityFeature.TARGET_TEMPERATURE
                if zone.get("setPoint") is not None:
                    self.target_temperature = self._number(zone["setPoint"])
                if kind == "heat" and zone.get("mode") not in (None, "fan"):
                    self._fan_mode_memory_heat = zone["mode"]
            if zone["system"] == "off":
                self.hvac_mode = HVACMode.OFF
            elif zone["mode"] == "fan":
                self.hvac_mode = HVACMode.FAN_ONLY
            else:
                self.hvac_mode = HVACMode.HEAT if kind == "heat" else HVACMode.COOL
        ready = complete and not self._renewing
        changed = ready != self._ready
        if ready:
            self._ready = self.available = True
            if self._availability_timer is not None:
                self._availability_timer.cancel()
                self._availability_timer = None
        else:
            self._mark_unready()
        if changed or complete:
            self._publish()

    def server_data_received(self, data, *, transport):
        if self._closing or transport is not self._server_transport:
            self._trace("stale_frame_ignored", transport=transport)
            return
        if b"<!DOCTYPE" in data.upper() or b"<!ENTITY" in data.upper():
            raise ValueError("DTD is not allowed")
        root = ET.fromstring(data)
        if root.tag != "myclimate":
            raise ValueError("Unexpected document root")
        response, post = root.findtext("response"), root.findtext("post")
        if response == "discovery":
            self._connected = True
            self._trace("discovery_accepted")
        elif response == "installation":
            self._installation(root)
            self._refresh_ready()
        elif response == "getzoneinfo" or post == "postzoneinfo":
            if not self._connected:
                return
            self._pending_zone = self._zone(root)
            self._state_generation = self._generation
            self._state_sequence += 1
            self._refresh_ready()
        elif response == "postzoneinfo" and root.findtext("result") == "ok":
            active = self._active_command
            if active and active["generation"] == self._generation and active["sent"]:
                self._postzoneinfo_response_ok = True
                self._trace("command_ack_received", command_object_id=active["id"])
            else:
                self._trace("unmatched_command_ack_ignored")
        self._trace("message_received", response=response, post=post)
        self._signal()

    def _write(self, data):
        self._guard()
        self._write_owned(data)

    def _write_owned(self, data):
        transport = self._server_transport
        if transport is None or transport.is_closing():
            raise SessionLost("Bonaire transport is closed")
        self._trace("tcp_tx", payload={"bytes": len(data)})
        try:
            transport.write(data)
        except Exception as exc:
            self._trace_error("tcp_tx_error", exc)
            self.server_connection_lost(transport)
            transport.close()
            raise SessionLost("Bonaire write failed") from exc
        self._trace("tcp_tx_returned")

    def _current_kind(self):
        kind = self._zone_info.get("type")
        return (
            kind
            if self._appliances.get(kind)
            else next((k for k, v in self._appliances.items() if v), None)
        )

    async def async_set_fan_mode(self, fan_mode):
        """Set new target fan operation."""
        if self._zone_info.get("mode") == "fan":
            command = {"fanSpeed": fan_mode}
        elif self._current_kind() == "evap":
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
            commands = {"system": "off"}
        elif hvac_mode == HVACMode.FAN_ONLY:
            commands = {"system": "on", "mode": "fan"}
        elif hvac_mode == HVACMode.HEAT:
            commands = {
                "system": "on",
                "type": "heat",
                "mode": self._fan_mode_memory_heat,
            }
        elif hvac_mode == HVACMode.COOL:
            if self._appliances["cool"] is not None:
                commands = {"system": "on", "type": "cool", "mode": "thermo"}
            elif self._appliances["evap"] is not None:
                commands = {"system": "on", "type": "evap"}

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

    @staticmethod
    def _command(command):
        if not isinstance(command, dict) or not command:
            raise HomeAssistantError("Bonaire command must be a non-empty object")
        allowed = {"system", "type", "zoneList", "mode", "fanSpeed", "setPoint"}
        if any(key not in allowed for key in command):
            raise HomeAssistantError("Unsupported Bonaire command field")
        result = {}
        for key, value in command.items():
            if isinstance(value, bool) or not isinstance(value, (str, int, float)):
                raise HomeAssistantError("Invalid Bonaire command value")
            result[key] = str(value)
        choices = {
            "system": ("on", "off"),
            "type": ("heat", "cool", "evap"),
            "mode": ("fan", "manual", "thermo", "econ", "boost"),
            "fanSpeed": tuple(str(i) for i in range(9)),
        }
        if any(
            key in result and result[key] not in values
            for key, values in choices.items()
        ):
            raise HomeAssistantError("Invalid Bonaire command setting")
        if "zoneList" in result:
            result["zoneList"] = ",".join(
                x.strip() for x in result["zoneList"].split(",")
            )
            try:
                zone_combinations(result["zoneList"])
            except ValueError as exc:
                raise HomeAssistantError(str(exc)) from exc
            if not result["zoneList"] or any(ord(ch) < 32 for ch in result["zoneList"]):
                raise HomeAssistantError("Invalid Bonaire command zones")
        if "setPoint" in result:
            try:
                value = Hub._number(result["setPoint"])
                if not 0 <= value <= 50:
                    raise ValueError("Invalid target temperature")
            except (ValueError, TypeError) as exc:
                raise HomeAssistantError("Invalid Bonaire target temperature") from exc
        return result

    def _validate_command_appliance(self, command):
        if command.get("system") == "off":
            return
        kind = command.get("type", self._current_kind())
        if kind not in self._appliances or not self._appliances[kind]:
            raise HomeAssistantError("Requested Bonaire appliance is not installed")
        if "zoneList" in command:
            available = set(self._appliances[kind].split(","))
            if not set(command["zoneList"].split(",")) <= available:
                raise HomeAssistantError("Requested Bonaire zone is not installed")

    def _matches(self, command, after_sequence):
        if self._state_sequence <= after_sequence:
            return False
        actual = self._zone_info
        for key, expected in command.items():
            got = actual.get(key)
            if key == "zoneList":
                if set((got or "").split(",")) != set(expected.split(",")):
                    return False
            elif key == "setPoint":
                if got is None or self._number(got) != self._number(expected):
                    return False
            elif (
                key == "mode"
                and command.get("type", actual.get("type")) == "evap"
                and command.get("fanSpeed") == "8"
                and expected in ("manual", "boost")
            ):
                if got not in ("manual", "boost"):
                    return False
            elif got != expected:
                return False
        return True

    async def async_send_commands(self, command):
        caller_id = format(id(command), "x")
        command = self._command(command)
        self._guard()
        queued_generation = self._generation if self._ready else None
        off = command.get("system") == "off"
        if len(self._queued_commands) >= 32:
            if not off or any(x.get("system") == "off" for x in self._queued_commands):
                raise HomeAssistantError("Bonaire command queue is full")
        if off:
            self._stop_epoch += 1
            self._signal()
        stop_epoch = None if off else self._stop_epoch
        task = asyncio.current_task()
        self._command_tasks.add(task)
        self._queued_commands.append(command)
        command_id = format(id(command), "x")
        self._trace(
            "command_queued",
            command_object_id=command_id,
            command=command,
            caller_command_object_id=caller_id,
            queued_generation=queued_generation,
        )
        active = None
        try:
            # Includes time waiting for the lock, readiness and state verification.
            async with asyncio.timeout(self._timings.command):
                async with self._command_lock:
                    self._guard(queued_generation, stop_epoch)
                    self._trace("command_wait_for_ready", command_object_id=command_id)
                    if not await self._wait(
                        lambda: self._ready,
                        self._timings.command,
                        stop_epoch=stop_epoch,
                    ):
                        raise HomeAssistantError("Bonaire command readiness timed out")
                    self._guard(queued_generation, stop_epoch)
                    generation = self._generation
                    self._guard(generation, stop_epoch)
                    self._validate_command_appliance(command)
                    self._trace("command_ready", command_object_id=command_id)
                    root = ET.Element("myclimate")
                    ET.SubElement(root, "post").text = "postzoneinfo"
                    for key, value in command.items():
                        ET.SubElement(root, key).text = value
                    wire = ET.tostring(
                        root, encoding="utf-8", short_empty_elements=False
                    )
                    baseline = self._state_sequence
                    active = {"id": command_id, "generation": generation, "sent": False}
                    self._active_command = active
                    self._postzoneinfo_response_ok = False
                    confirmed = lambda: self._matches(command, baseline)
                    acknowledged = False
                    for attempt in range(1, self._timings.command_attempts + 1):
                        self._guard(generation, stop_epoch)
                        self._postzoneinfo_response_ok = False
                        active["sent"] = True
                        self._trace(
                            "command_attempt",
                            command_object_id=command_id,
                            attempt=attempt,
                        )
                        self._write(wire)
                        await self._wait(
                            lambda: self._postzoneinfo_response_ok or confirmed(),
                            self._timings.acknowledgement,
                            generation=generation,
                            stop_epoch=stop_epoch,
                        )
                        self._trace(
                            "command_attempt_result",
                            command_object_id=command_id,
                            attempt=attempt,
                            acknowledged=self._postzoneinfo_response_ok,
                            confirmed=confirmed(),
                        )
                        if confirmed():
                            break
                        if self._postzoneinfo_response_ok:
                            acknowledged = True
                            break
                    if not confirmed():
                        # Query actual state even without an ACK; an applied command
                        # may have lost only its reply. Never call this success on ACK alone.
                        for attempt in range(1, self._timings.command_attempts + 1):
                            self._guard(generation, stop_epoch)
                            self._trace(
                                "command_verify_request",
                                command_object_id=command_id,
                                attempt=attempt,
                                acknowledged=acknowledged,
                            )
                            self._write(XML_GETZONEINFO.encode())
                            if await self._wait(
                                confirmed,
                                self._timings.verification,
                                generation=generation,
                                stop_epoch=stop_epoch,
                            ):
                                break
                        else:
                            raise HomeAssistantError(
                                "Bonaire command state could not be confirmed"
                            )
                    self._trace(
                        "command_state_confirmed",
                        command_object_id=command_id,
                        generation=generation,
                        acknowledged=self._postzoneinfo_response_ok,
                    )
        except TimeoutError as exc:
            raise HomeAssistantError("Bonaire command exceeded its deadline") from exc
        finally:
            if self._active_command is active:
                self._active_command = None
                self._postzoneinfo_response_ok = False
            self._queued_commands[:] = [
                x for x in self._queued_commands if x is not command
            ]
            self._command_tasks.discard(task)
            self._signal()
            self._trace("command_removed", command_object_id=command_id)
