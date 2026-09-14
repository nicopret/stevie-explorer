import pytest

from stevie_explorer.config import Configuration
from stevie_explorer.devices import Device, DevicePatch, DeviceRegistry
from stevie_explorer.eventbus import EventBus
from stevie_explorer.identifiers import SessionState, TransportType
from stevie_explorer.kernel import ExplorerKernel
from stevie_explorer.sessions import SessionManager
from stevie_explorer.targets import Target, TargetRegistry
from stevie_explorer.telemetry import TelemetryService
from stevie_explorer.transports import BaseTransport


class FakeTransport(BaseTransport):
    def __init__(self, connected_uris: list[str]) -> None:
        self.connected_uris = connected_uris
        self._connected = False

    @property
    def connected(self) -> bool:
        return self._connected

    async def connect(self, target, on_message, on_error) -> None:
        self.connected_uris.append(target.uri)
        self._connected = True

    async def disconnect(self) -> None:
        self._connected = False

    async def send(self, payload) -> None:
        pass

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


@pytest.mark.asyncio
async def test_connected_device_session_reconnects_with_new_ip(tmp_path) -> None:
    kernel = ExplorerKernel()
    configuration = Configuration()
    eventbus = EventBus()
    telemetry = TelemetryService(eventbus)
    devices = DeviceRegistry(kernel, tmp_path / "devices.json")
    registry = TargetRegistry(kernel)
    manager = SessionManager(kernel)
    for component in (configuration, eventbus, telemetry, devices, registry, manager):
        kernel.register(component)

    connected_uris = []
    manager._create_transport = lambda transport_type: FakeTransport(connected_uris)
    await manager.start()
    try:
        device = await devices.create(Device(device_name="tv", display_name="TV", ip_address="10.0.0.1"))
        target = await registry.create(Target(
            name="TV", transport=TransportType.WEBSOCKET,
            device_id=device.id, scheme="ws", port=8001,
        ))
        session = await manager.create(target.target_id)
        await manager.connect(session.session_id)
        await devices.update(device.id, DevicePatch(ip_address="10.0.0.2"))

        assert connected_uris == ["ws://10.0.0.1:8001", "ws://10.0.0.2:8001"]
        assert session.state == SessionState.CONNECTED
    finally:
        await manager.stop()
