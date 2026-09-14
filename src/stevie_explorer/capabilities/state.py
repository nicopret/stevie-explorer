"""Latest command discovery state, separate from device configuration and history."""
from __future__ import annotations

import asyncio
import json
import os
import tempfile
from datetime import datetime
from pathlib import Path

from pydantic import BaseModel, Field

from stevie_explorer.capabilities.models import CapabilityStatus


class CommandResult(BaseModel):
    key: str
    status: CapabilityStatus
    last_checked: datetime
    duration_ms: float
    error: str | None = None


class CapabilityState(BaseModel):
    commands: dict[str, CommandResult] = Field(default_factory=dict)


class TargetState(BaseModel):
    capabilities: dict[str, CapabilityState] = Field(default_factory=dict)


class DeviceState(BaseModel):
    targets: dict[str, TargetState] = Field(default_factory=dict)


class StateDocument(BaseModel):
    version: int = 1
    devices: dict[str, DeviceState] = Field(default_factory=dict)


class CapabilityStateRepository:
    def __init__(self, path: Path):
        self.path = path
        self.document = StateDocument()
        self._lock = asyncio.Lock()
        self._loaded = False

    async def load(self):
        async with self._lock:
            if not self._loaded:
                try:
                    content = await asyncio.to_thread(self.path.read_text, encoding='utf-8')
                except FileNotFoundError:
                    pass
                else:
                    # Fail explicitly on invalid state rather than overwrite discoveries.
                    self.document = StateDocument.model_validate_json(content)
                self._loaded = True

    def get_commands(self, device_id: str, target_id: str, capability_id: str):
        device = self.document.devices.get(str(device_id), DeviceState())
        target = device.targets.get(target_id, TargetState())
        return target.capabilities.get(capability_id, CapabilityState()).commands.copy()

    async def save_commands(self, device_id: str, target_id: str, capability_id: str, results: tuple[CommandResult, ...]):
        await self.load()
        async with self._lock:
            updated = self.document.model_copy(deep=True)
            device = updated.devices.setdefault(str(device_id), DeviceState())
            target = device.targets.setdefault(target_id, TargetState())
            capability = target.capabilities.setdefault(capability_id, CapabilityState())
            capability.commands.update({result.key: result for result in results})
            content = json.dumps(updated.model_dump(mode='json'), indent=2) + '\n'
            # Finish the atomic write before releasing the lock even on cancellation.
            write = asyncio.create_task(asyncio.to_thread(self._atomic_write, content))
            try:
                await asyncio.shield(write)
            except asyncio.CancelledError:
                await write
                self.document = updated
                raise
            self.document = updated

    def _atomic_write(self, content: str):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(prefix=f'.{self.path.name}.', dir=self.path.parent)
        try:
            with os.fdopen(descriptor, 'w', encoding='utf-8') as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
