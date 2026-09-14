from __future__ import annotations

from ipaddress import IPv4Address, IPv6Address
from uuid import UUID
from datetime import UTC, datetime
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Response, status
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

from stevie_explorer.capabilities import CapabilityProbeService
from stevie_explorer.capabilities.models import CapabilityStatus
from stevie_explorer.capabilities.state import CommandResult
from stevie_explorer.capabilities.registry import UnknownCapabilityError
from stevie_explorer.routes.capabilities import (
    TargetProbeResponse, CapabilityManifestResponse, CapabilityDetailResponse,
    capability_manifest, capability_detail,
)
from stevie_explorer.devices import Device, DeviceDetail, DevicePatch, DeviceRegistry
from stevie_explorer.devices.samsung.client import get_api_v2
from stevie_explorer.identifiers import ServiceName
from stevie_explorer.kernel import ExplorerKernel
from stevie_explorer.targets import Target, TargetListResponse, TargetRegistry, TargetSummaryResponse

class DeviceUpsertRequest(BaseModel):
    id: UUID | None = None
    ip_address: IPv4Address | IPv6Address
    device_name: str | None = Field(default=None, min_length=1, max_length=128, pattern=r"^[a-zA-Z0-9][a-zA-Z0-9._-]*$")
    display_name: str | None = Field(default=None, min_length=1, max_length=256)


class CreateDeviceTargetRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=128)
    display_name: str = Field(min_length=1, max_length=256)


class DeviceResponse(BaseModel):
    id: UUID
    device_name: str
    display_name: str
    ip_address: IPv4Address | IPv6Address


class RemoteKeyTestRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    keys: list[Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=128)]] = Field(min_length=1)

    @field_validator("keys")
    @classmethod
    def unique_keys(cls, keys):
        if len(set(keys)) != len(keys):
            raise ValueError("Duplicate keys are not allowed")
        return keys


class RemoteKeyBatchResponse(BaseModel):
    device_id: str
    target_id: str
    capability_id: str = "remote.key"
    tested_at: datetime
    results: tuple[CommandResult, ...]


class DiscoveredCommandResponse(BaseModel):
    key: str
    display_name: str
    status: CapabilityStatus | Literal["not_tested"] = "not_tested"
    last_checked: datetime | None = None
    duration_ms: float | None = None
    error: str | None = None


class CapabilityCommandsResponse(BaseModel):
    device_id: str
    target_id: str
    capability_id: str
    commands: list[DiscoveredCommandResponse]


def create_router(kernel: ExplorerKernel) -> APIRouter:
    router = APIRouter(prefix="/devices", tags=["devices"])

    @router.get("", response_model=list[DeviceResponse])
    async def list_devices() -> tuple[Device, ...]:
        return _registry(kernel).list()

    @router.get("/{device_id}", response_model=DeviceDetail)
    async def get_device(device_id: UUID) -> DeviceDetail:
        try:
            device = _registry(kernel).get(device_id)

            if device is None:
                raise HTTPException(
                    status_code=404,
                    detail="Device not found"
                )
            
            result = device.model_dump()

            if device.device_name.lower() == "samsung_au8000":
                samsung_data = await get_api_v2(device.ip_address)

                result["api"] = {
                    "samsung": {
                        "v2": samsung_data
                    }
                }

            return result

        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @router.post("", response_model=DeviceResponse)
    async def upsert_device(request: DeviceUpsertRequest, response: Response) -> Device:
        registry = _registry(kernel)
        if request.id is None:
            if request.device_name is None or request.display_name is None:
                raise HTTPException(
                    status_code=422,
                    detail="device_name and display_name are required when creating a device",
                )
            response.status_code = status.HTTP_201_CREATED
            return await registry.create(Device(**request.model_dump()))

        try:
            return await registry.update(
                request.id,
                DevicePatch(display_name=request.display_name, ip_address=request.ip_address),
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @router.delete("/{device_id}", response_model=DeviceResponse)
    async def delete_device(device_id: UUID) -> Device:
        try:
            return await _registry(kernel).remove(device_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    def require_device_target(device_id: UUID, target_id: str) -> tuple[Device, Target]:
        try:
            device = _registry(kernel).get(device_id)
            registry: TargetRegistry = kernel.get(ServiceName.TARGET_REGISTRY)
            return device, registry.get_by_device_and_id(device_id, target_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @router.post("/{device_id}/targets/{target_id}/probe", response_model=TargetProbeResponse)
    async def probe_device_target(device_id: UUID, target_id: str) -> TargetProbeResponse:
        device, target = require_device_target(device_id, target_id)
        service: CapabilityProbeService = kernel.get(ServiceName.CAPABILITY_PROBE)
        result = await service.probe(device, target)
        return TargetProbeResponse(
            device_id=str(device.id), target_id=target.target_id,
            target_name=target.name, display_name=target.display_name,
            status=result.status, checked_at=result.timestamp, duration_ms=result.duration_ms,
            matched_event=result.matched_event, error=result.error,
        )

    @router.post("/{device_id}/targets/{target_id}/explore", response_model=CapabilityManifestResponse)
    async def explore_target(device_id: UUID, target_id: str) -> CapabilityManifestResponse:
        device, target = require_device_target(device_id, target_id)
        service: CapabilityProbeService = kernel.get(ServiceName.CAPABILITY_PROBE)
        try:
            await service.explore(device, target)
        except KeyError as exc:
            raise HTTPException(status_code=422, detail=exc.args[0]) from exc
        return capability_manifest(service, target)

    @router.post("/{device_id}/targets/{target_id}/explore/{capability_id}", response_model=CapabilityDetailResponse)
    async def explore_capability(device_id: UUID, target_id: str, capability_id: str) -> CapabilityDetailResponse:
        device, target = require_device_target(device_id, target_id)
        service: CapabilityProbeService = kernel.get(ServiceName.CAPABILITY_PROBE)
        try:
            result = await service.explore_capability(device, target, capability_id)
        except UnknownCapabilityError as exc:
            raise HTTPException(status_code=404, detail=exc.args[0]) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except KeyError as exc:
            raise HTTPException(status_code=422, detail=exc.args[0]) from exc
        return capability_detail(target, result)

    @router.post("/{device_id}/targets/{target_id}/capabilities/remote.key/test", response_model=RemoteKeyBatchResponse)
    async def test_remote_key(device_id: UUID, target_id: str, request: RemoteKeyTestRequest) -> RemoteKeyBatchResponse:
        device, target = require_device_target(device_id, target_id)
        service: CapabilityProbeService = kernel.get(ServiceName.CAPABILITY_PROBE)
        try:
            results = await service.test_capability_commands(device, target, "remote.key", request.keys)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=exc.args[0]) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        return RemoteKeyBatchResponse(device_id=str(device.id), target_id=target.target_id,
                                      tested_at=datetime.now(UTC), results=results)

    @router.get("/{device_id}/targets/{target_id}/capabilities/remote.key/commands", response_model=CapabilityCommandsResponse)
    async def list_remote_keys(device_id: UUID, target_id: str) -> CapabilityCommandsResponse:
        _, target = require_device_target(device_id, target_id)
        service: CapabilityProbeService = kernel.get(ServiceName.CAPABILITY_PROBE)
        try:
            probe = service.catalogue.get_capability(target.name, "remote.key")
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=exc.args[0]) from exc
        await service.state.load()
        saved = service.state.get_commands(str(device_id), target_id, "remote.key")
        return CapabilityCommandsResponse(
            device_id=str(device_id), target_id=target_id, capability_id="remote.key",
            commands=[DiscoveredCommandResponse(
                display_name=item.display_name,
                **(saved[item.name].model_dump() if item.name in saved else {"key": item.name}),
            ) for item in probe.commands],
        )

    @router.get("/{device_id}/targets/{target_id}/capabilities", response_model=CapabilityManifestResponse)
    async def get_target_capabilities(device_id: UUID, target_id: str) -> CapabilityManifestResponse:
        _, target = require_device_target(device_id, target_id)
        service: CapabilityProbeService = kernel.get(ServiceName.CAPABILITY_PROBE)
        return capability_manifest(service, target)

    @router.get("/{device_id}/targets/{target_id}/capabilities/{capability_id}", response_model=CapabilityDetailResponse)
    async def get_capability(device_id: UUID, target_id: str, capability_id: str) -> CapabilityDetailResponse:
        _, target = require_device_target(device_id, target_id)
        service: CapabilityProbeService = kernel.get(ServiceName.CAPABILITY_PROBE)
        try:
            result = service.get_capability_result(target.target_id, capability_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=exc.args[0]) from exc
        return capability_detail(target, result)

    @router.post("/{device_id}/targets", response_model=TargetSummaryResponse, status_code=201)
    async def create_device_target(device_id: UUID, request: CreateDeviceTargetRequest) -> TargetSummaryResponse:
        try:
            device = _registry(kernel).get(device_id)
            registry: TargetRegistry = kernel.get(ServiceName.TARGET_REGISTRY)
            target = await registry.create(Target(
                name=request.name, display_name=request.display_name, device_id=device.id,
            ))
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return TargetSummaryResponse(
            target_id=target.target_id, name=target.name, display_name=target.display_name,
        )

    @router.delete("/{device_id}/targets/{target_id}", response_model=TargetSummaryResponse)
    async def delete_device_target(device_id: UUID, target_id: str) -> TargetSummaryResponse:
        registry: TargetRegistry = kernel.get(ServiceName.TARGET_REGISTRY)
        try:
            target = await registry.remove_by_device_and_id(device_id, target_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return TargetSummaryResponse(
            target_id=target.target_id, name=target.name, display_name=target.display_name,
        )

    @router.get("/{device_id}/targets", response_model=TargetListResponse)
    async def get_device_targets(device_id: UUID) -> TargetListResponse:
        try:
            _registry(kernel).get(device_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

        registry: TargetRegistry = kernel.get(ServiceName.TARGET_REGISTRY)
        return TargetListResponse(
            device_id=str(device_id),
            targets=[
                TargetSummaryResponse(
                    target_id=target.target_id, name=target.name, display_name=target.display_name,
                )
                for target in registry.get_by_device(device_id)
            ],
        )

    return router


def _registry(kernel: ExplorerKernel) -> DeviceRegistry:
    return kernel.get(ServiceName.DEVICE_REGISTRY)
