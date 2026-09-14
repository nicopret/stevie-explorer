from __future__ import annotations

from base64 import b64encode
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from stevie_explorer.capabilities import CapabilityProbeService, CapabilityResult, CapabilityStatus
from stevie_explorer.capabilities.probes import PROBE_REGISTRY
from stevie_explorer.identifiers import ServiceName, TelemetryMessage
from stevie_explorer.kernel import ExplorerKernel
from stevie_explorer.targets import TargetRegistry, Target


class CaptureResponse(BaseModel):
    payload_type: str
    payload: Any
    timestamp: datetime


class ProbeResultResponse(BaseModel):
    result_id: str
    target_id: str
    probe_id: str
    probe_name: str
    status: CapabilityStatus
    duration_ms: float
    response: Any | None = None
    error: str | None = None
    matched_event: str | None = None
    captures: list[CaptureResponse]


class CapabilityResponse(BaseModel):
    capability_id: str
    name: str
    status: CapabilityStatus
    last_checked: datetime
    duration_ms: float
    matched_event: str | None
    error: str | None


class TargetProbeResponse(BaseModel):
    device_id: str
    target_id: str
    target_name: str
    display_name: str
    status: CapabilityStatus
    checked_at: datetime
    duration_ms: float
    matched_event: str | None
    error: str | None


class CapabilityDetailResponse(CapabilityResponse):
    device_id: str
    target_id: str


class CapabilityManifestResponse(BaseModel):
    target_id: str
    device_id: str | None = None
    target_name: str | None = None
    generated_at: datetime
    capabilities: list[CapabilityResponse]


class ProbePackResponse(BaseModel):
    target_id: str
    pack_id: str
    results: list[ProbeResultResponse]


def _json_safe(value: Any) -> Any:
    if isinstance(value, bytes):
        return b64encode(value).decode("ascii")
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return value


def _probe_response(result: Any) -> ProbeResultResponse:
    return ProbeResultResponse(
        result_id=result.result_id,
        target_id=result.target_id,
        probe_id=result.probe_id,
        probe_name=result.probe_name,
        status=result.status,
        duration_ms=result.duration_ms,
        response=_json_safe(result.response),
        error=result.error,
        matched_event=result.matched_event,
        captures=[
            CaptureResponse(
                payload_type=capture.payload_type.value,
                payload=_json_safe(capture.payload),
                timestamp=capture.timestamp,
            )
            for capture in result.captures
        ],
    )


def create_capabilities_router(kernel: ExplorerKernel) -> APIRouter:
    router = APIRouter(prefix="/targets", tags=["capabilities"])

    def require_target(target_id: str) -> Target:
        targets: TargetRegistry = kernel.get(ServiceName.TARGET_REGISTRY)  # type: ignore[assignment]
        try:
            return targets.get(target_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @router.get("/{target_id}/capabilities", response_model=CapabilityManifestResponse)
    async def get_capabilities(target_id: str) -> CapabilityManifestResponse:
        target: Target = require_target(target_id)
        service: CapabilityProbeService = kernel.get(ServiceName.CAPABILITY_PROBE)  # type: ignore[assignment]
        return capability_manifest(service, target)

    @router.post(
        "/{target_id}/capabilities/probes/{probe_id}",
        response_model=ProbeResultResponse,
    )
    async def run_probe(target_id: str, probe_id: str) -> ProbeResultResponse:
        require_target(target_id)
        try:
            probe = PROBE_REGISTRY.get(probe_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        service: CapabilityProbeService = kernel.get(ServiceName.CAPABILITY_PROBE)  # type: ignore[assignment]
        result = await service.run(target_id=target_id, probe=probe)
        if result.status == CapabilityStatus.ERROR:
            raise HTTPException(status_code=502, detail=result.error or "Probe failed")
        return _probe_response(result)

    @router.post(
        "/{target_id}/capabilities/probe-packs/{pack_id}",
        response_model=ProbePackResponse,
    )
    async def run_probe_pack(target_id: str, pack_id: str) -> ProbePackResponse:
        require_target(target_id)
        try:
            pack = PROBE_REGISTRY.get_pack(pack_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        service: CapabilityProbeService = kernel.get(ServiceName.CAPABILITY_PROBE)  # type: ignore[assignment]
        telemetry = kernel.get(ServiceName.TELEMETRY) if kernel.has(ServiceName.TELEMETRY) else None
        if telemetry is not None:
            await telemetry.emit(
                TelemetryMessage.PROBE_PACK_STARTED,
                source=service.name, target_id=target_id, pack_id=pack_id,
            )
        results = [
            await service.run(target_id, PROBE_REGISTRY.get(probe_id))
            for probe_id in pack.probe_ids
        ]
        if telemetry is not None:
            await telemetry.emit(
                TelemetryMessage.PROBE_PACK_COMPLETED,
                source=service.name, target_id=target_id, pack_id=pack_id,
                result_count=len(results),
            )
        return ProbePackResponse(
            target_id=target_id,
            pack_id=pack_id,
            results=[_probe_response(result) for result in results],
        )

    return router


def capability_response(result: CapabilityResult) -> CapabilityResponse:
    return CapabilityResponse(
        capability_id=result.probe_id,
        name=result.probe_name,
        status=result.status,
        last_checked=result.timestamp,
        duration_ms=result.duration_ms,
        matched_event=result.matched_event,
        error=result.error,
    )


def capability_detail(target: Target, result: CapabilityResult) -> CapabilityDetailResponse:
    return CapabilityDetailResponse(
        device_id=str(target.device_id), target_id=target.target_id,
        **capability_response(result).model_dump(),
    )


def capability_manifest(service: CapabilityProbeService, target: Target) -> CapabilityManifestResponse:
    return CapabilityManifestResponse(
        target_id=target.target_id,
        device_id=str(target.device_id) if target.device_id else None,
        target_name=target.name,
        generated_at=datetime.now(UTC),
        capabilities=[
            capability_response(result)
            for result in service.get_results(target.target_id)
        ],
    )
