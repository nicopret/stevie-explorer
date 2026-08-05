from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import uuid4

from stevie_explorer.identifiers import TransportType

@dataclass(frozen=True, slots=True)
class Target:
    name: str
    uri: str
    transport: TransportType

    target_id: str = field(
        default_factory=lambda: str(uuid4())
    )

    headers: dict[str, str] = field(
        default_factory=dict
    )

    tags: tuple[str, ...] = ()

    created_at: datetime = field(
        default_factory=lambda: datetime.now(UTC)
    )
