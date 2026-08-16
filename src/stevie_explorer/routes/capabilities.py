from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from stevie_explorer.capabilities import CapabilityProbeService
from stevie_explorer.capabilities.probes import SAMSUNG_PROBES
from stevie_explorer.identifiers import ServiceName
from stevie_explorer.kernel import ExplorerKernel

class RunProbeRequest(BaseModel):
    probe_id: str

class ProbeResultResponse(BaseModel):
    result_id: str
    target_id: str
    probe_id: str
    probe_name: str
    status: str
    duration_ms: float
    response: object | None = None
    error: str | None = None
    matched_event: str | None = None

def create_capabilities_router(kernel: ExplorerKernel) -> APIRouter:
    router = APIRouter(
        prefix="/targets",
        tags=["capabilities"]
    )

    @router.post("/{target_id}/capabilities/probe", response_model=ProbeResultResponse)
    async def run_probe(target_id: str, request: RunProbeRequest) -> ProbeResultResponse:
        probe = next(
            (
                candidate
                for candidate in SAMSUNG_PROBES
                if candidate.probe_id == request.probe_id
            ),
            None
        )

        if probe is None:
            raise HTTPException(
                status_code=404,
                detail=f"Unknown probe: {request.probe_id}"
            )
        
        service: CapabilityProbeService = kernel.get(ServiceName.CAPABILITY_PROBE)

        result = await service.run(target_id=target_id, probe=probe)

        return ProbeResultResponse(
            result_id=result.result_id,
            target_id=result.target_id,
            probe_id=result.probe_id,
            probe_name=result.probe_name,
            status=result.status.value,
            duration_ms=result.duration_ms,
            response=result.response,
            error=result.error,
            matched_event=result.matched_event
        )
    
    return router
