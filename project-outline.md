# Stevie Explorer

Stevie Explorer is a standalone protocol discovery and device investigation platform.

It is designed to help developers inspect, test, document, and reverse-engineer devices and network services without adding experimental tooling to Stevie Core.

Stevie Explorer can be used with televisions, media boxes, smart-home hardware, CAN bus devices, HTTP APIs, WebSocket services, TCP devices, UDP discovery protocols, serial devices, and other network-connected systems.

---

## Core Purpose

Stevie Explorer should answer questions such as:

* What services does this device expose?
* Which ports are open?
* What protocol is running on a port?
* What requests does the device accept?
* What responses or unsolicited events does it emit?
* Does it require authentication or pairing?
* Can commands be replayed?
* Can a successful experiment be exported as reusable code?
* Can discoveries be documented and shared?

Stevie Explorer discovers capabilities.

Stevie Core consumes proven capabilities.

---

# Project Boundary

## Stevie Explorer is responsible for

* Network and protocol exploration
* Raw request and response inspection
* WebSocket experimentation
* HTTP endpoint testing
* TCP and UDP communication
* Device discovery
* Protocol recording
* Response filtering
* Message replay
* Session management
* Experiment history
* Exporting discoveries
* Generating starter integration code
* Producing documentation for known devices

## Stevie Explorer is not responsible for

* Running household automations
* Media orchestration
* Long-term device control
* Home Assistant integration
* CAN bus orchestration
* Streaming-provider scraping
* Media scheduling
* User-facing playback workflows

Those remain responsibilities of Stevie Core and its clients.

---

# Proposed Repository

```text
stevie-explorer/
├── pyproject.toml
├── uv.lock
├── README.md
├── .env.example
├── .gitignore
├── src/
│   └── stevie_explorer/
│       ├── __init__.py
│       ├── main.py
│       ├── config.py
│       ├── kernel.py
│       ├── eventbus.py
│       ├── telemetry.py
│       ├── api.py
│       ├── sessions/
│       ├── targets/
│       ├── transports/
│       ├── discovery/
│       ├── captures/
│       ├── experiments/
│       ├── filters/
│       ├── exporters/
│       ├── storage/
│       └── routes/
├── sandbox/
├── tests/
└── data/
```

Folders should still be created only when needed.

---

# Initial Technology Stack

```text
Python 3.13+
uv
FastAPI
Uvicorn
Pydantic
Pydantic Settings
Structlog
websockets
httpx
SQLite
pytest
ruff
mypy
```

Later additions may include:

```text
python-can
pyserial
scapy
zeroconf
asyncssh
```

Those should only be added when the relevant transport or discovery feature is implemented.

---

# High-Level Architecture

```text
                       Clients
              ┌──────────┴──────────┐
              │                     │
         Web Interface          REST API
              │                     │
              └──────────┬──────────┘
                         │
               Stevie Explorer Kernel
                         │
      ┌──────────────────┼──────────────────┐
      │                  │                  │
  Target Registry    Session Manager    Telemetry
      │                  │                  │
      └──────────────────┼──────────────────┘
                         │
                  Transport Registry
                         │
       ┌─────────┬───────┼───────┬─────────┐
       │         │       │       │         │
   WebSocket    HTTP    TCP     UDP      Serial
```

---

# Core Concepts

## Target

A target represents something being explored.

Examples:

```text
Samsung living-room TV
EE media box
ESP32 CAN gateway
Home Assistant server
Unknown device at 192.168.50.44
```

A target stores:

* Stable target ID
* Display name
* Hostname or IP address
* Known ports
* Authentication information
* Transport configuration
* Tags
* Notes
* Discovered capabilities

Example:

```json
{
  "id": "living_room_tv",
  "name": "Samsung AU8000",
  "host": "192.168.50.232",
  "tags": ["television", "samsung", "tizen"]
}
```

---

## Transport

A transport knows how to communicate.

Initial transports:

```text
WebSocket
HTTP
TCP
UDP
```

Future transports:

```text
Serial
CAN bus
MQTT
Bluetooth
SSH
ADB
UPnP
```

Each transport should expose a common interface:

```python
class Transport:
    async def connect(self) -> None:
        ...

    async def send(self, request) -> None:
        ...

    async def receive(self):
        ...

    async def close(self) -> None:
        ...
```

---

## Session

A session represents an active investigation.

A session contains:

* Target
* Transport
* Connection state
* Authentication state
* Sent messages
* Received messages
* Timestamps
* Errors
* Notes
* Filters
* Saved experiments

Example:

```text
Session: Samsung App Discovery
Target: Samsung AU8000
Transport: Secure WebSocket
Started: 20:15
Messages sent: 4
Messages received: 9
```

---

## Message

Every sent or received message should use a common envelope:

```json
{
  "id": "message-uuid",
  "direction": "outbound",
  "transport": "websocket",
  "target_id": "living_room_tv",
  "timestamp": "2026-07-10T20:15:00Z",
  "payload_type": "json",
  "payload": {
    "method": "ms.channel.emit"
  }
}
```

Supported payload formats should eventually include:

```text
JSON
Text
Binary
Hex
Base64
Form data
HTTP headers/body
CAN frames
```

---

## Experiment

An experiment is a reusable sequence of steps.

Example:

```text
Connect to Samsung remote WebSocket
Wait for handshake
Send ed.installedApp.get
Capture five responses
Filter for ed.installedApp.get
```

An experiment should support:

* Named steps
* Delays
* Send operations
* Wait operations
* Response matching
* Assertions
* Variables
* Extracted values
* Retries
* Notes

Example concept:

```yaml
name: samsung-installed-apps
target: living_room_tv

steps:
  - connect:
      transport: websocket

  - wait_for:
      event: ms.channel.connect

  - send:
      payload:
        method: ms.channel.emit
        params:
          event: ed.installedApp.get
          to: host

  - wait_for:
      event: ed.installedApp.get
      timeout: 10
```

---

# Transport Registry

The explorer should use a transport registry rather than hard-coded transport selection.

```python
registry.register(WebSocketTransport())
registry.register(HttpTransport())
registry.register(TcpTransport())
```

Execution:

```python
transport = registry.get(TransportType.WEBSOCKET)
```

This keeps the core independent of individual protocols.

---

# Target Adapters

Some devices require more than a generic transport.

For example, Samsung needs:

* App-name encoding
* Token persistence
* Pairing
* WebSocket URL construction
* Initial handshake handling

That logic belongs in an optional target adapter:

```text
Generic WebSocket Transport
          │
          ▼
Samsung Tizen Adapter
```

The generic transport remains reusable, while the adapter handles device-specific behaviour.

Initial adapter candidates:

```text
Samsung Tizen
Android TV / ADB
Samsung SmartThings
EE media box
Home Assistant
ESP32 CAN gateway
```

Adapters should not contain long-term automation logic. They only simplify exploration.

---

# Discovery Modules

Discovery should be separate from transports.

Initial discovery modules could include:

```text
Network host discovery
TCP port discovery
mDNS / Zeroconf discovery
SSDP / UPnP discovery
HTTP service identification
TLS certificate inspection
WebSocket endpoint probing
```

Later:

```text
Bluetooth discovery
CAN node discovery
MQTT broker discovery
ADB device discovery
```

The discovery layer should create or enrich targets.

---

# Capture System

The capture system records everything sent and received during a session.

It should support:

* Timestamped messages
* Raw payload storage
* Parsed payload storage
* Binary payloads
* Connection events
* Errors
* Search
* Filtering
* Export

Captures should be stored in SQLite initially.

Large binary captures can later be stored in files with references in the database.

---

# Filters and Matchers

Filters help isolate meaningful responses.

Examples:

```text
Event name equals ed.installedApp.get
JSON path exists: data.data
Payload contains Netflix
Direction is inbound
Transport is WebSocket
HTTP status is 200
CAN arbitration ID equals 0x180
```

Example API representation:

```json
{
  "field": "event",
  "operator": "equals",
  "value": "ed.installedApp.get"
}
```

---

# Replay

Any captured request should be replayable.

Replay options:

* Send once
* Repeat a fixed number of times
* Delay between sends
* Change selected fields
* Replay against another target
* Compare responses

This will be particularly useful for:

* Samsung commands
* EE box experiments
* CAN messages
* HTTP APIs
* WebSocket authentication flows

---

# Variables and Secrets

Experiments should support variables:

```text
${target.ip}
${target.token}
${session.handshake_token}
${env.SAMSUNG_PASSWORD}
```

Secrets must never be written into Git or exported captures by default.

Store secrets in:

```text
.env
local encrypted secret storage later
```

Exports should redact known secret fields.

---

# API Design

Initial endpoints:

```text
GET    /health
GET    /targets
POST   /targets
GET    /targets/{target_id}

GET    /transports
POST   /sessions
GET    /sessions/{session_id}
DELETE /sessions/{session_id}

POST   /sessions/{session_id}/send
GET    /sessions/{session_id}/messages

POST   /experiments
POST   /experiments/{experiment_id}/run

GET    /captures
GET    /captures/{capture_id}
POST   /captures/{capture_id}/replay
```

Development documentation:

```text
/docs
```

---

# Web Interface

The first interface can be FastAPI documentation, but Stevie Explorer will eventually benefit from a dedicated browser UI.

Suggested layout:

```text
┌──────────────────────────────────────────────────────┐
│ Target: Samsung AU8000        Connected: Yes         │
├───────────────────┬──────────────────────────────────┤
│ Targets           │ Session messages                 │
│                   │                                  │
│ Samsung TV        │ 20:10 OUT ms.channel.emit        │
│ EE Box            │ 20:10 IN  ms.channel.connect     │
│ ESP32 Gateway     │ 20:11 IN  ed.apps.launch         │
│                   │                                  │
├───────────────────┼──────────────────────────────────┤
│ Experiments       │ Payload editor                   │
│                   │                                  │
│ App discovery     │ {                                │
│ Launch Netflix    │   "method": "..."                │
│ Query status      │ }                                │
└───────────────────┴──────────────────────────────────┘
```

Useful UI features:

* JSON editor
* Raw text editor
* Hex viewer
* Send button
* Response timeline
* Search
* Filters
* Saved experiments
* Diff two responses
* Export code

---

# Exporters

One of the most valuable features will be converting a successful experiment into reusable code.

Initial exporters:

```text
Python async function
curl command
JavaScript fetch
JavaScript WebSocket
JSON experiment file
Markdown protocol documentation
```

Future exporters:

```text
Stevie device-service skeleton
Home Assistant integration starter
CAN message definition
OpenAPI fragment
Postman collection
```

Example generated Python:

```python
async def launch_samsung_app(
    websocket,
    app_id: str,
) -> None:
    payload = {
        "method": "ms.channel.emit",
        "params": {
            "event": "ed.apps.launch",
            "to": "host",
            "data": {
                "appId": app_id,
                "action_type": "NATIVE_LAUNCH",
                "metaTag": "",
            },
        },
    }

    await websocket.send(json.dumps(payload))
```

---

# Stevie Core Integration

Stevie Explorer and Stevie Core should remain independent repositories.

The relationship should be:

```text
Stevie Explorer
      │
      │ exports a tested protocol definition
      ▼
Stevie Core
      │
      │ implements a production driver
      ▼
Home media orchestration
```

Possible export:

```yaml
device: samsung_au8000

commands:
  launch_app:
    transport: websocket
    request:
      method: ms.channel.emit
      params:
        event: ed.apps.launch
```

Stevie Core can later consume this manually or through generated code.

Stevie Core should never depend on Stevie Explorer at runtime.

---

# Security

The explorer is powerful and potentially dangerous.

It should:

* Be disabled by default
* Bind to localhost by default
* Require authentication before LAN access
* Restrict target networks
* Prevent access to arbitrary internet hosts by default
* Redact tokens and passwords
* Record who ran an experiment
* Log dangerous operations
* Require confirmation for broadcast or repeated sends
* Limit message sizes
* Limit concurrent sessions
* Limit retries and send rates

It should never be exposed directly to the internet.

---

# Telemetry

Stevie Explorer can reuse the same telemetry philosophy as Stevie Core:

```text
Level
Category
Message definition
Context
Multiple outputs
```

Suggested categories:

```text
SYSTEM
API
SESSION
TRANSPORT
DISCOVERY
CAPTURE
EXPERIMENT
SECURITY
EXPORT
```

Future outputs:

```text
Local JSON logs
Grafana / Loki
Critical mobile notifications
Audit logs
```

---

# Initial Milestones

## Milestone 1 — Project Foundation

* uv project
* Kernel
* Component registry
* Service lifecycle
* Configuration
* Event bus
* Telemetry
* FastAPI
* Health endpoint

## Milestone 2 — WebSocket Explorer

* Target model
* WebSocket transport
* Session model
* Send JSON/text payload
* Receive multiple frames
* Timeouts
* Capture messages
* Samsung WebSocket experiment

## Milestone 3 — HTTP Explorer

* HTTP transport
* Headers
* Query parameters
* Request bodies
* Response inspection
* Authentication options

## Milestone 4 — Experiments

* Saved experiments
* Ordered steps
* Variables
* Response matchers
* Assertions
* Replay

## Milestone 5 — Discovery

* Host discovery
* Port discovery
* mDNS
* SSDP
* Basic service fingerprinting

## Milestone 6 — Web Interface

* Target selector
* Session timeline
* Payload editor
* Response viewer
* Capture search
* Experiment runner

## Milestone 7 — Export

* Python exporter
* JavaScript exporter
* curl exporter
* Markdown documentation exporter
* Stevie driver starter exporter

## Milestone 8 — Additional Transports

* TCP
* UDP
* Serial
* CAN bus
* MQTT
* ADB

---

# First Practical Use Case

The first full experiment should be the Samsung AU8000.

Objectives:

* Connect to the authenticated remote WebSocket
* Capture handshake frames
* Send remote commands
* Test EDEN commands
* Launch apps
* Query app state
* Probe installed-app discovery
* Record every response
* Export working commands
* Document unsupported commands

That gives Stevie Explorer a real target while keeping the architecture device-independent.

---

# Suggested Repository Name

```text
stevie-explorer
```

Suggested description:

> A protocol discovery, device investigation, and integration development platform.

Suggested first branch:

```text
feature/project-foundation
```

Suggested initial commit:

```text
feat: initialise Stevie Explorer project
```

---

# Guiding Principle

> Explore freely, capture everything, promote only proven behaviour.

Stevie Explorer is the laboratory.

Stevie Core is the production system.
