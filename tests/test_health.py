import httpx
import pytest

from stevie_explorer.api import ApiService
from stevie_explorer.config import Configuration
from stevie_explorer.eventbus import EventBus
from stevie_explorer.kernel import ExplorerKernel
from stevie_explorer.telemetry import TelemetryService

@pytest.mark.asyncio
async def test_health_endpoint() -> None:
    kernel = ExplorerKernel()

    configuration = Configuration()
    eventbus = EventBus()
    telemetry = TelemetryService(eventbus)
    api = ApiService(kernel)

    kernel.register(configuration)
    kernel.register(eventbus)
    kernel.register(telemetry)
    kernel.register(api)

    transport = httpx.ASGITransport(app=api.app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/health")

        assert response.status_code == 200
        assert response.json()["status"] == "ok"
