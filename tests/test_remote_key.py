import asyncio
import json
from dataclasses import replace
from unittest.mock import AsyncMock, Mock
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

import httpx
import pytest

from test_target_probes import setup as key_setup, add_target, FakeConnection
from test_capability_exploration import AppConnection
from stevie_explorer.api import ApiService
from stevie_explorer.capabilities.models import ProbeSafety
from stevie_explorer.capabilities.probes.samsung import CAPABILITY_CATALOGUE, REMOTE_KEY
from stevie_explorer.capabilities.registry import CapabilityCatalogue
from stevie_explorer.devices import Device
from stevie_explorer.devices.models import DeviceAuthentication, SamsungAuthentication
from stevie_explorer.devices.samsung import client as samsung_client
from stevie_explorer.devices.samsung.keys import SAMSUNG_REMOTE_KEYS
from stevie_explorer.transports import websocket

setup = key_setup


@pytest.fixture(autouse=True)
def short_timeouts(setup, monkeypatch):
    catalogue = CapabilityCatalogue()
    catalogue.register('samsung.remote.control', tuple(
        replace(probe, timeout=0.3, strategies=tuple(
            replace(strategy, timeout=0.02) for strategy in probe.strategies
        )) for probe in CAPABILITY_CATALOGUE.get('samsung.remote.control')
    ))
    setup[3].catalogue = catalogue
    monkeypatch.setattr(samsung_client, 'REMOTE_KEY_ERROR_WINDOW', 0.03)


class KeyConnection(FakeConnection):
    def __init__(self, reply=(), token=None):
        super().__init__([])
        self.reply = reply
        self.token = token
        self.sent = asyncio.Event()
        self.send = AsyncMock(side_effect=lambda frame: self.sent.set())

    async def __aiter__(self):
        yield json.dumps({'event': 'ms.channel.connect', 'data': {'token': self.token}})
        await self.sent.wait()
        await asyncio.sleep(0.005)
        for message in self.reply:
            if message is None:
                return
            yield message if isinstance(message, str) else json.dumps(message)
        await asyncio.Event().wait()


def test_remote_key_catalogue_is_active_and_conservative():
    assert CAPABILITY_CATALOGUE.get_capability('samsung.remote.control', 'remote.key') is REMOTE_KEY
    assert REMOTE_KEY.name == 'Remote Key'
    assert REMOTE_KEY.safety == ProbeSafety.STATE_CHANGE
    assert REMOTE_KEY.payload is None
    assert REMOTE_KEY.command is None
    assert {command.name for command in REMOTE_KEY.commands} == set(SAMSUNG_REMOTE_KEYS)
    assert len(SAMSUNG_REMOTE_KEYS) == 30
    assert 'KEY_POWER' not in SAMSUNG_REMOTE_KEYS


@pytest.mark.asyncio
async def test_bulk_exploration_never_sends_remote_keys(setup, monkeypatch):
    kernel, _, _, service = setup
    device, target = await add_target(setup, 'samsung.remote.control')
    first = FakeConnection([{'event': 'ms.channel.connect'}])
    second = AppConnection([{'event': 'ed.installedApp.get', 'data': []}])
    connect = AsyncMock(side_effect=[first, second])
    monkeypatch.setattr(websocket, 'connect', connect)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=ApiService(kernel).app), base_url='http://test') as client:
        response = await client.post(f'/devices/{device.id}/targets/{target.target_id}/explore')
    assert response.status_code == 200
    assert {result.probe_id for result in service.get_results(target.target_id)} == {'apps.list', 'remote.connection'}
    first.send.assert_not_awaited()
    second.send.assert_awaited_once()
    assert json.loads(second.send.await_args.args[0])['method'] == 'ms.channel.emit'
    assert connect.await_count == 2


@pytest.mark.asyncio
@pytest.mark.parametrize('key', ['KEY_HOME', 'KEY_VOLUP', 'KEY_MUTE'])
async def test_explicit_key_send_and_read_only_results(setup, monkeypatch, key):
    kernel, devices, targets, service = setup
    device, target = await add_target(setup, 'samsung.remote.control')
    await devices.update_authentication(device.id, DeviceAuthentication(samsung=SamsungAuthentication(token='stored-secret')))
    before = devices.path.read_bytes()
    lookup = Mock(wraps=targets.get)
    monkeypatch.setattr(targets, 'get', lookup)
    connection = KeyConnection()
    connect = AsyncMock(return_value=connection)
    monkeypatch.setattr(websocket, 'connect', connect)
    path = f'/devices/{device.id}/targets/{target.target_id}'
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=ApiService(kernel).app), base_url='http://test') as client:
        # The generic exploration route must not choose a key, even with a body.
        for body in (None, {'key': key}):
            response = await client.post(path + '/explore/remote.key', json=body)
            assert response.status_code == 422
            assert 'explicit command' in response.json()['detail']
        connect.assert_not_awaited()
        assert service.get_results(target.target_id) == ()
        response = await client.post(path + '/capabilities/remote.key/test', json={'keys': [key]})
        assert response.status_code == 200
        assert response.json()['results'][0]['status'] == 'supported'
        assert response.json()['capability_id'] == 'remote.key'
        # No key acknowledgement or independent physical confirmation was received.
        assert service.get_capability_result(target.target_id, 'remote.key').matched_event is None
        assert response.json()['results'][0]['error'] is None
        result = service.get_capability_result(target.target_id, 'remote.key')
        for _ in range(2):
            assert (await client.get(path + '/capabilities/remote.key')).json()['status'] == 'supported'
            assert (await client.get(path + '/capabilities')).json()['capabilities'][0]['capability_id'] == 'remote.key'
            commands = await client.get(path + '/capabilities/remote.key/commands')
            assert commands.status_code == 200
            discovered = {item['key']: item for item in commands.json()['commands']}
            assert discovered[key]['status'] == 'supported'
            assert discovered['KEY_GUIDE']['status'] == 'not_tested'
            assert discovered['KEY_GUIDE']['last_checked'] is None
        assert service.get_capability_result(target.target_id, 'remote.key') is result
    connect.assert_awaited_once()
    connection.send.assert_awaited_once()
    assert json.loads(connection.send.await_args.args[0]) == {
        'method': 'ms.remote.control',
        'params': {'Cmd': 'Click', 'DataOfCmd': key, 'Option': 'false', 'TypeOfRemote': 'SendRemoteKey'},
    }
    assert parse_qs(urlsplit(connect.await_args.args[0]).query)['token'] == ['stored-secret']
    lookup.assert_called_with(target.target_id)
    connection.close.assert_awaited_once()
    assert devices.path.read_bytes() == before
    assert 'stored-secret' not in response.text
    assert not any(task.get_name().startswith('websocket-receiver:') for task in asyncio.all_tasks())


@pytest.mark.asyncio
@pytest.mark.parametrize('body,status', [
    ({}, 422), ({'keys': []}, 422), ({'keys': ['']}, 422),
    ({'keys': [' \t ']}, 422), ({'keys': [None]}, 422), ({'keys': [42]}, 422),
    ({'keys': ['KEY_HOME'], 'repeat': 10}, 422),
    ({'keys': ['KEY_HOME', 'KEY_HOME']}, 422), ({'key': 'KEY_HOME'}, 422),
    ({'keys': ['KEY_POWER']}, 400), ({'keys': ['KEY_SOMETHING']}, 400),
    ({'keys': ['key_home']}, 400),
])
async def test_invalid_keys_never_connect(setup, monkeypatch, body, status):
    kernel, devices, _, service = setup
    device, target = await add_target(setup, 'samsung.remote.control')
    before = devices.path.read_bytes()
    connect = AsyncMock()
    monkeypatch.setattr(websocket, 'connect', connect)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=ApiService(kernel).app), base_url='http://test') as client:
        response = await client.post(f'/devices/{device.id}/targets/{target.target_id}/capabilities/remote.key/test', json=body)
    assert response.status_code == status
    connect.assert_not_awaited()
    assert service.get_results(target.target_id) == ()
    assert devices.path.read_bytes() == before


@pytest.mark.asyncio
@pytest.mark.parametrize('missing', ['device', 'target', 'other_device', 'target_name', 'wrong_type', 'unregistered'])
async def test_key_routes_validate_hierarchy_and_capability(setup, monkeypatch, missing):
    kernel, devices, _, service = setup
    device, target = await add_target(setup, 'unknown.interface' if missing == 'wrong_type' else 'samsung.remote.control')
    device_id, target_id = device.id, target.target_id
    if missing == 'device':
        device_id = uuid4()
    elif missing == 'target':
        target_id = str(uuid4())
    elif missing == 'target_name':
        target_id = target.name
    elif missing == 'other_device':
        other = await devices.create(Device(device_name='other', display_name='Other', ip_address='10.0.0.2'))
        device_id = other.id
    elif missing == 'unregistered':
        service.catalogue = CapabilityCatalogue()
        service.catalogue.register(target.name, ())
    connect = AsyncMock()
    monkeypatch.setattr(websocket, 'connect', connect)
    path = f'/devices/{device_id}/targets/{target_id}/capabilities/remote.key'
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=ApiService(kernel).app), base_url='http://test') as client:
        assert (await client.post(path + '/test', json={'keys': ['KEY_HOME']})).status_code == 404
        assert (await client.get(path + '/commands')).status_code == 404
    connect.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize('reply,status', [
    ([{'event': 'ms.error', 'data': {'message': 'private protocol failure'}}], 'unsupported'),
    ([{'params': {'event': 'ms.remote.control', 'error': 'rejected'}}], 'unsupported'),
    ([{'event': 'ms.channel.unauthorized'}], 'error'),
    ([None], 'error'),
    (['invalid JSON'], 'error'),
    ([{'event': 'ed.edenTV.update'}], 'supported'),
])
async def test_immediate_key_errors_are_stored_without_retry(setup, monkeypatch, reply, status):
    kernel, _, _, service = setup
    device, target = await add_target(setup, 'samsung.remote.control')
    connection = KeyConnection(reply)
    connect = AsyncMock(return_value=connection)
    monkeypatch.setattr(websocket, 'connect', connect)
    path = f'/devices/{device.id}/targets/{target.target_id}/capabilities/remote.key'
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=ApiService(kernel).app), base_url='http://test') as client:
        response = await client.post(path + '/test', json={'keys': ['KEY_HOME']})
        assert response.status_code == 200
        assert response.json()['results'][0]['status'] == status
        assert (await client.get(path)).json()['status'] == status
    assert service.get_capability_result(target.target_id, 'remote.key').status.value == status
    assert 'private protocol failure' not in response.text
    connect.assert_awaited_once()
    connection.send.assert_awaited_once()
    connection.close.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize('replacement', [True, False])
async def test_key_authentication_reuses_existing_recovery(setup, monkeypatch, replacement):
    kernel, devices, _, service = setup
    device, target = await add_target(setup, 'samsung.remote.control')
    await devices.update_authentication(device.id, DeviceAuthentication(samsung=SamsungAuthentication(token='old-secret')))
    first = FakeConnection([{'event': 'ms.channel.unauthorized'}])
    second = KeyConnection(token='new-secret') if replacement else FakeConnection([{'event': 'ms.channel.unauthorized'}])
    connect = AsyncMock(side_effect=[first, second])
    monkeypatch.setattr(websocket, 'connect', connect)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=ApiService(kernel).app), base_url='http://test') as client:
        response = await client.post(f'/devices/{device.id}/targets/{target.target_id}/capabilities/remote.key/test', json={'keys': ['KEY_HOME']})
    assert response.status_code == 200
    assert response.json()['results'][0]['status'] == ('supported' if replacement else 'error')
    assert connect.await_count == 2
    assert parse_qs(urlsplit(connect.await_args_list[0].args[0]).query)['token'] == ['old-secret']
    assert 'token' not in parse_qs(urlsplit(connect.await_args_list[1].args[0]).query)
    first.send.assert_not_awaited()
    assert second.send.await_count == (1 if replacement else 0)
    first.close.assert_awaited_once()
    second.close.assert_awaited_once()
    document = json.loads(devices.path.read_text())
    assert document['devices'][0]['authentication']['samsung']['token'] == ('new-secret' if replacement else 'old-secret')
    assert 'KEY_HOME' not in devices.path.read_text()
    assert service.get_capability_result(target.target_id, 'remote.key').status.value == response.json()['results'][0]['status']


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', ['connect', 'send', 'timeout'])
async def test_key_network_failures_are_controlled(setup, monkeypatch, failure):
    kernel, _, _, _ = setup
    device, target = await add_target(setup, 'samsung.remote.control')
    connection = KeyConnection()
    connect = AsyncMock(return_value=connection)
    if failure == 'connect':
        connect.side_effect = OSError('private connection URL')
    elif failure == 'send':
        connection.send.side_effect = OSError('private connection URL')
    else:
        async def stall(frame):
            await asyncio.Event().wait()
        connection.send.side_effect = stall
    monkeypatch.setattr(websocket, 'connect', connect)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=ApiService(kernel).app), base_url='http://test') as client:
        response = await client.post(f'/devices/{device.id}/targets/{target.target_id}/capabilities/remote.key/test', json={'keys': ['KEY_HOME']})
    assert response.status_code == 200
    assert response.json()['results'][0]['status'] == 'error'
    assert 'private connection URL' not in response.text
    connect.assert_awaited_once()
    if failure != 'connect':
        connection.send.assert_awaited_once()
        connection.close.assert_awaited_once()
    assert not any(task.get_name().startswith('websocket-receiver:') for task in asyncio.all_tasks())


@pytest.mark.asyncio
@pytest.mark.parametrize('reply,expected,sends', [
    ([{'event': 'ms.error'}], ['unsupported', 'supported', 'supported'], 3),
    ([None], ['error', 'error', 'error'], 1),
    ([], ['supported', 'supported', 'supported'], 3),
])
async def test_batch_order_delay_persistence_and_restart(setup, monkeypatch, reply, expected, sends, caplog):
    from time import perf_counter
    from stevie_explorer.capabilities import CapabilityProbeService

    kernel, devices, _, service = setup
    device, target = await add_target(setup, 'samsung.remote.control')
    await devices.update_authentication(device.id, DeviceAuthentication(samsung=SamsungAuthentication(token='secret-token')))
    before = devices.path.read_bytes()
    persist_devices = AsyncMock(wraps=devices._persist)
    monkeypatch.setattr(devices, '_persist', persist_devices)
    monkeypatch.setattr(samsung_client, 'REMOTE_KEY_DELAY', 0.04)
    connection = KeyConnection(reply)
    times = []

    async def send(frame):
        times.append(perf_counter())
        connection.sent.set()

    connection.send.side_effect = send
    connect = AsyncMock(return_value=connection)
    monkeypatch.setattr(websocket, 'connect', connect)
    keys = ['KEY_HOME', 'KEY_RETURN', 'KEY_ENTER']
    path = f'/devices/{device.id}/targets/{target.target_id}/capabilities/remote.key'
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=ApiService(kernel).app), base_url='http://test') as client:
        initial = await client.get(path + '/commands')
        assert all(item['status'] == 'not_tested' for item in initial.json()['commands'])
        assert not service.state.path.exists()
        response = await client.post(path + '/test', json={'keys': keys})
        assert response.status_code == 200
        results = response.json()['results']
        assert [r['key'] for r in results] == keys
        assert [r['status'] for r in results] == expected
        assert all(r['last_checked'] and r['duration_ms'] >= 0 for r in results)
        assert (await client.get(path)).json()['status'] == ('supported' if 'supported' in expected else 'error')
    connect.assert_awaited_once()
    assert parse_qs(urlsplit(connect.await_args.args[0]).query)['token'] == ['secret-token']
    assert [json.loads(call.args[0])['params']['DataOfCmd'] for call in connection.send.await_args_list] == keys[:sends]
    assert all(b - a >= 0.04 for a, b in zip(times, times[1:]))
    connection.close.assert_awaited_once()
    persist_devices.assert_not_awaited()
    assert devices.path.read_bytes() == before
    content = service.state.path.read_text()
    assert 'secret-token' not in content + caplog.text
    saved = json.loads(content)['devices'][str(device.id)]['targets'][target.target_id]['capabilities']['remote.key']['commands']
    assert [saved[key]['status'] for key in keys] == expected
    restored = CapabilityProbeService(kernel, state_path=service.state.path)
    await restored.start()
    assert restored.state.get_commands(str(device.id), target.target_id, 'remote.key') == service.state.get_commands(str(device.id), target.target_id, 'remote.key')
    # Serve restored discoveries without any network activity.
    service._state = restored.state
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=ApiService(kernel).app), base_url='http://test') as client:
        discovered = (await client.get(path + '/commands')).json()['commands']
        assert {r['key']: r['status'] for r in discovered}['KEY_RETURN'] == expected[1]
    connect.assert_awaited_once()
    # Retest one key: replace its latest result while preserving other commands.
    monkeypatch.setattr(websocket, 'connect', AsyncMock(return_value=KeyConnection()))
    await service.test_capability_commands(device, target, 'remote.key', ['KEY_HOME'])
    updated = service.state.get_commands(str(device.id), target.target_id, 'remote.key')
    assert len(updated) == 3
    assert updated['KEY_HOME'].status.value == 'supported'
    assert updated['KEY_HOME'].last_checked.isoformat() != saved['KEY_HOME']['last_checked']
    assert updated['KEY_RETURN'].model_dump(mode='json') == saved['KEY_RETURN']


@pytest.mark.asyncio
async def test_all_invalid_keys_reported_before_send(setup, monkeypatch):
    kernel, _, _, service = setup
    device, target = await add_target(setup, 'samsung.remote.control')
    connect = AsyncMock()
    monkeypatch.setattr(websocket, 'connect', connect)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=ApiService(kernel).app), base_url='http://test') as client:
        response = await client.post(f'/devices/{device.id}/targets/{target.target_id}/capabilities/remote.key/test',
                                     json={'keys': ['KEY_HOME', 'KEY_BAD', 'KEY_POWER']})
    assert response.status_code == 400
    assert 'KEY_BAD' in response.text and 'KEY_POWER' in response.text
    connect.assert_not_awaited()
    assert not service.state.path.exists()


@pytest.mark.asyncio
async def test_persistence_failure_is_atomic_and_does_not_retry(setup, monkeypatch):
    kernel, _, _, service = setup
    device, target = await add_target(setup, 'samsung.remote.control')
    connection = KeyConnection()
    monkeypatch.setattr(websocket, 'connect', AsyncMock(return_value=connection))
    await service.test_capability_commands(device, target, 'remote.key', ['KEY_HOME'])
    before = service.state.path.read_bytes()
    import stevie_explorer.capabilities.state as state_module
    monkeypatch.setattr(state_module.os, 'replace', Mock(side_effect=OSError('disk failure')))
    connection = KeyConnection()
    monkeypatch.setattr(websocket, 'connect', AsyncMock(return_value=connection))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=ApiService(kernel).app), base_url='http://test') as client:
        response = await client.post(f'/devices/{device.id}/targets/{target.target_id}/capabilities/remote.key/test', json={'keys': ['KEY_ENTER']})
    assert response.status_code == 503
    assert 'do not automatically retry' in response.text
    assert service.state.path.read_bytes() == before
    assert set(service.state.get_commands(str(device.id), target.target_id, 'remote.key')) == {'KEY_HOME'}
    connection.send.assert_awaited_once()
    connection.close.assert_awaited_once()
    assert not list(service.state.path.parent.glob('.capabilities.json.*'))
