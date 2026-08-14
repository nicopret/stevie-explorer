from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import uuid4

from stevie_explorer.identifiers import PayloadType, TransportType

class ProbeMode(StrEnum):
    EXPECT_MATCH = "expect_match"
    EXPECT_RESPONSE = "expect_response"
    SEND_ONLY = "send_only"

class CapabilityStatus(StrEnum):
    CONNECTION_CLOSED = "connection_closed"
    ERROR = "error"
    NO_RESPONSE = "no_response"
    REJECTED = "rejected"
    SUPPORTED = "supported"

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

    probe_instance_id: str = field(default_factory=lambda: str(uuid4))

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

    result_id: str = field(default_factory=lambda: str(uuid4))

    timestamp: datetime = field(default_factory=labda: datetime.now(UTC))
