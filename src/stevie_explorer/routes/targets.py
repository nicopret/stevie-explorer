from __future__ import annotations

from datetime import datetime
from uuid import UUID
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, model_validator

from stevie_explorer.identifiers import ServiceName, TransportType
from stevie_explorer.kernel import ExplorerKernel
from stevie_explorer.targets import Target, TargetRegistry 

class CreateTargetRequest(BaseModel):
    name: str = Field(
        min_length=1,
        max_length=128
    )

    uri: str | None = Field(
        default=None,
        min_length=1,
        max_length=2048,
        examples=["ws://localhost:8765"]
    )

    transport: TransportType
    device_id: UUID | None = None
    scheme: str | None = None
    port: int | None = Field(default=None, ge=1, le=65535)
    path: str = ""
    query: dict[str, str] = Field(default_factory=dict)

    headers: dict[str, str] = Field(default_factory=dict)

    tags: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_location(self) -> "CreateTargetRequest":
        if self.uri is None and self.device_id is None:
            raise ValueError("uri or device_id is required")
        return self

class TargetResponse(BaseModel):
    target_id: str
    name: str
    display_name: str
    uri: str | None
    transport: TransportType | None
    headers: dict[str, str]
    tags: list[str]
    created_at: datetime
    device_id: UUID | None
    scheme: str | None
    port: int | None
    path: str
    query: dict[str, str]

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

        try:
            target = await registry.create(
                Target(
                    name = request.name,
                    uri = request.uri,
                    transport = request.transport,
                    device_id=request.device_id,
                    scheme=request.scheme,
                    port=request.port,
                    path=request.path,
                    query=request.query,
                    headers = request.headers,
                    tags = tuple(request.tags)
                )
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

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
        display_name=target.display_name,
        uri = target.uri,
        transport = target.transport,
        headers = target.headers,
        tags = list(target.tags),
        created_at = target.created_at,
        device_id=target.device_id,
        scheme=target.scheme,
        port=target.port,
        path=target.path,
        query=target.query,
    )
