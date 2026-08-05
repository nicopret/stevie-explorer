from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from stevie_explorer.identifiers import PayloadType

@dataclass(frozen=True, slots=True)
class TransportPayload:
    payload_type: PayloadType
    value: Any
