from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from stevie_explorer.identifiers import PayloadType, TransportType

if TYPE_CHECKING:
    from stevie_explorer.capabilities.state import CommandResult
    from stevie_explorer.devices import Device
    from stevie_explorer.devices.models import DeviceAuthentication
    from stevie_explorer.targets import Target

class ProbeMode(StrEnum):
    EXPECT_MATCH = "expect_match"
    EXPECT_RESPONSE = "expect_response"
    SEND_ONLY = "send_only"

class CapabilityStatus(StrEnum):
    CONNECTION_CLOSED = "connection_closed"
    ERROR = "error"
    NO_MATCH = "no_match"
    NO_RESPONSE = "no_response"
    REJECTED = "rejected"
    SUPPORTED = "supported"
    UNSUPPORTED = "unsupported"

class ProbeSafety(StrEnum):
    READ_ONLY = "read_only"
    STATE_CHANGE = "state_change"
    DESTRUCTIVE = "destructive"


@dataclass(frozen=True, slots=True)
class ProbeStrategy:
    strategy_id: str
    payload: Any
    response_matcher: Callable[[dict], bool]
    expected_event: str
    timeout: float = 5.0


@dataclass(frozen=True, slots=True)
class StrategyDiagnostic:
    event: str | None
    message_keys: tuple[str, ...]
    matched_event: bool
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass(frozen=True, slots=True)
class StrategyAttempt:
    strategy_id: str
    status: CapabilityStatus
    matched_event: str | None = None
    error: str | None = None
    diagnostics: tuple[StrategyDiagnostic, ...] = ()
    dropped_diagnostics: int = 0
    connection_failed: bool = False

@dataclass(frozen=True, slots=True)
class CapabilityCommand:
    name: str
    display_name: str


@dataclass(frozen=True, slots=True)
class CapabilityProbe:
    probe_id: str
    name: str
    transport: TransportType
    payload_type: PayloadType
    payload: Any

    mode: ProbeMode = ProbeMode.EXPECT_RESPONSE

    timeout: float = 5.0

    expected_event: str | None = None
    description: str | None = None
    tags: tuple[str, ...] = ()
    safety: ProbeSafety = ProbeSafety.READ_ONLY

    # Existence probes can perform a handshake without sending a command payload.
    runner: Callable[[Device, Target, CapabilityProbe, Callable[[DeviceAuthentication], Awaitable[None]]], Awaitable[CapabilityResult]] | None = None

    strategies: tuple[ProbeStrategy, ...] = ()
    commands: tuple[CapabilityCommand, ...] = ()
    command: str | None = None
    batch_runner: Callable[[Device, Target, CapabilityProbe, Callable[[DeviceAuthentication], Awaitable[None]], list[str]], Awaitable[tuple[CommandResult, ...]]] | None = None

    probe_instance_id: str = field(default_factory=lambda: str(uuid4()))

@dataclass(frozen=True, slots=True)
class CapabilityResult:
    target_id: str
    probe_id: str
    probe_name: str
    status: CapabilityStatus

    duration_ms: float
    
    response: Any | None = None
    error: str | None = None
    matched_event: str | None = None

    matched_strategy: str | None = None
    strategy_attempts: tuple[StrategyAttempt, ...] = ()

    captures: tuple[ProbeCapture, ...] = ()

    result_id: str = field(default_factory=lambda: str(uuid4()))

    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))

@dataclass(frozen=True, slots=True)
class ProbeCapture:
    payload_type: PayloadType
    payload: Any

    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))
