from __future__ import annotations

import asyncio
import signal

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, TypeVar

from stevie_explorer.identifiers import (
    ServiceName,
    TelemetryMessage
)

class BaseComponent(ABC):
    name: str

    @property
    def component_name(self) -> str:
        return str(self.name)

class BaseService(BaseComponent):
    @abstractmethod
    async def start(self) -> None:
        pass
    
    @abstractmethod
    async def stop(self) -> None:
        pass

ComponentType = TypeVar("ComponentType", bound=BaseComponent)

@dataclass
class ExplorerKernel:
    name: str = "stevie-explorer"
    running: bool = False

    _registry: dict[str, BaseComponent] = field(default_factory=dict)
    _services: list[BaseService] = field(default_factory=list)
    _stop_event: asyncio.Event = field(default_factory=asyncio.Event)

    def component_names(self) -> tuple[str, ...]:
        return tuple(self._registry.keys())

    def get(self, name: str | ServiceName) -> BaseComponent:
        return self._registry[str(name)]

    def get_typed(self, name: str | ServiceName, expected_type: type[ComponentType]) -> ComponentType:
        component = self.get(name)

        if not isinstance(component, expected_type):
            raise TypeError(
                f"Component {name} is not {expected_type.__name___}"
            )
        
        return component
        
    def has(self, name: str | ServiceName) -> bool:
        return str(name) in self._registry

    def install_signal_handlers(self) -> None:
        loop = asyncio.get_running_loop()

        for current_signal in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(
                current_signal,
                self.request_stop
            )

    def is_service(self, name: str | ServiceName) -> bool:
        component = self.get(name)

        return any(
            service is component
            for service in self._services
        )

    def register(self, component: BaseComponent, name: str | None = None) -> None:
        component_name = str(
            name or component.component_name
        )

        if component_name in self._registry:
            raise ValueError(
                f"Component already registered: {component_name}"
            )
        
        self._registry[component_name] = component

        if isinstance(component, BaseService):
            self._services.append(component)

    def request_stop(self) -> None:
        self._stop_event.set()

    async def start(self) -> None:
        await self._emit(
            TelemetryMessage.KERNEL_STARTING,
            kernel=self.name
        )

        started_services: list[BaseService] = []

        try: 
            for service in self._services:
                await self._emit(
                    TelemetryMessage.KERNEL_SERVICE_STARTING,
                    service=str(service.name)
                )

                await service.start()
                started_services.append(service)

                await self._emit(
                    TelemetryMessage.KERNEL_SERVICE_STARTED,
                    service=str(service.name)
                )

            self.running = True

            await self._emit(
                TelemetryMessage.KERNEL_STARTED,
                kernel=self.name
            )

        except Exception as exc:
            await self._emit(
                TelemetryMessage.KERNEL_START_FAILED,
                kernel=self.name,
                error=str(exc)
            )

            for service in reversed(started_services):
                try:
                    await service.stop()
                except Exception:
                    pass

            raise

    async def stop(self) -> None:
        if not self.running:
            return
        
        await self._emit(
            TelemetryMessage.KERNEL_STOPPING,
            kernel=self.name
        )

        for service in reversed(self._services):
            try:
                await self._emit(
                    TelemetryMessage.KERNEL_SERVICE_STOPPING,
                    service=str(service.name)
                )

                await service.stop()

                await self._emit(
                    TelemetryMessage.KERNEL_SERVICE_STOPPED,
                    service=str(service.name)
                )
            
            except Exception as exc:
                await self._emit(
                    TelemetryMessage.KERNEL_SERVICE_STOP_FAILED,
                    service=str(service.name),
                    error=str(exc)
                )

        self.running = False
        self._stop_event.set()

        await self._emit(
            TelemetryMessage.KERNEL_STOPPED,
            kernel=self.name
        )
    
    async def wait_until_stoppped(self) -> None:
        await self._stop_event.wait()

    async def _emit(self, message: TelemetryMessage, **context: Any) -> None:
        if not self.has(ServiceName.TELEMETRY):
            return
        
        telemetry = self.get(ServiceName.TELEMETRY)

        await telemetry.emit(
            message,
            source="kernel",
            **context
        )
