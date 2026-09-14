from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING
from urllib.parse import urlencode
from uuid import UUID

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

if TYPE_CHECKING:
    from stevie_explorer.devices import DeviceRegistry


class TargetRegistry(BaseComponent):
    name = ServiceName.TARGET_REGISTRY

    def __init__(self, kernel: ExplorerKernel) -> None:
        self.kernel = kernel
        self._targets: dict[str, Target] = {}
    
    async def create(self, target: Target) -> Target:
        if target.uri is None and target.device_id is None:
            raise ValueError("Target requires uri or device_id")
        if target.target_id in self._targets:
            raise ValueError(
                f"Target already exist: {target.target_id}"
            )
        
        if target.device_id is not None:
            devices: DeviceRegistry = self.kernel.get(ServiceName.DEVICE_REGISTRY)
            await devices.add_target(target)
        else:
            self._targets[target.target_id] = target

        telemetry: TelemetryService = self.kernel.get(
            ServiceName.TELEMETRY
        )

        await telemetry.emit(
            TelemetryMessage.TARGET_CREATED,
            source=self.name,
            target_id=target.target_id,
            target_name=target.name,
            transport=target.transport.value if target.transport else None,
            uri=target.uri,
            device_id=str(target.device_id) if target.device_id else None,
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

    def get_by_device(self, device_id: UUID | str) -> list[Target]:
        return [
            target for target in self._targets.values()
            if str(target.device_id) == str(device_id)
        ]

    def get_by_device_and_name(self, device_id: UUID | str, target_name: str) -> Target:
        for target in self.get_by_device(device_id):
            if target.name == target_name:
                return target
        raise KeyError(f"Unknown target for device {device_id}: {target_name}")

    def get_by_device_and_id(self, device_id: UUID | str, target_id: str) -> Target:
        target = self.get(target_id)
        if str(target.device_id) != str(device_id):
            raise KeyError(f"Unknown target for device {device_id}: {target_id}")
        return target

    async def remove_by_device_and_id(self, device_id: UUID | str, target_id: str) -> Target:
        devices: DeviceRegistry = self.kernel.get(ServiceName.DEVICE_REGISTRY)
        devices.get(device_id)
        target = self.get_by_device_and_id(device_id, target_id)
        return await devices.remove_target(device_id, target.target_id)

    def replace_device_targets(self, targets: tuple[Target, ...]) -> None:
        """Restore the device configuration while preserving standalone targets."""
        incoming = {
            key: target for key, target in self._targets.items()
            if target.device_id is None
        }
        for target in targets:
            if target.device_id is None:
                raise ValueError("Device target requires device_id")
            if target.target_id in incoming:
                raise ValueError(f"Target already exist: {target.target_id}")
            incoming[target.target_id] = target
        self._targets = incoming

    def resolve(self, target_id: str) -> Target:
        target = self.get(target_id)
        if target.transport is None:
            raise ValueError(f"Target transport is not configured: {target_id}")
        if target.device_id is None:
            if target.uri is None:
                raise ValueError("Target requires uri or device_id")
            return target

        devices: DeviceRegistry = self.kernel.get(ServiceName.DEVICE_REGISTRY)
        device = devices.get(target.device_id)
        scheme = target.scheme or ("wss" if target.transport.value == "websocket" else target.transport.value)
        port = f":{target.port}" if target.port is not None else ""
        path = target.path if target.path.startswith("/") or not target.path else f"/{target.path}"
        query = f"?{urlencode(target.query)}" if target.query else ""
        return replace(target, uri=f"{scheme}://{device.ip_address}{port}{path}{query}")
    
    def remove(self, target_id: str) -> None:
        try:
            del self._targets[target_id]
        except KeyError as exc:
            raise KeyError(
                f"Unknown target: {target_id}"
            ) from exc
