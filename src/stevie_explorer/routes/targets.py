from __future__ import annotations

from datetime import datetime
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from stevie_explorer.identifiers import ServiceName, TransportType
from stevie_explorer.kernel import ExplorerKernel
from stevie_explorer.targets import Target, TargetRegistry 

class CreateTargetRequest(BaseModel):
    name: str = Field(
        min_length=1,
        max_length=128
    )

    uri: str = Field(
        min_length=1,
        max_length=2048,
        examples=["ws://localhost:8765"]
    )

    transport: TransportType

    headers: dict[str, str] = Field(default_factory=dict)

    tags: list[str] = Field(default_factory=list)

class TargetResponse(BaseModel):
    target_id: str
    name: str
    uri: str
    transport: TransportType
    headers: dict[str, str]
    tags: list[str]
    created_at: datetime

def create_router(kernel: ExplorerKernel) -> APIRouter:
    router = APIRouter(
        prefix="/targets",
        tags=["targets"]
    )

    @router.get("", response_model=list[TargetResponse])
    async def list_targets() -> list[TargetResponse]:
        registry: TargetRegistry = kernel.get(ServiceName.TARGET_REGISTRY)

        return [
            _response(target) for target in registry.list()
        ]

    @router.post("", response_model=TargetResponse, status_code=201)
    async def create_target(request: CreateTargetRequest) -> TargetResponse:
        registry: TargetRegistry = kernel.get(ServiceName.TARGET_REGISTRY)

        target = await registry.create(
            Target(
                name = request.name,
                uri = request.uri,
                transport = request.transport,
                headers = request.headers,
                tags = tuple(request.tags)
            )
        )

        return _response(target)

    @router.get("/{target_id}", response_model=TargetResponse)
    async def get_target(target_id: str) -> TargetResponse:
        registry: TargetRegistry = kernel.get(ServiceName.TARGET_REGISTRY)

        try:
            target = registry.get(target_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        
        return _response(target)

    return router

def _response(target: Target) -> TargetResponse:
    return TargetResponse(
        target_id = target.target_id,
        name = target.name,
        uri = target.uri,
        transport = target.transport,
        headers = target.headers,
        tags = list(target.tags),
        created_at = target.created_at
    )
