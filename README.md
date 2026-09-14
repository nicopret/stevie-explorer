# Stevie Explorer

Stevie Explorer is a protocol discovery and device investigation platform.

Its purpose is to explore, analyse, document, and reverse-engineer devices, protocols, and services without introducing experimental code into Stevie Core.

Stevie Explorer is the laboratory.

Stevie Core is the production system.

---

# Philosophy

Stevie Explorer follows one simple principle:

> Explore freely, capture everything, promote only proven behaviour.

Every successful experiment can later become a production-quality integration inside Stevie Core.

---

# Relationship to Stevie

```text
               Stevie Explorer
                      │
                      │ discovers protocols
                      │ validates behaviour
                      │ documents findings
                      ▼
                 Stevie Core
                      │
                      │ production drivers
                      │ orchestration
                      │ automation
                      ▼
                 Home Media Platform
```

Explorer never controls the home.

Explorer discovers how devices work.

Stevie consumes those discoveries.

---

# Current Architecture

```text
                      Clients
                          │
                          ▼
                     FastAPI API
                          │
                          ▼
                 Explorer Kernel
          ┌───────────────┼───────────────┐
          │               │               │
     Configuration     Event Bus     Telemetry
          │                               │
          └───────────────┬───────────────┘
                          │
                     Future Services
```

---

# Milestone 1

The first milestone establishes the project foundation.

Implemented:

* Kernel
* Component registry
* Service lifecycle
* Configuration component
* Event bus
* Structured telemetry
* FastAPI integration
* Health endpoint
* Component discovery endpoint
* Initial unit tests

No protocol exploration has been implemented yet.

---

# Current Project Structure

```text
stevie-explorer/
├── pyproject.toml
├── README.md
├── .env.example
├── src/
│   └── stevie_explorer/
│       ├── __init__.py
│       ├── main.py
│       ├── api.py
│       ├── config.py
│       ├── kernel.py
│       ├── telemetry.py
│       ├── eventbus.py
│       ├── events.py
│       └── identifiers/
├── sandbox/
└── tests/
```

Folders will be added as the project grows rather than creating empty placeholders.

---

# Design Principles

Stevie Explorer follows the same architectural principles as Stevie Core.

* Async-first architecture
* Event-driven communication
* Service/component separation
* Strongly typed identifiers
* Structured telemetry
* Small, focused modules
* Configuration via `.env`
* Dependency injection through the kernel
* Minimal global state

---

# Components

## Explorer Kernel

The kernel owns the application lifecycle.

Responsibilities:

* Register components
* Start services
* Stop services
* Maintain the component registry
* Coordinate application shutdown

The kernel intentionally contains no protocol-specific logic.

---

## Configuration

Configuration is responsible for loading application settings from `.env`.

Current settings include:

* Environment
* API host
* API port

Future settings will include discovery configuration, storage options, security settings, and transport defaults.

---

## Event Bus

The Event Bus is the internal communication backbone.

Current capabilities:

* Publish events
* Synchronous publishing
* Asynchronous publishing
* Topic subscriptions
* Wildcard topic matching

Future capabilities:

* Event persistence
* Event replay
* Message tracing
* Distributed transports

---

## Telemetry

Telemetry provides structured operational logging.

Current output:

* Local JSON logging using Structlog

Future outputs:

* Grafana / Loki
* Audit logs
* Metrics
* Alerting
* Performance dashboards

---

## API

FastAPI provides the external interface.

Current endpoints:

```text
GET /health
GET /components
GET /devices
GET /devices/{device_id}
POST /devices
DELETE /devices/{device_id}
GET /devices/{device_id}/targets
POST /devices/{device_id}/targets
DELETE /devices/{device_id}/targets/{target_id}
POST /devices/{device_id}/targets/{target_id}/probe
POST /devices/{device_id}/targets/{target_id}/explore
POST /devices/{device_id}/targets/{target_id}/explore/{capability_id}
GET /devices/{device_id}/targets/{target_id}/capabilities
GET /devices/{device_id}/targets/{target_id}/capabilities/{capability_id}
```

Target URLs use the generated `target_id`. The target's `name` (for example,
`samsung.remote.control`) selects the implementation internally, and `display_name`
remains its human-readable label. Nested routes validate that the target belongs
to the requested device.

POST `/probe` checks whether the target interface responds. POST `/explore` runs
all registered read-only capability probes; POST `/explore/{capability_id}` runs
only the explicitly requested capability. Neither exploration endpoint needs a
request body. Bulk exploration skips state-changing and destructive probes;
individual exploration explicitly selects a probe and may run an active one.

GET `/capabilities` and GET `/capabilities/{capability_id}` only read stored results
and never contact the device. Single exploration and lookup return the same
capability fields, including `last_checked`, plus `device_id` and `target_id`.
Missing results and unregistered capabilities return 404; exploring a target
without a registered explorer returns 422. Capability results are held in memory
and must be explored again after restarting the service.

For Samsung remote control targets, use `apps.list` or `remote.connection` as the
capability ID. The Samsung event `ed.installedApp.get` is an implementation detail
of `apps.list`, not a capability ID. The former GET target `/probe` is now POST.

`remote.key` (Remote Key) is active (`ProbeSafety.STATE_CHANGE`) and is skipped
by bulk exploration. POST `/explore/remote.key` returns 422 requesting an explicit
command. Test an ordered batch with:

```text
POST /devices/{device_id}/targets/{target_id}/capabilities/remote.key/test
{"keys": ["KEY_HOME", "KEY_RETURN", "KEY_ENTER"]}
```

The former `key` field is replaced by `keys`. Empty lists, blank entries, and
duplicate keys return 422. All unknown keys are listed in a 400 response before
any command is sent. Missing devices, mismatched targets, and unregistered
capabilities return 404. The 30-key code catalogue continues to exclude `KEY_POWER`.

A batch reuses one authenticated Samsung WebSocket and the existing token recovery.
Each requested key is sent once in caller order, with a 400 ms delay between tests
(adjustable via `REMOTE_KEY_DELAY` in the Samsung integration). A command rejection
does not stop later tests; connection failure marks remaining keys as errors without
replaying keys. Connections are closed after the batch.

The response contains `device_id`, `target_id`, `capability_id`, `tested_at`, and
ordered `results`, each with `key`, `status`, `last_checked`, `duration_ms`, and
optional `error`. `supported` means sent without an immediate protocol error, not
verified physical effect. `unsupported` means explicit command rejection; `error`
means the connection, authentication, or send failed. The aggregate capability
is supported if any key succeeds and remains readable via GET `/capabilities/remote.key`
with `matched_event: null`.

`CAPABILITIES_FILE` defaults to `config/capabilities.json`. Version 1 stores latest
command state under `devices[device_id].targets[target_id].capabilities[capability_id].commands[key]`.
Each entry contains the per-key result fields above. Atomic replacement preserves
other keys while retesting replaces only the requested keys. Startup loads this
file without contacting the TV; a missing file means no tests yet. Invalid files
fail loading rather than silently discard saved discoveries. Only detailed command
state is restored; aggregate probe results remain in memory.

GET `/devices/{device_id}/targets/{target_id}/capabilities/remote.key/commands`
combines persisted results with catalogue display names and returns untested keys
as `not_tested` with null timestamps/errors. It never contacts the TV. Discovery
contains no authentication tokens and never writes command data to `devices.json`.
If persistence fails after testing, POST returns 503 explaining that commands were
already tested and must not be automatically retried. No history is retained.

`apps.list` tries `ed.installedApp.get`, then `ed.edenApp.get`, on one authenticated
remote-control WebSocket. Both requests use `ms.channel.emit` with
`params: {"event": "...", "to": "host", "data": ""}`. Each strategy waits up to
five seconds, so two unanswered requests take approximately ten seconds after
authentication. Connection setup has a separate five-second budget, and cleanup
is also bounded. A matching event must contain a valid application list (including
an empty list); direct and nested `data`/`params` envelopes are recognized.

The first successful strategy stops exploration and supplies `matched_event`.
If neither produces a usable response, the result is `no_response`, not
`unsupported`. Request-level Samsung errors allow the alternate strategy to run;
authorization, connection, and protocol failures stop the sequence. If no strategy
succeeds and one reports an explicit error, the result retains that failure.
`ms.application.get` is excluded because it queries a known application ID.

Internal results retain each strategy attempt and up to 100 diagnostic message
records per attempt, plus a count of omitted records. Records contain timestamps,
sanitized event names, up to 32 top-level keys, and event-match flags. DEBUG logs
use the same sanitized metadata; neither raw application payloads nor pairing
tokens are logged. Strategy definitions live in source code, not `devices.json`.

Interactive documentation:

```text
http://localhost:8100/docs
```

## Shared device registry

Stevie Explorer keeps network addresses in a shared, versioned JSON registry instead of source code. `id` is the stable UUID used by targets, lookups, and mutations. `device_name` is a unique readable name, while `display_name` is user-facing and may change. Both Explorer and Stevie should set `DEVICE_REGISTRY_PATH` to the same absolute path.

```json
{
  "version": 1,
  "devices": [
    {
      "id": "193a3333-85b9-59cc-8fac-a38c551040f7",
      "device_name": "samsung_au8000",
      "display_name": "Samsung AU8000",
      "ip_address": "192.168.50.232"
    }
  ]
}
```

Explorer writes this file using fsync and atomic replacement. Its async watcher checks once per second by default, validates the complete file, diffs it against memory, and publishes local `device.created`, `device.updated`, and `device.removed` events. Invalid external edits leave the last valid in-memory registry active. A process does not duplicate its own events because unchanged registry content is ignored.

Targets may retain the legacy `uri`, or reference a device dynamically using `device_id`, `scheme`, `port`, `path`, and `query`. Device-backed targets resolve the current address whenever a connection is made. If an IP changes while a session is connected, Explorer disconnects it and attempts one reconnect using the new address; failures leave the session failed without a reconnect loop.

Update an address without restarting either process:

```bash
curl -X POST \
  http://localhost:8100/devices \
  -H "Content-Type: application/json" \
  -d '{"id":"193a3333-85b9-59cc-8fac-a38c551040f7","ip_address":"<new-ip-address>"}'
```

`POST /devices` updates an existing device by `id` and returns `200`. It also
supports creating a new device with `201` when `device_name` and `display_name`
are included and `id` is omitted.

Explorer uses the address immediately. Any other process watching the same registry file detects the atomic replacement and emits the equivalent local event. Services should retain `device_id`, call `device_registry.get(device_id)` when connecting, and reconnect on an IP-changing `device.updated` event.

---

# Running

Install dependencies:

```bash
uv sync
```

Copy the example configuration:

```bash
cp .env.example .env
```

Run the application:

```bash
uv run python -m stevie_explorer.main
```

---

# Testing

Run the test suite:

```bash
uv run pytest
```

Run static analysis:

```bash
uv run ruff check .
```

---

# Sandbox

The `sandbox/` directory is used for experimentation.

Nothing inside the sandbox is considered production code.

It is intended for:

* Protocol experiments
* Device investigations
* Temporary utilities
* Proof-of-concept implementations

Only proven behaviour should move into the production codebase.

---

# Roadmap

## Milestone 1 — Foundation ✅

* Kernel
* Configuration
* Event Bus
* Telemetry
* FastAPI
* Tests

## Milestone 2 — WebSocket Explorer

* WebSocket transport
* Session management
* Raw message sending
* Multi-frame capture
* Response filtering

## Milestone 3 — HTTP Explorer

* HTTP transport
* Request editor
* Response inspection
* Authentication support

## Milestone 4 — Experiments

* Saved experiments
* Replay
* Variables
* Assertions
* Response matching

## Milestone 5 — Discovery

* Network scanning
* TCP discovery
* mDNS
* SSDP
* Service fingerprinting

## Milestone 6 — User Interface

* Browser interface
* Session timeline
* Payload editor
* Message viewer
* Capture browser

## Milestone 7 — Exporters

* Python
* JavaScript
* curl
* Markdown protocol documentation
* Stevie integration starter

## Milestone 8 — Additional Transports

* TCP
* UDP
* Serial
* CAN bus
* MQTT
* Bluetooth
* ADB

---

# Long-Term Vision

Stevie Explorer aims to become a general-purpose protocol exploration platform.

While it was born from the Stevie project, it is intentionally designed to be useful far beyond home media.

Potential targets include:

* Smart TVs
* Media boxes
* Home automation hubs
* IoT devices
* ESP32 projects
* CAN bus systems
* REST APIs
* WebSocket services
* Industrial controllers
* Embedded hardware

Explorer should make understanding unknown systems easier, faster, and reproducible.

---

# License

This project is licensed under the MIT License.
