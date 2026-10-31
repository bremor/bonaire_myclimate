# Session recovery and command confirmation

The module can accept a TCP connection without answering the first installation
or state query. A connected socket alone therefore does not mean the controller
is ready. Previously those queries were sent only once, so recovery could wait
for a much later unsolicited update. An unexpected disconnect also did not
interrupt the fixed 220-second session sleep.

The connection loop now requires both a valid installation and state from the
current TCP connection. Each phase retries three times at five-second intervals,
then closes the failed session and rediscovers with bounded backoff. Each
discovery owns a fresh UDP socket. Connection changes wake the loop immediately;
old sockets cannot overwrite a newer session's state or readiness.

The existing 220-second renewal remains. New commands wait during renewal, while
an in-flight command gets up to ten seconds to finish. DELETE retains the
three-second peer-close/quiet interval before rediscovery. Shutdown closes the
client before awaiting the listener, including when the climate entity is
disabled or teardown is cancelled.

## State and command behavior

- TCP is a stream: the receiver buffers fragmented XML and separates combined
  documents before parsing. The per-document limit is 64 KiB; malformed input
  closes that connection. A large burst of small complete documents is allowed.
- The last confirmed HA state remains available for up to 45 seconds during a
  short reconnect. This is cached state, not evidence of a live connection.
  Internal command readiness becomes false immediately. Repeated failed
  connections do not restart that grace period; continued failure makes the
  entity unavailable. A new hub starts unavailable until its first handshake.
- Every service call has a 30-second total deadline, including queueing and
  readiness. Success requires a fresh state matching the requested fields.
  An acknowledgement alone is insufficient; a lost acknowledgement can still
  succeed if a subsequent state query confirms the command.
- Pending commands from an established session fail if that session changes;
  they are not replayed on the replacement connection. A newer OFF request
  supersedes older non-OFF commands. The queue admits 32 normal requests and
  reserves one additional slot for an urgent OFF request.
- Invalid requests, queue overflow, timeouts and unconfirmed commands raise
  `HomeAssistantError`. The raw-command service accepts the documented protocol
  fields `system`, `type`, `zoneList`, `mode`, `fanSpeed` and `setPoint`, with
  value and installed-appliance/zone validation. Unknown fields are rejected.
  XML serialization escapes values. Zone lists are limited to 12 distinct names
  of at most 64 characters each to bound combination generation.

Normal HA logging is used. This patch does not require a separate telemetry
module, persistent log files, network probes, or changes to automations. The
official phone app must still be closed because the controller has a single
local session.

## Running the regression tests

From the repository root, with Python 3.12 or newer:

```console
python -m unittest discover -s tests -v
```

The 72 tests need only the standard library. They load the actual protocol
modules with small HA constant/exception stubs, fake controller responses, and
real TCP connections bound only to localhost on ephemeral ports. No appliance,
HA instance, credentials or broadcast traffic is required. Run this suite in a
separate Python process; it supplies its own `homeassistant` modules. The unload
test executes the actual unload function with fake HA service/config-entry
objects; this is not a full Home Assistant integration test.

Coverage includes dropped handshake replies and discovery, retry exhaustion,
unexpected and scheduled disconnects, stale transport callbacks, XML splits at
every byte boundary, UTF-8 fragmentation, combined frames, size boundaries,
invalid state/capabilities, acknowledgement/state disagreements, command
deadlines, cancellation, queue limits, OFF priority, renewal overlap, cached
availability expiry, graceful/forced shutdown and active-client listener cleanup.

## Hardware validation and remaining limits

The same recovery/command logic was tested on a live evaporative installation.
Before the repair, an observed renewal reconnected TCP in about seven seconds
but did not become ready until an unsolicited state around 64 seconds after
renewal. In another passive observation the module acknowledged the request's
TCP bytes but returned no application response. This does not establish the
module's internal cause; missing application responses are the condition the
client must recover from.

The repaired logic recovered a missed state reply on its five-second retry.
Three final-build scheduled renewals completed in 10.7, 11.6 and 10.8 seconds,
including their shutdown quiet period. Two real OFF requests were confirmed by
fresh state, and availability survived these short renewals. These observations
are a short hardware check, not an uptime guarantee. The public port removes
site-specific diagnostics; its regression suite was rerun independently.

Physical ON, heating, pump and fan changes were not exercised in that hardware
check. Those command branches use simulated responses in the tests. Additional
maintainer testing on heating, refrigerated cooling, other zone layouts and
firmware versions is needed before release. The XML delimiter intentionally
matches the module's observed `<myclimate>...</myclimate>` wire format; alternate
root attributes/XML declarations would need separate fixtures and support.
