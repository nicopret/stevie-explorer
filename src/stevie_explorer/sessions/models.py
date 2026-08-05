from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from stevie_explorer.identifiers import (
    MessageDirection,
    PayloadType,
    SessionState
)

@dataclass(frozen=True, slots=True)
class CapturedMessage:
    session_id: str
    direction: MessageDirection
    payload_type: PayloadType
    payload: Any

    message_id: str = field(
        default_factory=lambda: datetime.now(UTC)
    )

@dataclass(slots=True)
class ExplorerSession:
    target_id: str

    session_id: str = field(
        default_factory=lambda: str(uuid4())
    )

    state: SessionState = SessionState.CREATED

    messages: list[CapturedMessage] = field(
        default_factory=list
    )

    error: str | None = None

    created_at: datetime = field(
        default_factory=lambda: datetime.now(UTC)
    )

    connected_at: datetime | None = None
    disconnected_at: datetime | None = None
