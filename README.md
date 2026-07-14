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
```

Interactive documentation:

```text
http://localhost:8100/docs
```

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
