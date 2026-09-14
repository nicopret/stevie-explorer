from __future__ import annotations

import asyncio
import json
import os
import tempfile

from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, ValidationError, model_validator
from uuid import UUID

from stevie_explorer.devices.models import Device, DevicePatch, DeviceAuthentication
from stevie_explorer.eventbus import EventBus
from stevie_explorer.events import ExplorerEvent
from stevie_explorer.identifiers import ServiceName, TelemetryMessage, Topic
from stevie_explorer.kernel import BaseService, ExplorerKernel
from stevie_explorer.telemetry import TelemetryService
from stevie_explorer.targets import Target, TargetRegistry


class RegistryDocument(BaseModel):
    version: int = Field(default=0, ge=0)
    devices: list[Device] = Field(default_factory=list)

    @model_validator(mode="after")
    def unique_names(self) -> "RegistryDocument":
        names = [device.device_name for device in self.devices]
        if len(names) != len(set(names)):
            raise ValueError("duplicate device_name")
        ids = [device.id for device in self.devices]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate device id")
        target_ids = [target.target_id for device in self.devices for target in device.targets]
        if len(target_ids) != len(set(target_ids)):
            raise ValueError("duplicate target id")
        return self


class DeviceRegistry(BaseService):
    name = ServiceName.DEVICE_REGISTRY

    def __init__(self, kernel: ExplorerKernel, path: Path | str, poll_interval: float = 1.0) -> None:
        self.kernel = kernel
        self.path = Path(path)
        self.poll_interval = poll_interval
        self.version = 0
        self._devices: dict[str, Device] = {}
        self._signature: tuple[int, int] | None = None
        self._watch_task: asyncio.Task[None] | None = None
        self._lock = asyncio.Lock()

    async def start(self) -> None:
        if self.path.exists():
            await self.reload()
        else:
            async with self._lock:
                await self._persist()
        await self._emit_telemetry(TelemetryMessage.DEVICE_REGISTRY_LOADED, count=len(self._devices), version=self.version)
        self._watch_task = asyncio.create_task(self._watch(), name="device-registry-watcher")

    async def stop(self) -> None:
        if self._watch_task is not None:
            self._watch_task.cancel()
            try:
                await self._watch_task
            except asyncio.CancelledError:
                pass
            self._watch_task = None

    def get(self, device_id: UUID | str) -> Device:
        try:
            return self._devices[str(device_id)]
        except (KeyError, ValueError) as exc:
            raise KeyError(f"Unknown device: {device_id}") from exc

    def get_by_name(self, device_name: str) -> Device:
        for device in self._devices.values():
            if device.device_name == device_name:
                return device
        raise KeyError(f"Unknown device name: {device_name}")

    def list(self) -> tuple[Device, ...]:
        return tuple(self._devices.values())

    async def create(self, device: Device) -> Device:
        async with self._lock:
            if str(device.id) in self._devices:
                raise ValueError(f"Device id already exists: {device.id}")
            if any(item.device_name == device.device_name for item in self._devices.values()):
                raise ValueError(f"Device already exists: {device.device_name}")
            before = dict(self._devices)
            self._devices[str(device.id)] = device
            self.version += 1
            try:
                await self._persist()
            except Exception:
                self._devices = before
                self.version -= 1
                raise
        await self._publish(Topic.DEVICE_CREATED, device.id, None, device)
        await self._emit_telemetry(TelemetryMessage.DEVICE_CREATED, device_id=str(device.id), device_name=device.device_name)
        return device

    async def add_target(self, target: Target) -> None:
        async with self._lock:
            current = self.get(target.device_id)
            key = str(current.id)
            self._devices[key] = current.model_copy(update={"targets": (*current.targets, target)})
            self.version += 1
            try:
                await self._persist()
            except Exception:
                self._devices[key] = current
                self.version -= 1
                raise

    async def remove_target(self, device_id: UUID | str, target_id: str) -> Target:
        async with self._lock:
            current = self.get(device_id)
            target = next((item for item in current.targets if item.target_id == target_id), None)
            if target is None:
                raise KeyError(f"Unknown target for device {device_id}: {target_id}")
            key = str(current.id)
            self._devices[key] = current.model_copy(update={
                "targets": tuple(item for item in current.targets if item.target_id != target_id),
            })
            self.version += 1
            try:
                await self._persist()
            except Exception:
                self._devices[key] = current
                self.version -= 1
                raise
        return target

    async def update(self, device_id: UUID | str, patch: DevicePatch) -> Device:
        async with self._lock:
            current = self.get(device_id)
            changes = patch.model_dump(exclude_none=True)
            updated = current.model_copy(update=changes)
            if updated == current:
                return current
            key = str(current.id)
            self._devices[key] = updated
            self.version += 1
            try:
                await self._persist()
            except Exception:
                self._devices[key] = current
                self.version -= 1
                raise
        await self._publish(Topic.DEVICE_UPDATED, current.id, current, updated)
        await self._emit_telemetry(TelemetryMessage.DEVICE_UPDATED, device_id=str(current.id), device_name=current.device_name)
        return updated

    async def update_authentication(
        self, device_id: UUID | str, authentication: DeviceAuthentication,
    ) -> None:
        async with self._lock:
            current = self.get(device_id)
            if current.authentication == authentication:
                return
            key = str(current.id)
            self._devices[key] = current.model_copy(update={"authentication": authentication})
            self.version += 1
            try:
                await self._persist()
            except Exception:
                self._devices[key] = current
                self.version -= 1
                raise

    async def remove(self, device_id: UUID | str) -> Device:
        async with self._lock:
            current = self.get(device_id)
            key = str(current.id)
            del self._devices[key]
            self.version += 1
            try:
                await self._persist()
            except Exception:
                self._devices[key] = current
                self.version -= 1
                raise
        await self._publish(Topic.DEVICE_REMOVED, current.id, current, None)
        await self._emit_telemetry(TelemetryMessage.DEVICE_REMOVED, device_id=str(current.id), device_name=current.device_name)
        return current

    async def reload(self) -> bool:
        async with self._lock:
            try:
                raw = await asyncio.to_thread(self.path.read_text, encoding="utf-8")
                document = RegistryDocument.model_validate_json(raw)
            except (OSError, ValidationError, ValueError, json.JSONDecodeError) as exc:
                await self._emit_telemetry(TelemetryMessage.DEVICE_REGISTRY_RELOAD_FAILED, error=type(exc).__name__)
                return False

            incoming = {str(device.id): device for device in document.devices}
            old = self._devices
            if incoming == old:
                self.version = max(self.version, document.version)
                self._signature = self._file_signature()
                return False
            try:
                self._sync_targets(tuple(target for device in incoming.values() for target in device.targets))
            except ValueError as exc:
                await self._emit_telemetry(TelemetryMessage.DEVICE_REGISTRY_RELOAD_FAILED, error=type(exc).__name__)
                return False
            self._devices = incoming
            self.version = document.version
            self._signature = self._file_signature()

        await self._emit_telemetry(TelemetryMessage.DEVICE_REGISTRY_RELOAD_DETECTED, version=self.version)
        for name in old.keys() - incoming.keys():
            await self._publish(Topic.DEVICE_REMOVED, name, old[name], None)
        for name in incoming.keys() - old.keys():
            await self._publish(Topic.DEVICE_CREATED, name, None, incoming[name])
        for name in old.keys() & incoming.keys():
            if old[name] != incoming[name]:
                await self._publish(Topic.DEVICE_UPDATED, name, old[name], incoming[name])
        return True

    async def _watch(self) -> None:
        while True:
            await asyncio.sleep(self.poll_interval)
            signature = self._file_signature()
            if signature is not None and signature != self._signature:
                await self.reload()

    def _file_signature(self) -> tuple[int, int] | None:
        try:
            stat = self.path.stat()
            return stat.st_mtime_ns, stat.st_size
        except FileNotFoundError:
            return None

    async def _persist(self) -> None:
        document = RegistryDocument(version=self.version, devices=list(self._devices.values()))
        data = document.model_dump(mode="json")
        for record, device in zip(data["devices"], document.devices):
            if device.authentication is not None:
                auth = device.authentication.model_dump(mode="json")
                samsung = device.authentication.samsung
                if samsung is not None and samsung.token is not None:
                    auth["samsung"]["token"] = samsung.token.get_secret_value()
                record["authentication"] = auth
        content = json.dumps(data, indent=2) + "\n"
        registry: TargetRegistry | None = (
            self.kernel.get(ServiceName.TARGET_REGISTRY)
            if self.kernel.has(ServiceName.TARGET_REGISTRY) else None
        )
        previous = tuple(target for target in registry.list() if target.device_id is not None) if registry else ()
        self._sync_targets(tuple(target for device in document.devices for target in device.targets))
        try:
            await asyncio.to_thread(self._atomic_write, content)
        except Exception:
            if registry is not None:
                registry.replace_device_targets(previous)
            raise
        self._signature = self._file_signature()

    def _sync_targets(self, targets: tuple[Target, ...]) -> None:
        if self.kernel.has(ServiceName.TARGET_REGISTRY):
            registry: TargetRegistry = self.kernel.get(ServiceName.TARGET_REGISTRY)
            registry.replace_device_targets(targets)

    def _atomic_write(self, content: str) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(prefix=f".{self.path.name}.", dir=self.path.parent)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
            directory_fd = os.open(self.path.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    async def _publish(self, topic: Topic, device_id: UUID | str, before: Device | None, after: Device | None) -> None:
        eventbus: EventBus = self.kernel.get(ServiceName.EVENTBUS)
        payload: dict[str, Any] = {
            "device_id": str(device_id),
            "device_name": (after or before).device_name,
            "before": before.model_dump(mode="json") if before else None,
            "after": after.model_dump(mode="json") if after else None,
            "version": self.version,
        }
        await eventbus.publish_sync(ExplorerEvent(topic=topic, source=str(self.name), payload=payload))

    async def _emit_telemetry(self, message: TelemetryMessage, **context: Any) -> None:
        if self.kernel.has(ServiceName.TELEMETRY):
            telemetry: TelemetryService = self.kernel.get(ServiceName.TELEMETRY)
            await telemetry.emit(message, source=self.name, **context)
