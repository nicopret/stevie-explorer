import asyncio
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from stevie_explorer.devices import Device, DevicePatch, DeviceRegistry
from stevie_explorer.eventbus import EventBus
from stevie_explorer.identifiers import Topic
from stevie_explorer.kernel import ExplorerKernel
from stevie_explorer.devices.registry import RegistryDocument


def make_registry(tmp_path, poll_interval=0.01):
    kernel = ExplorerKernel()
    eventbus = EventBus()
    registry = DeviceRegistry(kernel, tmp_path / "devices.json", poll_interval)
    kernel.register(eventbus)
    kernel.register(registry)
    return registry, eventbus


def test_bundled_registry_contains_samsung_au8000() -> None:
    path = Path(__file__).parents[1] / "config" / "devices.json"
    document = RegistryDocument.model_validate_json(path.read_text())
    samsung = next(device for device in document.devices if device.device_name == "samsung_au8000")
    assert str(samsung.id) == "193a3333-85b9-59cc-8fac-a38c551040f7"


@pytest.mark.asyncio
async def test_crud_persists_versioned_registry(tmp_path) -> None:
    registry, _ = make_registry(tmp_path)
    await registry.start()
    try:
        device = await registry.create(Device(
            device_name="living-room-tv",
            display_name="Living Room TV",
            ip_address="192.168.50.10",
        ))
        assert registry.get(device.id) == device
        assert registry.list() == (device,)

        renamed = await registry.update(device.id, DevicePatch(display_name="Samsung TV"))
        updated = await registry.update(device.id, DevicePatch(ip_address="192.168.50.123"))
        assert renamed.display_name == "Samsung TV"
        assert str(updated.ip_address) == "192.168.50.123"

        document = json.loads(registry.path.read_text())
        assert document["version"] == 3
        assert document["devices"][0]["ip_address"] == "192.168.50.123"
        assert not list(tmp_path.glob(".devices.json.*"))

        assert await registry.remove(device.id) == updated
        with pytest.raises(KeyError):
            registry.get(device.id)
    finally:
        await registry.stop()


@pytest.mark.asyncio
async def test_duplicate_and_invalid_ip_are_rejected(tmp_path) -> None:
    registry, _ = make_registry(tmp_path)
    device = Device(device_name="tv", display_name="TV", ip_address="127.0.0.1")
    await registry.create(device)
    with pytest.raises(ValueError):
        await registry.create(device)
    with pytest.raises(ValidationError):
        Device(device_name="bad", display_name="Bad", ip_address="not-an-ip")


@pytest.mark.asyncio
async def test_external_reload_emits_once_and_invalid_file_preserves_state(tmp_path) -> None:
    registry, eventbus = make_registry(tmp_path)
    events = []

    async def receive(event):
        events.append(event)

    eventbus.subscribe(Topic.DEVICE_UPDATED, receive)
    registry.path.write_text(json.dumps({
        "version": 1,
        "devices": [{"device_name": "tv", "display_name": "TV", "ip_address": "10.0.0.1"}],
    }))
    await registry.start()
    try:
        registry.path.write_text(json.dumps({
            "version": 2,
            "devices": [{"device_name": "tv", "display_name": "TV", "ip_address": "10.0.0.2"}],
        }))
        await asyncio.sleep(0.05)
        device_id = registry.get_by_name("tv").id
        assert str(registry.get(device_id).ip_address) == "10.0.0.2"
        assert len(events) == 1

        await registry.reload()
        assert len(events) == 1

        registry.path.write_text('{"version":3,"devices":[{"device_name":"tv","display_name":"TV","ip_address":"bad"}]}')
        assert await registry.reload() is False
        assert str(registry.get(device_id).ip_address) == "10.0.0.2"
    finally:
        await registry.stop()
