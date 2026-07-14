from __future__ import annotations

import asyncio

import uvicorn
from fastapi import FastAPI
from pydantic import BaseModel

from stevie_explorer.config import Configuration
from stevie_explorer.identifiers import (
    ServiceName,
    TelemetryMessage,
)
from stevie_explorer.kernel import (
    BaseService,
    ExplorerKernel,
)
from stevie_explorer.telemetry import TelemetryService


class HealthResponse(BaseModel):
    status: str
    application: str
    environment: str


class ComponentResponse(BaseModel):
    name: str
    is_service: bool


class ApiService(BaseService):
    name = ServiceName.API

    def __init__(self, kernel: ExplorerKernel) -> None:
        self.kernel = kernel

        self.app = FastAPI(
            title="Stevie Explorer API",
            description=(
                "Protocol discovery and device "
                "investigation platform"
            ),
            version="0.1.0",
        )

        self._server: uvicorn.Server | None = None
        self._server_task: asyncio.Task[None] | None = None

        self._register_routes()

    async def start(self) -> None:
        configuration: Configuration = self.kernel.get(
            ServiceName.CONFIGURATION
        )
        telemetry: TelemetryService = self.kernel.get(
            ServiceName.TELEMETRY
        )

        settings = configuration.settings

        await telemetry.emit(
            TelemetryMessage.API_STARTING,
            source=self.name,
            host=settings.api_host,
            port=settings.api_port,
        )

        server_config = uvicorn.Config(
            app=self.app,
            host=settings.api_host,
            port=settings.api_port,
            log_config=None,
            access_log=False,
            lifespan="off",
        )

        self._server = uvicorn.Server(server_config)

        self._server_task = asyncio.create_task(
            self._server.serve(),
            name="stevie-explorer-api",
        )

        await self._wait_until_started()

        await telemetry.emit(
            TelemetryMessage.API_STARTED,
            source=self.name,
            host=settings.api_host,
            port=settings.api_port,
        )

    async def stop(self) -> None:
        telemetry: TelemetryService = self.kernel.get(
            ServiceName.TELEMETRY
        )

        await telemetry.emit(
            TelemetryMessage.API_STOPPING,
            source=self.name,
        )

        if self._server is not None:
            self._server.should_exit = True

        if self._server_task is not None:
            try:
                await self._server_task
            except asyncio.CancelledError:
                pass

        self._server = None
        self._server_task = None

    def _register_routes(self) -> None:
        @self.app.get(
            "/health",
            response_model=HealthResponse,
            tags=["system"],
        )
        async def health() -> HealthResponse:
            configuration: Configuration = self.kernel.get(
                ServiceName.CONFIGURATION
            )

            return HealthResponse(
                status="ok",
                application=self.kernel.name,
                environment=configuration.settings.explorer_env,
            )

        @self.app.get(
            "/components",
            response_model=list[ComponentResponse],
            tags=["system"],
        )
        async def components() -> list[ComponentResponse]:
            return [
                ComponentResponse(
                    name=name,
                    is_service=self.kernel.is_service(name),
                )
                for name in self.kernel.component_names()
            ]

    async def _wait_until_started(self) -> None:
        if self._server is None or self._server_task is None:
            raise RuntimeError("API server has not been created.")

        for _ in range(100):
            if self._server.started:
                return

            if self._server_task.done():
                exception = self._server_task.exception()

                if exception is not None:
                    raise exception

                raise RuntimeError(
                    "API server stopped during startup."
                )

            await asyncio.sleep(0.05)

        self._server.should_exit = True

        raise TimeoutError(
            "API server did not start in time."
        )