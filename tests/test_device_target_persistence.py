import json
from uuid import UUID, uuid4

import httpx
import pytest

from stevie_explorer.api import ApiService
from stevie_explorer.devices import Device, DevicePatch, DeviceRegistry
from stevie_explorer.eventbus import EventBus
from stevie_explorer.identifiers import TransportType
from stevie_explorer.kernel import ExplorerKernel
from stevie_explorer.targets import Target, TargetRegistry
from stevie_explorer.telemetry import TelemetryService


def make_kernel(tmp_path):
    kernel = ExplorerKernel()
    eventbus = EventBus()
    targets = TargetRegistry(kernel)
    devices = DeviceRegistry(kernel, tmp_path / "devices.json")
    for component in (eventbus, targets, TelemetryService(eventbus), devices):
        kernel.register(component)
    return kernel, devices, targets


@pytest.mark.asyncio
async def test_create_targets_persist_and_survive_kernel_restart(tmp_path):
    kernel, devices, targets = make_kernel(tmp_path)
    device = await devices.create(Device(
        device_name="tv", display_name="TV", ip_address="10.0.0.1",
    ))
    other = await devices.create(Device(
        device_name="office", display_name="Office", ip_address="10.0.0.2",
    ))
    transport = httpx.ASGITransport(app=ApiService(kernel).app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        expected = []
        for name, display_name in (
            ("samsung.api.v2", "Samsung API v2"),
            ("samsung.remote.control", "Samsung Remote Control"),
        ):
            response = await client.post(f"/devices/{device.id}/targets", json={
                "name": name, "display_name": display_name,
            })
            assert response.status_code == 201
            summary = response.json()
            assert set(summary) == {"target_id", "name", "display_name"}
            assert summary["name"] == name
            assert summary["display_name"] == display_name
            assert UUID(summary["target_id"]).version == 4
            target = targets.get(summary["target_id"])
            assert target.device_id == device.id
            assert target.name == name
            assert target.display_name == display_name
            assert target.transport is None
            expected.append(summary)
        assert expected[0]["target_id"] != expected[1]["target_id"]
        assert (await client.get(f"/devices/{device.id}/targets")).json() == {
            "device_id": str(device.id), "targets": expected,
        }
        assert (await client.get(f"/devices/{other.id}/targets")).json() == {
            "device_id": str(other.id), "targets": [],
        }
        all_targets = await client.get("/targets")
        assert all_targets.status_code == 200
        assert [item["display_name"] for item in all_targets.json()] == [
            item["display_name"] for item in expected
        ]
        detail = await client.get(f"/targets/{expected[0]['target_id']}")
        assert detail.status_code == 200
        assert detail.json()["display_name"] == expected[0]["display_name"]
        assert "targets" not in (await client.get(f"/devices/{device.id}")).json()

    # Subsequent device edits must preserve nested target configuration.
    await devices.update(device.id, DevicePatch(display_name="Renamed TV"))
    saved = json.loads(devices.path.read_text())
    assert [{"target_id": item["target_id"], "name": item["name"], "display_name": item["display_name"]}
            for item in saved["devices"][0]["targets"]] == expected
    assert saved["devices"][1]["targets"] == []

    restarted, restored_devices, restored_targets = make_kernel(tmp_path)
    await restarted.start()
    try:
        assert restored_devices.get(device.id).display_name == "Renamed TV"
        assert restored_targets.list() == targets.list()
        transport = httpx.ASGITransport(app=ApiService(restarted).app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            assert (await client.get(f"/devices/{device.id}/targets")).json() == {
                "device_id": str(device.id), "targets": expected,
            }
    finally:
        await restarted.stop()


@pytest.mark.asyncio
@pytest.mark.parametrize("body", [
    {}, {"name": "control"}, {"display_name": "Control"},
    {"name": "", "display_name": "Control"},
    {"name": " \t\n ", "display_name": "Control"},
    {"name": None, "display_name": "Control"},
    {"name": "control", "display_name": ""},
    {"name": "control", "display_name": " \t\n "},
    {"name": "control", "display_name": None},
    {"name": "control", "display_name": "Control", "target_id": str(uuid4())},
    {"name": "control", "display_name": "Control", "transport": "websocket"},
])
async def test_invalid_target_requests_do_not_change_state(tmp_path, body):
    kernel, devices, targets = make_kernel(tmp_path)
    device = await devices.create(Device(
        device_name="tv", display_name="TV", ip_address="10.0.0.1",
    ))
    before = devices.path.read_bytes()
    transport = httpx.ASGITransport(app=ApiService(kernel).app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(f"/devices/{device.id}/targets", json=body)
    assert response.status_code == 422
    assert targets.list() == ()
    assert devices.path.read_bytes() == before


@pytest.mark.asyncio
async def test_unknown_device_does_not_create_target(tmp_path):
    kernel, devices, targets = make_kernel(tmp_path)
    device_id = uuid4()
    transport = httpx.ASGITransport(app=ApiService(kernel).app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(f"/devices/{device_id}/targets", json={"name": "control", "display_name": "Control"})
    assert response.status_code == 404
    assert response.json() == {"detail": f"'Unknown device: {device_id}'"}
    assert targets.list() == ()
    assert not devices.path.exists()


@pytest.mark.asyncio
async def test_legacy_devices_and_summary_targets_load(tmp_path):
    kernel, devices, targets = make_kernel(tmp_path)
    target_id = str(uuid4())
    devices.path.write_text(json.dumps({"version": 1, "devices": [
        {"device_name": "legacy", "display_name": "Legacy", "ip_address": "10.0.0.1"},
        {"device_name": "tv", "display_name": "TV", "ip_address": "10.0.0.2",
         "targets": [{"target_id": target_id, "name": "Control"}]},
    ]}))
    await kernel.start()
    try:
        assert devices.get_by_name("legacy").targets == ()
        assert targets.get_by_device(devices.get_by_name("legacy").id) == []
        assert targets.get(target_id).device_id == devices.get_by_name("tv").id
        assert targets.get(target_id).name == "Control"
        assert targets.get(target_id).display_name == "Control"
        assert targets.get(target_id).target_id == target_id
        await devices.update(devices.get_by_name("tv").id, DevicePatch(display_name="Renamed TV"))
        saved_target = json.loads(devices.path.read_text())["devices"][1]["targets"][0]
        assert saved_target["name"] == saved_target["display_name"] == "Control"
        assert saved_target["target_id"] == target_id
        _, reloaded_devices, reloaded_targets = make_kernel(tmp_path)
        assert await reloaded_devices.reload()
        assert reloaded_targets.get(target_id) == targets.get(target_id)
    finally:
        await kernel.stop()


@pytest.mark.asyncio
async def test_failed_write_rolls_back_target_and_device_state(tmp_path, monkeypatch):
    kernel, devices, targets = make_kernel(tmp_path)
    device = await devices.create(Device(
        device_name="tv", display_name="TV", ip_address="10.0.0.1",
    ))
    existing = await targets.create(Target(name="Existing", device_id=device.id))
    before = devices.path.read_bytes()
    version = devices.version

    def fail_write(content):
        raise OSError("Disk unavailable")

    monkeypatch.setattr(devices, "_atomic_write", fail_write)
    transport = httpx.ASGITransport(app=ApiService(kernel).app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(f"/devices/{device.id}/targets", json={"name": "new", "display_name": "New"})
    assert response.status_code == 500
    assert targets.list() == (existing,)
    assert devices.get(device.id).targets == (existing,)
    assert devices.version == version
    assert devices.path.read_bytes() == before


@pytest.mark.asyncio
async def test_configured_targets_reload_and_device_removal_cleans_runtime(tmp_path):
    kernel, devices, targets = make_kernel(tmp_path)
    device = await devices.create(Device(
        device_name="tv", display_name="TV", ip_address="10.0.0.1",
    ))
    configured = await targets.create(Target(
        name="Control", device_id=device.id, transport=TransportType.WEBSOCKET,
        scheme="ws", port=8001, path="/control", query={"name": "remote"},
        headers={"X-Test": "value"}, tags=("remote",),
    ))
    standalone = await targets.create(Target(
        name="Standalone", uri="ws://localhost:8765", transport=TransportType.WEBSOCKET,
    ))
    _, restored_devices, restored_targets = make_kernel(tmp_path)
    assert await restored_devices.reload()
    assert restored_targets.get(configured.target_id) == configured
    assert restored_targets.resolve(configured.target_id).uri == "ws://10.0.0.1:8001/control?name=remote"

    document = json.loads(devices.path.read_text())
    document["devices"][0]["targets"][0]["name"] = "Renamed"
    devices.path.write_text(json.dumps(document))
    assert await devices.reload()
    assert targets.get(configured.target_id).name == "Renamed"
    assert targets.get(standalone.target_id) == standalone
    await devices.remove(device.id)
    assert targets.list() == (standalone,)
    assert json.loads(devices.path.read_text())["devices"] == []


@pytest.mark.asyncio
async def test_unconfigured_target_cannot_resolve(tmp_path):
    _, devices, targets = make_kernel(tmp_path)
    device = await devices.create(Device(
        device_name="tv", display_name="TV", ip_address="10.0.0.1",
    ))
    target = await targets.create(Target(name="Control", device_id=device.id))
    with pytest.raises(ValueError, match="transport is not configured"):
        targets.resolve(target.target_id)


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid_targets", [
    [{"name": "Missing ID"}],
    [{"target_id": "duplicate", "name": "First"}, {"target_id": "duplicate", "name": "Second"}],
    [{"target_id": "wrong-owner", "name": "Control", "device_id": str(uuid4())}],
    None,
])
async def test_invalid_persisted_targets_preserve_runtime(tmp_path, invalid_targets):
    _, devices, targets = make_kernel(tmp_path)
    device = await devices.create(Device(
        device_name="tv", display_name="TV", ip_address="10.0.0.1",
    ))
    existing = await targets.create(Target(name="Existing", device_id=device.id))
    document = json.loads(devices.path.read_text())
    document["devices"][0]["targets"] = invalid_targets
    devices.path.write_text(json.dumps(document))
    assert await devices.reload() is False
    assert targets.list() == (existing,)
    assert devices.get(device.id).targets == (existing,)


@pytest.mark.asyncio
async def test_delete_target_persists_and_survives_restart(tmp_path):
    kernel, devices, targets = make_kernel(tmp_path)
    device = await devices.create(Device(device_name="tv", display_name="TV", ip_address="10.0.0.1"))
    other = await devices.create(Device(device_name="other", display_name="Other", ip_address="10.0.0.2"))
    removed = await targets.create(Target(name="ed.installedApp.get", display_name="Installed Applications", device_id=device.id))
    remaining = await targets.create(Target(name="samsung.remote.control", device_id=device.id))
    other_target = await targets.create(Target(name=removed.name, device_id=other.id))
    standalone = await targets.create(Target(name=removed.name, uri="ws://localhost:8765"))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=ApiService(kernel).app), base_url="http://test") as client:
        response = await client.delete(f"/devices/{device.id}/targets/{removed.target_id}")
        assert response.status_code == 200
        assert response.json() == {"target_id": removed.target_id, "name": removed.name, "display_name": removed.display_name}
        listing = await client.get(f"/devices/{device.id}/targets")
        assert [item["target_id"] for item in listing.json()["targets"]] == [remaining.target_id]
        assert (await client.delete(f"/devices/{device.id}/targets/{removed.target_id}")).status_code == 404
    with pytest.raises(KeyError):
        targets.get(removed.target_id)
    assert devices.get(device.id).targets == (remaining,)
    assert targets.get(other_target.target_id) == other_target
    assert targets.get(standalone.target_id) == standalone
    saved = json.loads(devices.path.read_text())
    assert [item["target_id"] for item in saved["devices"][0]["targets"]] == [remaining.target_id]
    restarted, restored_devices, restored_targets = make_kernel(tmp_path)
    await restarted.start()
    try:
        assert restored_devices.get(device.id).targets == (remaining,)
        assert restored_targets.get_by_device(device.id) == [remaining]
        with pytest.raises(KeyError):
            restored_targets.get(removed.target_id)
        assert restored_targets.get(other_target.target_id) == other_target
    finally:
        await restarted.stop()


@pytest.mark.asyncio
@pytest.mark.parametrize("missing", ["device", "target", "wrong_device", "display_name", "name"])
async def test_delete_target_not_found_preserves_state(tmp_path, missing):
    kernel, devices, targets = make_kernel(tmp_path)
    device = await devices.create(Device(device_name="tv", display_name="TV", ip_address="10.0.0.1"))
    other = await devices.create(Device(device_name="other", display_name="Other", ip_address="10.0.0.2"))
    target = await targets.create(Target(name="samsung.remote.control", display_name="Remote Control", device_id=device.id))
    device_id = uuid4() if missing == "device" else other.id if missing == "wrong_device" else device.id
    target_id = {"target": "unknown", "display_name": target.display_name, "name": target.name}.get(missing, target.target_id)
    before = devices.path.read_bytes()
    version = devices.version
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=ApiService(kernel).app), base_url="http://test") as client:
        response = await client.delete(f"/devices/{device_id}/targets/{target_id}")
    assert response.status_code == 404
    assert "detail" in response.json()
    assert targets.list() == (target,)
    assert devices.get(device.id).targets == (target,)
    assert devices.path.read_bytes() == before
    assert devices.version == version


@pytest.mark.asyncio
async def test_delete_target_write_failure_rolls_back(tmp_path, monkeypatch):
    kernel, devices, targets = make_kernel(tmp_path)
    device = await devices.create(Device(device_name="tv", display_name="TV", ip_address="10.0.0.1"))
    target = await targets.create(Target(name="samsung.remote.control", device_id=device.id))
    before = devices.path.read_bytes()
    version = devices.version

    def fail_write(content):
        raise OSError("Disk unavailable")

    monkeypatch.setattr(devices, "_atomic_write", fail_write)
    transport = httpx.ASGITransport(app=ApiService(kernel).app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.delete(f"/devices/{device.id}/targets/{target.target_id}")
        assert response.status_code == 500
        listing = await client.get(f"/devices/{device.id}/targets")
        assert listing.json()["targets"][0]["target_id"] == target.target_id
    assert devices.get(device.id).targets == (target,)
    assert targets.list() == (target,)
    assert devices.version == version
    assert devices.path.read_bytes() == before
