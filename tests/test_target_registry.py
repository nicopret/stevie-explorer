import pytest

from stevie_explorer.config import Configuration
from stevie_explorer.eventbus import EventBus
from stevie_explorer.identifiers import TransportType
from stevie_explorer.kernel import ExplorerKernel
from stevie_explorer.targets import Target, TargetRegistry
from stevie_explorer.telemetry import TelemetryService
from stevie_explorer.devices import Device, DevicePatch, DeviceRegistry

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


@pytest.mark.asyncio
async def test_device_target_resolves_latest_ip(tmp_path) -> None:
    kernel = ExplorerKernel()
    eventbus = EventBus()
    telemetry = TelemetryService(eventbus)
    devices = DeviceRegistry(kernel, tmp_path / "devices.json")
    targets = TargetRegistry(kernel)
    for component in (eventbus, telemetry, devices, targets):
        kernel.register(component)

    device = await devices.create(Device(device_name="tv", display_name="TV", ip_address="10.0.0.1"))
    target = await targets.create(Target(
        name="Samsung", transport=TransportType.WEBSOCKET,
        device_id=device.id, scheme="wss", port=8002, path="/api/v2", query={"name": "remote"},
    ))
    assert targets.resolve(target.target_id).uri == "wss://10.0.0.1:8002/api/v2?name=remote"
    await devices.update(device.id, DevicePatch(ip_address="10.0.0.2"))
    assert targets.resolve(target.target_id).uri.startswith("wss://10.0.0.2:8002/")
