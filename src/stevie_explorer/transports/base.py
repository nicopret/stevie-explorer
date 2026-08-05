from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable 
from typing import Any

from stevie_explorer.identifiers import TransportType
from stevie_explorer.targets.models import Target
from stevie_explorer.transports.payload import TransportPayload

ErrorHandler = Callable[[Any], Awaitable[None]]
MessageHandler = Callable[[TransportPayload], Awaitable[None]]

class BaseTransport(ABC):
    transport_type: TransportType

    @property
    @abstractmethod
    def connected(self) -> bool:
        pass
    
    @abstractmethod
    async def connect(self, target: Target, on_message: MessageHandler, on_error: ErrorHandler) -> None:
        pass

    @abstractmethod
    async def disconnect(self) -> None:
        pass
    
    @abstractmethod
    async def send(self, payload: TransportPayload) -> None:
        pass
