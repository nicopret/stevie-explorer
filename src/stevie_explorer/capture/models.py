from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import uuid4

from stevie_explorer.sessions.models import CapturedMessage

@dataclass(slots=True)
class Capture:
    session_id: str
    start_index: int

    capture_id: str = field(default_factory=lambda: str(uuid4()))

    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    stopped_at: datetime | None = None

    messages: list[CapturedMessage] = field(default_factory=list)

    @property
    def is_recording(self) -> bool:
        return self.stopped_at is None
