from __future__ import annotations

from stevie_explorer.identifiers import (
    ServiceName,
    TelemetryMessage
)
from stevie_explorer.kernel import (
    BaseComponent,
    ExplorerKernel
)
from stevie_explorer.targets.models import Target
from stevie_explorer.telemetry import TelemetryService

class TargetRegistry(BaseComponent):
    name = ServiceName.TARGET_REGISTRY

    def __init__(self, kernel: ExplorerKernel) -> None:
        self.kernel = kernel
        self._targets: dict[str, Target] = {}
    
    async def create(self, target: Target) -> Target:
        if target.target_id in self._targets:
            raise ValueError(
                f"Target already exist: {target.target_id}"
            )
        
        self._targets[target.target_id] = target

        telemetry: TelemetryService = self.kernel.get(
            ServiceName.TELEMETRY
        )

        await telemetry.emit(
            TelemetryMessage.TARGET_CREATED,
            source=self.name,
            target_id=target.target_id,
            target_name=target.name,
            transport=target.transport.value,
            uri=target.uri
        )

        return target

    def get(self, target_id: str) -> Target:
        try:
            return self._targets[target_id]
        except KeyError as exc:
            raise KeyError(
                f"Unknown target: {target_id}"
            ) from exc
        
    def list(self) -> tuple[Target, ...]:
        return tuple(self._targets.values())
    
    def remove(self, target_id: str) -> None:
        try:
            del self._targets[target_id]
        except KeyError as exc:
            raise KeyError(
                f"Unknown target: {target_id}"
            ) from exc
