import pytest

from stevie_explorer.config import Configuration
from stevie_explorer.eventbus import EventBus
from stevie_explorer.identifiers import TransportType
from stevie_explorer.kernel import ExplorerKernel
from stevie_explorer.targets import Target, TargetRegistry
from stevie_explorer.telemetry import TelemetryService

@pytest.mark.asyncio
async def test_target_can_be_registered() -> None:
    kernel = ExplorerKernel()

    configuration = Configuration()
    eventbus = EventBus()
    telemetry = TelemetryService(eventbus)
    registry = TargetRegistry(kernel)

    kernel.register(configuration)
    kernel.register(eventbus)
    kernel.register(telemetry)
    kernel.register(registry)

    await telemetry.start()

    target = Target(
        name="Test",
        uri="ws://localhost:8765",
        transport=TransportType.WEBSOCKET
    )

    await registry.create(target)

    assert registry.get(target.target_id) == target
