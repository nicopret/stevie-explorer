import asyncio
import json
from dataclasses import replace
from unittest.mock import AsyncMock, Mock
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

import httpx
import pytest

from test_target_probes import setup as exploration_setup, add_target, FakeConnection

from stevie_explorer.api import ApiService
from stevie_explorer.capabilities.models import ProbeSafety
from stevie_explorer.capabilities.probes.samsung import CAPABILITY_CATALOGUE
from stevie_explorer.capabilities.registry import CapabilityCatalogue
from stevie_explorer.devices import Device
from stevie_explorer.devices.models import DeviceAuthentication, SamsungAuthentication
from stevie_explorer.targets import Target
from stevie_explorer.transports import websocket

setup = exploration_setup


@pytest.fixture(autouse=True)
def short_catalogue(setup):
    service = setup[3]
    service.catalogue = CapabilityCatalogue()
    service.catalogue.register('samsung.remote.control', tuple(
        replace(probe, timeout=0.2, strategies=tuple(
            replace(strategy, timeout=0.03) for strategy in probe.strategies
        ))
        for probe in CAPABILITY_CATALOGUE.get('samsung.remote.control')
    ))


class AppConnection(FakeConnection):
    def __init__(self, reply):
        super().__init__([])
        self.sent = asyncio.Event()
        self.reply = reply
        self.send = AsyncMock(side_effect=self.on_send)

    async def on_send(self, frame):
        assert json.loads(frame) == {
            'method': 'ms.channel.emit',
            'params': {'event': ('ed.installedApp.get' if self.send.await_count == 1 else 'ed.edenApp.get'), 'to': 'host', 'data': ''},
        }
        self.sent.set()

    async def __aiter__(self):
        yield json.dumps({'event': 'ms.channel.connect'})
        # An unsolicited response before the request must not prove support.
        yield json.dumps({'event': 'ed.installedApp.get'})
        await self.sent.wait()
        for message in self.reply:
            yield message if isinstance(message, str) else json.dumps(message)
        await asyncio.Event().wait()


@pytest.mark.asyncio
@pytest.mark.parametrize('reply,expected', [
    ([{'event': 'ed.installedApp.get', 'data': {'data': []}}], 'supported'),
    ([], 'no_response'),
    ([{'event': 'unrelated'}], 'no_response'),
    ([{'event': 'ed.installedApp.get', 'error': 'unavailable'}], 'error'),
    (['invalid json'], 'error'),
])
async def test_explore_and_get(setup, monkeypatch, reply, expected):
    kernel, devices, targets, service = setup
    device, target = await add_target(setup, 'samsung.remote.control')
    await devices.update_authentication(device.id, DeviceAuthentication(
        samsung=SamsungAuthentication(token='stored-secret'),
    ))
    before = devices.path.read_bytes()
    first = FakeConnection([{'event': 'ms.channel.connect'}])
    second = AppConnection(reply)
    connect = AsyncMock(side_effect=[first, second])
    monkeypatch.setattr(websocket, 'connect', connect)
    path = f'/devices/{device.id}/targets/{target.target_id}'
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=ApiService(kernel).app), base_url='http://test') as client:
        assert (await client.get(path + '/capabilities')).json()['capabilities'] == []
        response = await client.post(path + '/explore')
        nested = await client.get(path + '/capabilities')
        legacy = await client.get(f'/targets/{target.target_id}/capabilities')
    assert response.status_code == 200
    results = {r['capability_id']: r for r in response.json()['capabilities']}
    assert results['remote.connection']['status'] == 'supported'
    assert results['remote.connection']['matched_event'] == 'ms.channel.connect'
    assert results['apps.list']['status'] == expected
    assert results['apps.list']['matched_event'] == ('ed.installedApp.get' if expected == 'supported' else None)
    assert nested.json()['capabilities'] == legacy.json()['capabilities'] == response.json()['capabilities']
    assert len(service.get_results(target.target_id)) == 2
    assert targets.list() == (target,)
    assert devices.path.read_bytes() == before
    assert connect.await_count == 2
    for call in connect.await_args_list:
        assert parse_qs(urlsplit(call.args[0]).query)['token'] == ['stored-secret']
    first.send.assert_not_awaited()
    assert second.send.await_count == (1 if expected == 'supported' or reply == ['invalid json'] else 2)
    first.close.assert_awaited_once()
    second.close.assert_awaited_once()
    assert not any(t.get_name().startswith('websocket-receiver:') for t in asyncio.all_tasks())


@pytest.mark.asyncio
@pytest.mark.parametrize('missing', ['device', 'target', 'other_device', 'target_name', 'display_name'])
async def test_target_endpoints_validate_hierarchy(setup, monkeypatch, missing):
    kernel, devices, _, service = setup
    device, target = await add_target(setup, 'samsung.remote.control')
    device_id, target_id = device.id, target.target_id
    if missing == 'device':
        device_id = uuid4()
    elif missing == 'target':
        target_id = 'missing'
    elif missing == 'target_name':
        target_id = target.name
    elif missing == 'display_name':
        target_id = target.display_name
    else:
        other = await devices.create(Device(device_name='other', display_name='Other', ip_address='10.0.0.2'))
        device_id = other.id
    explore = AsyncMock()
    monkeypatch.setattr(service, 'explore', explore)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=ApiService(kernel).app), base_url='http://test') as client:
        path = f'/devices/{device_id}/targets/{target_id}'
        assert (await client.post(path + '/explore')).status_code == 404
        assert (await client.get(path + '/capabilities')).status_code == 404
        assert (await client.post(path + '/probe')).status_code == 404
        assert (await client.post(path + '/explore/apps.list')).status_code == 404
        assert (await client.get(path + '/capabilities/apps.list')).status_code == 404
    explore.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize('suffix', ['', '/apps.list'])
async def test_unknown_explorer(setup, suffix):
    kernel, _, _, service = setup
    device, target = await add_target(setup, 'unknown.interface')
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=ApiService(kernel).app), base_url='http://test') as client:
        response = await client.post(f'/devices/{device.id}/targets/{target.target_id}/explore{suffix}')
    assert response.status_code == 422
    assert response.json()['detail'] == "No capability explorer registered for target 'unknown.interface'"
    assert service.get_results(target.target_id) == ()


@pytest.mark.asyncio
@pytest.mark.parametrize('safety', [ProbeSafety.STATE_CHANGE, ProbeSafety.DESTRUCTIVE])
async def test_failure_does_not_stop_other_probes_and_active_skipped(setup, monkeypatch, safety):
    kernel, _, _, service = setup
    device, target = await add_target(setup, 'samsung.remote.control')
    probes = service.catalogue.get(target.name)
    active = AsyncMock()
    catalogue = CapabilityCatalogue()
    catalogue.register(target.name, (*probes, replace(
        probes[0], probe_id='active', safety=safety, runner=active,
    )))
    service.catalogue = catalogue
    connection = AppConnection([{'event': 'ed.installedApp.get', 'data': []}])
    monkeypatch.setattr(websocket, 'connect', AsyncMock(side_effect=[OSError('secret URL'), connection]))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=ApiService(kernel).app), base_url='http://test') as client:
        response = await client.post(f'/devices/{device.id}/targets/{target.target_id}/explore')
    assert response.status_code == 200
    results = sorted(service.get_results(target.target_id), key=lambda result: result.probe_id, reverse=True)
    assert [r.status.value for r in results] == ['error', 'supported']
    assert 'secret' not in results[0].error
    active.assert_not_awaited()
    connection.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_id_post_probe_remains_only_target_probe(setup, monkeypatch):
    kernel, _, _, service = setup
    device, target = await add_target(setup, 'samsung.remote.control')
    connection = FakeConnection([{'event': 'ms.channel.connect'}])
    monkeypatch.setattr(websocket, 'connect', AsyncMock(return_value=connection))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=ApiService(kernel).app), base_url='http://test') as client:
        response = await client.post(f'/devices/{device.id}/targets/{target.target_id}/probe')
    assert response.status_code == 200
    assert response.json()['target_id'] == target.target_id
    assert response.json()['status'] == 'supported'
    assert [r.probe_id for r in service.get_results(target.target_id)] == [target.name]
    connection.send.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("string_device_id", [False, True])
async def test_target_endpoints_accept_device_id_representations(setup, monkeypatch, string_device_id):
    kernel, devices, targets, service = setup
    device = Device(device_name="tv", display_name="TV", ip_address="10.0.0.1")
    target = Target(
        name="samsung.remote.control",
        device_id=str(device.id) if string_device_id else device.id,
    )
    monkeypatch.setattr(devices, "get", lambda device_id: device)
    monkeypatch.setattr(targets, "get", lambda target_id: target)
    monkeypatch.setattr(websocket, "connect", AsyncMock(side_effect=lambda *args, **kwargs: AppConnection([{'event': 'ed.installedApp.get', 'data': []}])))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=ApiService(kernel).app), base_url="http://test") as client:
        path = f"/devices/{device.id}/targets/{target.target_id}"
        assert (await client.post(path + "/explore")).status_code == 200
        assert (await client.get(path + "/capabilities")).status_code == 200
        assert (await client.post(path + "/probe")).status_code == 200
        assert (await client.post(path + "/explore/apps.list")).json()["status"] == "supported"
        assert (await client.get(path + "/capabilities/apps.list")).json()["status"] == "supported"
    assert all(result.status.value == "supported" for result in service.get_results(target.target_id))


@pytest.mark.asyncio
@pytest.mark.parametrize('capability_id,event', [
    ('apps.list', 'ed.installedApp.get'),
    ('remote.connection', 'ms.channel.connect'),
])
async def test_explore_one_refreshes_only_requested_result(setup, monkeypatch, capability_id, event):
    kernel, devices, targets, service = setup
    device, target = await add_target(setup, 'samsung.remote.control')
    await devices.update_authentication(device.id, DeviceAuthentication(
        samsung=SamsungAuthentication(token='stored-secret'),
    ))
    before = devices.path.read_bytes()
    lookup = Mock(wraps=targets.get)
    monkeypatch.setattr(targets, 'get', lookup)
    connections = []

    def connection(*args, **kwargs):
        value = (AppConnection([{'event': event, 'data': []}]) if capability_id == 'apps.list'
                 else FakeConnection([{'event': event}]))
        connections.append(value)
        return value

    connect = AsyncMock(side_effect=connection)
    monkeypatch.setattr(websocket, 'connect', connect)
    path = f'/devices/{device.id}/targets/{target.target_id}'
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=ApiService(kernel).app), base_url='http://test') as client:
        missing = await client.get(path + f'/capabilities/{capability_id}')
        assert missing.status_code == 404
        connect.assert_not_awaited()
        first = await client.post(path + f'/explore/{capability_id}')
        assert first.status_code == 200
        first_result = service.get_capability_result(target.target_id, capability_id)
        assert len(service.get_results(target.target_id)) == 1
        assert first.json()['capability_id'] == capability_id
        assert first.json()['status'] == 'supported'
        assert first.json()['matched_event'] == event
        assert first.json()['device_id'] == str(device.id)
        assert first.json()['target_id'] == target.target_id
        for _ in range(2):
            stored = await client.get(path + f'/capabilities/{capability_id}')
            manifest = await client.get(path + '/capabilities')
            assert stored.json() == first.json()
            assert manifest.json()['capabilities'] == [{
                key: value for key, value in first.json().items()
                if key not in {'device_id', 'target_id'}
            }]
        connect.assert_awaited_once()
        second = await client.post(path + f'/explore/{capability_id}')
        assert second.status_code == 200
        second_result = service.get_capability_result(target.target_id, capability_id)
        assert second_result.result_id != first_result.result_id
        assert len(service.get_results(target.target_id)) == 1
        assert (await client.get(path + f'/capabilities/{capability_id}')).json() == second.json()
    assert connect.await_count == 2
    lookup.assert_called_with(target.target_id)
    assert target.name == 'samsung.remote.control'
    assert target.display_name == 'Human-readable label'
    assert devices.path.read_bytes() == before
    for call in connect.await_args_list:
        assert parse_qs(urlsplit(call.args[0]).query)['token'] == ['stored-secret']
    for value in connections:
        value.close.assert_awaited_once()
        if capability_id == 'apps.list':
            value.send.assert_awaited_once()
        else:
            value.send.assert_not_awaited()
    assert not any(t.get_name().startswith('websocket-receiver:') for t in asyncio.all_tasks())


@pytest.mark.asyncio
@pytest.mark.parametrize('capability_id', ['missing', 'ed.installedApp.get'])
async def test_unknown_capability_does_not_probe_or_store(setup, monkeypatch, capability_id):
    kernel, _, _, service = setup
    device, target = await add_target(setup, 'samsung.remote.control')
    connect = AsyncMock()
    monkeypatch.setattr(websocket, 'connect', connect)
    path = f'/devices/{device.id}/targets/{target.target_id}'
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=ApiService(kernel).app), base_url='http://test') as client:
        response = await client.post(path + f'/explore/{capability_id}')
        assert response.status_code == 404
        assert response.json()['detail'] == (
            f"Capability '{capability_id}' is not registered for target '{target.name}'"
        )
        assert (await client.get(path + f'/capabilities/{capability_id}')).status_code == 404
    connect.assert_not_awaited()
    assert service.get_results(target.target_id) == ()


def test_openapi_target_routes_use_ids(setup):
    paths = ApiService(setup[0]).app.openapi()['paths']
    assert not any('{target_name}' in path for path in paths)
    prefix = '/devices/{device_id}/targets/{target_id}'
    for suffix, method in [
        ('', 'delete'), ('/probe', 'post'), ('/explore', 'post'),
        ('/explore/{capability_id}', 'post'), ('/capabilities', 'get'),
        ('/capabilities/{capability_id}', 'get'),
    ]:
        assert method in paths[prefix + suffix]
    assert 'get' not in paths[prefix + '/probe']
    assert 'requestBody' not in paths[prefix + '/explore']['post']
    assert 'requestBody' not in paths[prefix + '/explore/{capability_id}']['post']


@pytest.mark.asyncio
async def test_single_remote_exploration_persists_new_token_for_apps(setup, monkeypatch):
    kernel, devices, _, service = setup
    device, target = await add_target(setup, 'samsung.remote.control')
    first = FakeConnection([{'event': 'ms.channel.connect', 'data': {'token': 'new-secret'}}])
    second = AppConnection([{'event': 'ed.installedApp.get', 'data': []}])
    connect = AsyncMock(side_effect=[first, second])
    monkeypatch.setattr(websocket, 'connect', connect)
    path = f'/devices/{device.id}/targets/{target.target_id}'
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=ApiService(kernel).app), base_url='http://test') as client:
        remote = await client.post(path + '/explore/remote.connection')
        assert remote.json()['status'] == 'supported'
        recorded_remote = service.get_capability_result(target.target_id, 'remote.connection')
        apps = await client.post(path + '/explore/apps.list')
        assert apps.json()['status'] == 'supported'
        assert service.get_capability_result(target.target_id, 'remote.connection') is recorded_remote
        assert (await client.get(path + '/capabilities/remote.connection')).json() == remote.json()
    saved = json.loads(devices.path.read_text())
    assert saved['devices'][0]['authentication']['samsung']['token'] == 'new-secret'
    assert 'token' not in parse_qs(urlsplit(connect.await_args_list[0].args[0]).query)
    assert parse_qs(urlsplit(connect.await_args_list[1].args[0]).query)['token'] == ['new-secret']
    assert 'new-secret' not in remote.text + apps.text
    assert len(service.get_results(target.target_id)) == 2
    first.close.assert_awaited_once()
    second.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_single_result_lookup_is_scoped_to_target(setup, monkeypatch):
    kernel, _, targets, service = setup
    device, target = await add_target(setup, 'samsung.remote.control')
    other = await targets.create(replace(target, target_id=str(uuid4())))
    monkeypatch.setattr(websocket, 'connect', AsyncMock(return_value=FakeConnection([
        {'event': 'ms.channel.connect'},
    ])))
    await service.explore_capability(device, target, 'remote.connection')
    before = dict(service._results)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=ApiService(kernel).app), base_url='http://test') as client:
        response = await client.get(f'/devices/{device.id}/targets/{other.target_id}/capabilities/remote.connection')
        assert response.status_code == 404
    assert service._results == before
    assert service.get_results(other.target_id) == ()
