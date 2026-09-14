import asyncio
import json
from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest

from stevie_explorer.api import ApiService
from stevie_explorer.capabilities import CapabilityProbeService, CapabilityStatus, ProbeRegistry
from stevie_explorer.capabilities import service as service_module
from stevie_explorer.capabilities.probes.samsung import SAMSUNG_PROBES
from stevie_explorer.devices import Device, DeviceRegistry
from stevie_explorer.devices.samsung import client as samsung_client
from stevie_explorer.eventbus import EventBus
from stevie_explorer.kernel import ExplorerKernel
from stevie_explorer.targets import Target, TargetRegistry
from stevie_explorer.telemetry import TelemetryService
from stevie_explorer.transports import websocket


@pytest.fixture
def setup(tmp_path, monkeypatch):
    kernel = ExplorerKernel()
    eventbus = EventBus()
    devices = DeviceRegistry(kernel, tmp_path / "devices.json")
    targets = TargetRegistry(kernel)
    service = CapabilityProbeService(kernel)
    for component in (eventbus, TelemetryService(eventbus), devices, targets, service):
        kernel.register(component)
    # Exercise the real timeout path without waiting for production timeouts.
    monkeypatch.setattr(service_module, "PROBE_REGISTRY", ProbeRegistry(
        tuple(replace(probe, timeout=0.3) for probe in SAMSUNG_PROBES), (),
    ))
    return kernel, devices, targets, service


async def add_target(setup, name):
    _, devices, targets, _ = setup
    device = await devices.create(Device(
        device_name="tv", display_name="TV", ip_address="10.0.0.1",
    ))
    target = await targets.create(Target(
        name=name, display_name="Human-readable label", device_id=device.id,
    ))
    return device, target


def mock_http(monkeypatch, handler):
    real_client = httpx.AsyncClient
    monkeypatch.setattr(samsung_client, "httpx", SimpleNamespace(
        AsyncClient=lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs),
    ))


class FakeConnection:
    def __init__(self, messages):
        self.messages = messages
        self.close = AsyncMock()
        self.send = AsyncMock()

    async def __aiter__(self):
        for message in self.messages:
            yield json.dumps(message)
        await asyncio.Event().wait()


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome,expected", [
    ("valid", "supported"), ("refused", "no_response"),
    ("invalid", "no_match"), ("http_error", "no_response"),
    ("invalid_json", "no_response"),
])
async def test_api_v2_probe_and_result_store(setup, monkeypatch, outcome, expected):
    kernel, devices, _, service = setup
    device, target = await add_target(setup, "samsung.api.v2")
    calls = []

    def respond(request):
        calls.append(request)
        assert str(request.url) == "http://10.0.0.1:8001/api/v2/"
        if outcome == "refused":
            raise httpx.ConnectError("Connection refused", request=request)
        if outcome == "http_error":
            return httpx.Response(404)
        if outcome == "invalid_json":
            return httpx.Response(200, text="not JSON")
        return httpx.Response(200, json=(
            {"device": {"type": "Samsung SmartTV", "name": "TV"}} if outcome == "valid" else {"other": True}
        ))

    mock_http(monkeypatch, respond)
    before = devices.path.read_bytes()
    started = datetime.now(UTC)
    transport = httpx.ASGITransport(app=ApiService(kernel).app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(f"/devices/{device.id}/targets/{target.target_id}/probe")
        manifest = await client.get(f"/targets/{target.target_id}/capabilities")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == expected
    assert body["device_id"] == str(device.id)
    assert body["target_id"] == target.target_id
    assert body["target_name"] == target.name
    assert body["display_name"] == target.display_name
    assert body["duration_ms"] > 0
    assert started <= datetime.fromisoformat(body["checked_at"]) <= datetime.now(UTC)
    assert "response" not in body and "data" not in body
    if expected == "supported":
        assert body["error"] is None
    else:
        assert body["error"]
    assert len(calls) == 1
    result, = service.get_results(target.target_id)
    assert result.probe_id == target.name
    assert result.status.value == expected
    assert result.response is None
    assert manifest.status_code == 200
    assert manifest.json()["capabilities"][0]["status"] == expected
    assert devices.path.read_bytes() == before


@pytest.mark.asyncio
@pytest.mark.parametrize("messages,expected", [
    ([{"event": "ms.channel.connect"}], "supported"),
    ([{"event": "other"}, {"event": "ms.channel.connect"}], "supported"),
    ([], "no_response"),
    ([{"event": "other"}], "no_response"),
    ([{"event": "ms.channel.unauthorized"}], "rejected"),
])
async def test_remote_probe_waits_for_event_and_closes(setup, monkeypatch, messages, expected):
    kernel, _, _, service = setup
    device, target = await add_target(setup, "samsung.remote.control")
    connection = FakeConnection(messages)
    connect = AsyncMock(return_value=connection)
    monkeypatch.setattr(websocket, "connect", connect)
    transport = httpx.ASGITransport(app=ApiService(kernel).app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(f"/devices/{device.id}/targets/{target.target_id}/probe")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == expected
    assert body["matched_event"] == ("ms.channel.connect" if expected == "supported" else None)
    assert connect.await_args.args[0].startswith(
        "wss://10.0.0.1:8002/api/v2/channels/samsung.remote.control?name="
    )
    connection.send.assert_not_awaited()
    connection.close.assert_awaited_once()
    assert not any(task.get_name() == f"websocket-receiver:{target.target_id}" for task in asyncio.all_tasks())
    result, = service.get_results(target.target_id)
    assert result.matched_event == body["matched_event"]
    assert result.duration_ms > 0


@pytest.mark.asyncio
@pytest.mark.parametrize("missing", ["device", "target", "other_device", "target_name", "display_name"])
async def test_probe_requires_device_and_its_target_id(setup, monkeypatch, missing):
    kernel, devices, _, service = setup
    device, target = await add_target(setup, "samsung.api.v2")
    device_id = device.id
    target_id = target.target_id
    if missing == "device":
        device_id = uuid4()
    elif missing == "target":
        target_id = "unconfigured.target"
    elif missing == "display_name":
        target_id = target.display_name
    elif missing == "target_name":
        target_id = target.name
    else:
        other = await devices.create(Device(
            device_name="other", display_name="Other", ip_address="10.0.0.2",
        ))
        device_id = other.id
    probe = AsyncMock()
    monkeypatch.setattr(service, "probe", probe)
    transport = httpx.ASGITransport(app=ApiService(kernel).app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(f"/devices/{device_id}/targets/{target_id}/probe")
    assert response.status_code == 404
    probe.assert_not_awaited()
    assert service.get_results(target.target_id) == ()


@pytest.mark.asyncio
@pytest.mark.parametrize("name", ["some.target", "samsung.remote.home"])
async def test_unknown_existence_probe_is_controlled_error(setup, name):
    kernel, _, _, service = setup
    device, target = await add_target(setup, name)
    transport = httpx.ASGITransport(app=ApiService(kernel).app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(f"/devices/{device.id}/targets/{target.target_id}/probe")
    assert response.status_code == 200
    assert response.json()["status"] == "error"
    assert response.json()["error"] == f"No probe implementation registered for target '{name}'"
    assert service.get_results(target.target_id)[0].status == CapabilityStatus.ERROR


@pytest.mark.asyncio
async def test_connection_timeout_is_bounded_and_disconnects(setup, monkeypatch):
    _, _, _, service = setup
    device, target = await add_target(setup, "samsung.remote.control")

    async def hang(*args, **kwargs):
        await asyncio.Event().wait()

    monkeypatch.setattr(websocket, "connect", hang)
    disconnect = AsyncMock()
    monkeypatch.setattr(websocket.WebSocketTransport, "disconnect", disconnect)
    result = await asyncio.wait_for(service.probe(device, target), timeout=1)
    assert result.status == CapabilityStatus.NO_RESPONSE
    assert result.error
    disconnect.assert_awaited_once()


@pytest.mark.asyncio
async def test_probe_errors_are_stored_and_connection_cleanup_runs(setup, monkeypatch):
    _, _, _, service = setup
    device, target = await add_target(setup, "samsung.remote.control")
    monkeypatch.setattr(websocket, "connect", AsyncMock(side_effect=RuntimeError("Unexpected failure")))
    disconnect = AsyncMock()
    monkeypatch.setattr(websocket.WebSocketTransport, "disconnect", disconnect)
    result = await service.probe(device, target)
    assert result.status == CapabilityStatus.ERROR
    assert result.error == "Samsung WebSocket connection failed"
    assert service.get_results(target.target_id) == (result,)
    disconnect.assert_awaited_once()



@pytest.mark.asyncio
async def test_http_probe_timeout_and_existing_probe_route(setup, monkeypatch):
    kernel, _, _, service = setup
    device, target = await add_target(setup, "samsung.api.v2")

    async def stall(request):
        await asyncio.Event().wait()

    mock_http(monkeypatch, stall)
    result = await asyncio.wait_for(service.probe(device, target), timeout=1)
    assert result.status == CapabilityStatus.NO_RESPONSE

    mock_http(monkeypatch, lambda request: httpx.Response(200, json={
        "device": {"type": "Samsung SmartTV"},
    }))
    transport = httpx.ASGITransport(app=ApiService(kernel).app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(f"/targets/{target.target_id}/capabilities/probes/samsung.api.v2")
    assert response.status_code == 200
    assert response.json()["status"] == "supported"
    latest, = service.get_results(target.target_id)
    assert latest.result_id != result.result_id
    assert latest.status == CapabilityStatus.SUPPORTED


@pytest.mark.asyncio
async def test_receiver_is_cancelled_even_if_close_fails(setup, monkeypatch):
    _, _, _, service = setup
    device, target = await add_target(setup, "samsung.remote.control")
    connection = FakeConnection([{"event": "ms.channel.connect"}])
    connection.close.side_effect = RuntimeError("Close failed")
    monkeypatch.setattr(websocket, "connect", AsyncMock(return_value=connection))
    result = await service.probe(device, target)
    assert result.status == CapabilityStatus.ERROR
    assert result.error == "Samsung WebSocket connection failed"
    assert not any(task.get_name() == f"websocket-receiver:{target.target_id}" for task in asyncio.all_tasks())


def saved_token(devices, device):
    return devices.get(device.id).authentication.samsung.token.get_secret_value()


@pytest.mark.asyncio
async def test_pair_persist_restart_reuse_and_api_privacy(setup, monkeypatch, caplog):
    import logging
    from urllib.parse import parse_qs, urlsplit

    kernel, devices, _, service = setup
    device, target = await add_target(setup, "samsung.remote.control")
    other = await devices.create(Device(device_name="other", display_name="Other", ip_address="10.0.0.2"))
    secret = "pairing-secret+&"
    first = FakeConnection([{"event": "ms.channel.connect", "data": {"token": secret}}])
    second = FakeConnection([{"event": "ms.channel.connect"}])
    connect = AsyncMock(side_effect=[first, second])
    monkeypatch.setattr(websocket, "connect", connect)
    caplog.set_level(logging.DEBUG)
    assert (await service.probe(device, target)).status == CapabilityStatus.SUPPORTED
    assert saved_token(devices, device) == secret
    assert devices.get(other.id).authentication is None
    document = json.loads(devices.path.read_text())
    assert document["devices"][0]["authentication"]["samsung"]["token"] == secret
    assert "authentication" not in devices.get(device.id).model_dump()
    assert secret not in repr(devices.get(device.id))
    first.close.assert_awaited_once()
    # A stale caller snapshot still gets the latest registry authentication.
    assert (await service.probe(device, target)).status == CapabilityStatus.SUPPORTED
    queries = [parse_qs(urlsplit(call.args[0]).query) for call in connect.await_args_list]
    assert "token" not in queries[0]
    assert queries[1]["token"] == [secret]
    assert queries[0]["name"] == queries[1]["name"]
    second.close.assert_awaited_once()
    assert saved_token(devices, device) == secret
    restored = DeviceRegistry(kernel, devices.path)
    await restored.start()
    try:
        assert saved_token(restored, device) == secret
        assert restored.get(other.id).authentication is None
    finally:
        await restored.stop()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=ApiService(kernel).app), base_url="http://test") as client:
        for path in ("/devices", f"/devices/{device.id}"):
            response = await client.get(path)
            assert response.status_code == 200
            assert secret not in response.text
            assert "authentication" not in response.text
    assert secret not in caplog.text
    logger = connect.await_args.kwargs["logger"]
    assert not logger.isEnabledFor(logging.DEBUG)


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["replacement", "rejected", "timeout", "network", "empty"])
async def test_saved_token_recovery_is_bounded(setup, monkeypatch, caplog, outcome):
    from urllib.parse import parse_qs, urlsplit
    from stevie_explorer.devices.models import DeviceAuthentication, SamsungAuthentication

    _, devices, _, service = setup
    device, target = await add_target(setup, "samsung.remote.control")
    monkeypatch.setattr(service_module, "PROBE_REGISTRY", ProbeRegistry(
        tuple(replace(probe, timeout=0.3) for probe in SAMSUNG_PROBES), (),
    ))
    secret = "old-secret"
    await devices.update_authentication(device.id, DeviceAuthentication(samsung=SamsungAuthentication(token=secret)))
    before = devices.path.read_bytes()
    rejected = FakeConnection([{"event": "ms.channel.unauthorized"}])
    if outcome in {"replacement", "rejected"}:
        last = FakeConnection([{"event": "ms.channel.connect", "data": {"token": "new-secret"}}]) if outcome == "replacement" else FakeConnection([{"event": "ms.channel.unauthorized"}])
        connect = AsyncMock(side_effect=[rejected, last])
    elif outcome == "network":
        connect = AsyncMock(side_effect=OSError(f"Failed URL token={secret}"))
    else:
        connect = AsyncMock(return_value=FakeConnection([] if outcome == "timeout" else [{"event": "ms.channel.connect", "data": {"token": ""}}]))
    monkeypatch.setattr(websocket, "connect", connect)
    result = await service.probe(device, target)
    assert result.status.value == {"replacement": "supported", "rejected": "rejected", "timeout": "no_response", "network": "no_response", "empty": "supported"}[outcome]
    assert connect.await_count == (2 if outcome in {"replacement", "rejected"} else 1)
    assert parse_qs(urlsplit(connect.await_args_list[0].args[0]).query)["token"] == [secret]
    if connect.await_count == 2:
        assert "token" not in parse_qs(urlsplit(connect.await_args_list[1].args[0]).query)
        rejected.close.assert_awaited_once()
        last.close.assert_awaited_once()
    if outcome == "replacement":
        assert saved_token(devices, device) == "new-secret"
        assert json.loads(devices.path.read_text())["devices"][0]["authentication"]["samsung"]["token"] == "new-secret"
    else:
        assert saved_token(devices, device) == secret
        assert devices.path.read_bytes() == before
    assert secret not in (result.error or "")
    assert secret not in caplog.text


@pytest.mark.parametrize("message,expected", [
    ({"event": "ms.channel.connect", "data": {"token": "123"}}, "123"),
    ({"event": "ms.channel.connect"}, None),
    ({"event": "ms.channel.connect", "data": None}, None),
    ({"event": "ms.channel.connect", "data": {"token": " "}}, None),
    ({"event": "ms.channel.connect", "data": {"token": 123}}, None),
    ({"event": "other", "data": {"token": "123"}}, None),
])
def test_extract_samsung_token(message, expected):
    assert samsung_client.extract_token(message) == expected


@pytest.mark.asyncio
async def test_authentication_save_failure_rolls_back(setup, monkeypatch):
    _, devices, _, service = setup
    device, target = await add_target(setup, "samsung.remote.control")
    before = devices.path.read_bytes()
    version = devices.version
    monkeypatch.setattr(devices, "_atomic_write", lambda content: (_ for _ in ()).throw(OSError("write failed")))
    connection = FakeConnection([{"event": "ms.channel.connect", "data": {"token": "unsaved-secret"}}])
    monkeypatch.setattr(websocket, "connect", AsyncMock(return_value=connection))
    result = await service.probe(device, target)
    assert result.status != CapabilityStatus.SUPPORTED
    assert devices.get(device.id).authentication is None
    assert devices.version == version
    assert devices.path.read_bytes() == before
    connection.close.assert_awaited_once()
