import httpx
import pytest

from stevie_explorer.api import ApiService
from stevie_explorer.config import Configuration
from stevie_explorer.devices import DeviceRegistry
from stevie_explorer.eventbus import EventBus
from stevie_explorer.kernel import ExplorerKernel
from stevie_explorer.telemetry import TelemetryService


@pytest.mark.asyncio
async def test_devices_crud_api(tmp_path) -> None:
    kernel = ExplorerKernel()
    configuration = Configuration()
    eventbus = EventBus()
    telemetry = TelemetryService(eventbus)
    registry = DeviceRegistry(kernel, tmp_path / "devices.json")
    api = ApiService(kernel)
    for component in (configuration, eventbus, telemetry, registry, api):
        kernel.register(component)

    transport = httpx.ASGITransport(app=api.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        created = await client.post("/devices", json={
            "device_name": "living-room-tv",
            "display_name": "Living Room TV",
            "ip_address": "192.168.50.10",
        })
        assert created.status_code == 201
        device_id = created.json()["id"]
        assert (await client.get("/devices")).json() == [created.json()]
        assert (await client.get(f"/devices/{device_id}")).status_code == 200

        assert (await client.post("/devices", json={
            "device_name": "bad", "display_name": "Bad", "ip_address": "invalid"
        })).status_code == 422

        updated = await client.post("/devices", json={
            "id": device_id,
            "ip_address": "192.168.50.123",
        })
        assert updated.status_code == 200
        assert updated.json()["ip_address"] == "192.168.50.123"
        missing = await client.post("/devices", json={
            "id": "00000000-0000-0000-0000-000000000000",
            "ip_address": "192.168.50.99",
        })
        assert missing.status_code == 404
        assert (await client.delete(f"/devices/{device_id}")).status_code == 200
        assert (await client.get(f"/devices/{device_id}")).status_code == 404


@pytest.mark.asyncio
@pytest.mark.parametrize("target_count", [0, 2])
async def test_device_targets_api(tmp_path, target_count) -> None:
    from stevie_explorer.devices import Device
    from stevie_explorer.identifiers import TransportType
    from stevie_explorer.targets import Target, TargetRegistry

    kernel = ExplorerKernel()
    eventbus = EventBus()
    telemetry = TelemetryService(eventbus)
    devices = DeviceRegistry(kernel, tmp_path / "devices.json")
    targets = TargetRegistry(kernel)
    api = ApiService(kernel)
    for component in (eventbus, telemetry, devices, targets, api):
        kernel.register(component)

    device = await devices.create(Device(
        device_name="living-room", display_name="Living Room", ip_address="10.0.0.1",
    ))
    other_device = await devices.create(Device(
        device_name="office", display_name="Office", ip_address="10.0.0.2",
    ))
    await targets.create(Target(
        name="Other device", transport=TransportType.WEBSOCKET, device_id=other_device.id,
    ))
    await targets.create(Target(
        name="Standalone", transport=TransportType.WEBSOCKET, uri="ws://localhost:8765",
    ))
    expected = []
    for index in range(target_count):
        target = await targets.create(Target(
            name=f"Control {index}", transport=TransportType.WEBSOCKET,
            device_id=device.id, scheme="wss", port=8002, path="/control",
            query={"name": "remote"}, headers={"Authorization": "private"}, tags=("control",),
        ))
        expected.append({
            "target_id": target.target_id, "name": target.name, "display_name": target.display_name,
        })

    transport = httpx.ASGITransport(app=api.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(f"/devices/{device.id}/targets")

    assert response.status_code == 200
    assert response.json() == {"device_id": str(device.id), "targets": expected}


@pytest.mark.asyncio
async def test_unknown_device_targets_returns_404(tmp_path) -> None:
    kernel = ExplorerKernel()
    devices = DeviceRegistry(kernel, tmp_path / "devices.json")
    kernel.register(devices)
    api = ApiService(kernel)
    device_id = "00000000-0000-0000-0000-000000000000"

    transport = httpx.ASGITransport(app=api.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(f"/devices/{device_id}/targets")
        device_response = await client.get(f"/devices/{device_id}")

    assert response.status_code == 404
    assert response.json() == {"detail": f"'Unknown device: {device_id}'"}
    assert response.json() == device_response.json()
