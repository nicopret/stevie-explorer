import pytest

from stevie_explorer.config import Configuration
from stevie_explorer.eventbus import EventBus
from stevie_explorer.identifiers import SessionState, TransportType
from stevie_explorer.kernel import ExplorerKernel
from stevie_explorer.sessions import SessionManager
from stevie_explorer.targets import Target, TargetRegistry
from stevie_explorer.telemetry import TelemetryService

@pytest.mark.asyncio
async def test_session_can_be_created() -> None:
    kernel = ExplorerKernel()

    configuration = Configuration()
    eventbus = EventBus()
    telemetry = TelemetryService(eventbus)
    registry = TargetRegistry(kernel)
    manager = SessionManager(kernel)

    kernel.register(configuration)
    kernel.register(eventbus)
    kernel.register(telemetry)
    kernel.register(registry)
    kernel.register(manager)

    await telemetry.start()

    target = await registry.create(
        Target(
            name = "Test WebSocket",
            uri = "ws://localhost:8765",
            transport = TransportType.WEBSOCKET
        )
    )

    session = await manager.create(target.target_id)

    assert session.target_id == target.target_id
    assert session.state == SessionState.CREATED
