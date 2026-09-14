from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from pydantic import BaseModel
from uuid import uuid4
from uuid import UUID

from stevie_explorer.identifiers import TransportType

@dataclass(frozen=True, slots=True)
class Target:
    name: str
    display_name: str = field(default="", kw_only=True)
    transport: TransportType | None = None
    uri: str | None = None
    device_id: UUID | None = None
    scheme: str | None = None
    port: int | None = None
    path: str = ""
    query: dict[str, str] = field(default_factory=dict)

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

    def __post_init__(self) -> None:
        if not self.display_name:
            object.__setattr__(self, "display_name", self.name)


class TargetSummaryResponse(BaseModel):
    target_id: str
    name: str
    display_name: str

class TargetListResponse(BaseModel):
    device_id: str
    targets: list[TargetSummaryResponse]
