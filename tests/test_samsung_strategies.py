import asyncio
import json
import logging
from dataclasses import replace
from unittest.mock import AsyncMock
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from test_target_probes import setup as strategy_setup, add_target
from stevie_explorer.api import ApiService
from stevie_explorer.capabilities.probes.samsung import INSTALLED_APPS
from stevie_explorer.capabilities.registry import CapabilityCatalogue
from stevie_explorer.devices.models import DeviceAuthentication, SamsungAuthentication
from stevie_explorer.devices.samsung.messages import extract_event_name, has_application_list
from stevie_explorer.transports import websocket

setup = strategy_setup
INSTALLED = 'ed.installedApp.get'
EDEN = 'ed.edenApp.get'


def app_response(event):
    return {'event': event, 'data': {'data': [{'appId': '123', 'name': 'App', 'app_type': 2}]}}


class StrategyConnection:
    def __init__(self, replies):
        self.replies = replies
        self.messages = asyncio.Queue()
        self.requests = []
        self.first_sent = asyncio.Event()
        self.send = AsyncMock(side_effect=self.on_send)
        self.close = AsyncMock()

    async def on_send(self, frame):
        self.requests.append(json.loads(frame))
        self.first_sent.set()
        for message in self.replies[len(self.requests) - 1]:
            await self.messages.put(message)

    async def __aiter__(self):
        yield json.dumps({'event': 'ms.channel.connect'})
        # Even a valid unsolicited app list before sending is not evidence.
        yield json.dumps(app_response(INSTALLED))
        while True:
            message = await self.messages.get()
            if message is None:
                return
            if isinstance(message, Exception):
                raise message
            yield message if isinstance(message, str) else json.dumps(message)


@pytest.fixture(autouse=True)
def short_strategies(setup):
    service = setup[3]
    service.catalogue = CapabilityCatalogue()
    service.catalogue.register('samsung.remote.control', (replace(
        INSTALLED_APPS, timeout=0.2,
        strategies=tuple(replace(strategy, timeout=0.1) for strategy in INSTALLED_APPS.strategies),
    ),))


def test_ordered_strategy_definitions():
    assert INSTALLED_APPS.probe_id == 'apps.list'
    assert [strategy.strategy_id for strategy in INSTALLED_APPS.strategies] == [
        'samsung.apps.installed_app', 'samsung.apps.eden_app',
    ]
    assert [strategy.expected_event for strategy in INSTALLED_APPS.strategies] == [INSTALLED, EDEN]
    for strategy in INSTALLED_APPS.strategies:
        assert strategy.payload == {
            'method': 'ms.channel.emit',
            'params': {'event': strategy.expected_event, 'to': 'host', 'data': ''},
        }
        assert strategy.timeout == 5
    assert sum(strategy.timeout for strategy in INSTALLED_APPS.strategies) == 10
    assert 'ms.application.get' not in repr(INSTALLED_APPS.strategies)


@pytest.mark.asyncio
@pytest.mark.parametrize('replies,status,matched,request_count', [
    ([[app_response(INSTALLED)], []], 'supported', INSTALLED, 1),
    ([[], [app_response(EDEN)]], 'supported', EDEN, 2),
    ([[], []], 'no_response', None, 2),
    ([[{'event': 'ed.edenTV.update'}, {'event': 'some.other.event'}, app_response(INSTALLED)], []], 'supported', INSTALLED, 1),
    ([[{'event': INSTALLED, 'data': 'malformed'}], []], 'no_response', None, 2),
    ([[{'event': INSTALLED, 'data': 'malformed'}, app_response(INSTALLED)], []], 'supported', INSTALLED, 1),
    ([[{'event': 'ms.error', 'data': {'message': 'request rejected'}}], [app_response(EDEN)]], 'supported', EDEN, 2),
    ([[{'event': INSTALLED, 'error': 'rejected'}], [app_response(EDEN)]], 'supported', EDEN, 2),
    ([[{'event': 'ms.error'}], []], 'error', None, 2),
    ([[{'event': 'ms.channel.unauthorized'}], []], 'rejected', None, 1),
    ([[None], []], 'error', None, 1),
    ([[RuntimeError('private receive error')], []], 'error', None, 1),
    ([['invalid json'], []], 'error', None, 1),
    ([[], [app_response(INSTALLED), app_response(EDEN)]], 'supported', EDEN, 2),
    ([[{'params': {'data': json.dumps(app_response(INSTALLED))}}], []], 'supported', INSTALLED, 1),
])
async def test_apps_strategy_sequence(setup, monkeypatch, replies, status, matched, request_count):
    kernel, devices, targets, service = setup
    device, target = await add_target(setup, 'samsung.remote.control')
    await devices.update_authentication(device.id, DeviceAuthentication(
        samsung=SamsungAuthentication(token='persisted-secret'),
    ))
    before = devices.path.read_bytes()
    connection = StrategyConnection(replies)
    connect = AsyncMock(return_value=connection)
    monkeypatch.setattr(websocket, 'connect', connect)
    path = f'/devices/{device.id}/targets/{target.target_id}'
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=ApiService(kernel).app), base_url='http://test') as client:
        response = await asyncio.wait_for(client.post(path + '/explore/apps.list'), timeout=1.5)
        assert response.status_code == 200
        assert response.json()['status'] == status
        assert response.json()['matched_event'] == matched
        assert response.json()['capability_id'] == 'apps.list'
        result = service.get_capability_result(target.target_id, 'apps.list')
        assert len(result.strategy_attempts) == request_count
        assert result.matched_strategy == (
            f'samsung.apps.{"installed_app" if matched == INSTALLED else "eden_app"}' if matched else None
        )
        for _ in range(2):
            stored = await client.get(path + '/capabilities/apps.list')
            manifest = await client.get(path + '/capabilities')
            assert stored.json() == response.json()
            assert manifest.json()['capabilities'][0]['status'] == status
        assert service.get_capability_result(target.target_id, 'apps.list') is result
    assert connection.requests == [
        {'method': 'ms.channel.emit', 'params': {'event': event, 'to': 'host', 'data': ''}}
        for event in [INSTALLED, EDEN][:request_count]
    ]
    connect.assert_awaited_once()
    assert parse_qs(urlsplit(connect.await_args.args[0]).query)['token'] == ['persisted-secret']
    connection.close.assert_awaited_once()
    assert targets.get(target.target_id).name == 'samsung.remote.control'
    assert devices.path.read_bytes() == before
    assert 'strategy' not in before.decode()
    if status == 'no_response':
        assert response.json()['error'] == 'No known application-list strategy produced a response'
    assert not any(task.get_name().startswith('websocket-receiver:') for task in asyncio.all_tasks())


@pytest.mark.asyncio
async def test_strategy_diagnostics_are_sanitized_and_bounded(setup, monkeypatch, caplog):
    _, devices, _, service = setup
    device, target = await add_target(setup, 'samsung.remote.control')
    secret = 'pairing-secret'
    await devices.update_authentication(device.id, DeviceAuthentication(samsung=SamsungAuthentication(token=secret)))
    connection = StrategyConnection([[
        {'event': 'ed.edenTV.update', 'data': {'token': secret, 'payload': 'private app data'}},
        {'event': secret, secret: 'private', 'token': secret},
        {'event': 'not\nan\nevent'},
        *[{'event': 'some.other.event'} for _ in range(101)],
        app_response(INSTALLED),
    ], []])
    monkeypatch.setattr(websocket, 'connect', AsyncMock(return_value=connection))
    caplog.set_level(logging.DEBUG)
    result = await service.explore_capability(device, target, 'apps.list')
    assert result.status.value == 'supported'
    attempt = result.strategy_attempts[0]
    assert len(attempt.diagnostics) == 100
    assert attempt.dropped_diagnostics == 5
    diagnostic = attempt.diagnostics[0]
    assert diagnostic.event == 'ed.edenTV.update'
    assert diagnostic.message_keys == ('event', 'data')
    assert diagnostic.matched_event is False
    assert diagnostic.timestamp.tzinfo is not None
    assert attempt.diagnostics[1].event == '<redacted>'
    assert attempt.diagnostics[2].event == '<invalid>'
    assert secret not in repr(result) + caplog.text
    assert 'private app data' not in repr(result) + caplog.text
    assert 'ed.edenTV.update' in caplog.text
    assert result.response is None
    connection.close.assert_awaited_once()


@pytest.mark.parametrize('message,event,valid', [
    ({'event': INSTALLED, 'data': []}, INSTALLED, True),
    ({'event': INSTALLED, 'data': {'data': []}}, INSTALLED, True),
    (app_response(INSTALLED), INSTALLED, True),
    ({'data': app_response(EDEN)}, EDEN, True),
    ({'params': app_response(EDEN)}, EDEN, True),
    ({'params': {'data': json.dumps(app_response(EDEN))}}, EDEN, True),
    ({'event': INSTALLED, 'data': json.dumps({'data': []})}, INSTALLED, True),
    ({'event': INSTALLED}, INSTALLED, False),
    ({'event': INSTALLED, 'data': {}}, INSTALLED, False),
    ({'event': INSTALLED, 'data': ['not an app']}, INSTALLED, False),
    ({'event': INSTALLED, 'data': [{'name': 'missing ID'}]}, INSTALLED, False),
    ({'event': INSTALLED, 'data': [{'appId': ''}]}, INSTALLED, False),
    ({'event': INSTALLED, 'data': [{'appId': True}]}, INSTALLED, False),
    ({'event': INSTALLED, 'data': {'error': 'bad', 'data': []}}, INSTALLED, False),
    ({'event': INSTALLED, 'error': 'bad', 'data': []}, INSTALLED, False),
    ({'data': 'invalid json'}, None, False),
])
def test_samsung_message_normalization_and_app_validation(message, event, valid):
    assert extract_event_name(message) == event
    assert has_application_list(message) is valid


@pytest.mark.asyncio
async def test_cancelling_strategy_closes_connection(setup, monkeypatch):
    _, _, _, service = setup
    device, target = await add_target(setup, 'samsung.remote.control')
    connection = StrategyConnection([[], []])
    monkeypatch.setattr(websocket, 'connect', AsyncMock(return_value=connection))
    task = asyncio.create_task(service.explore_capability(device, target, 'apps.list'))
    await asyncio.wait_for(connection.first_sent.wait(), timeout=1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    connection.close.assert_awaited_once()
    assert service.get_results(target.target_id) == ()
    assert not any(task.get_name().startswith('websocket-receiver:') for task in asyncio.all_tasks())


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', ['send_error', 'send_timeout', 'handshake_timeout'])
async def test_connection_failures_are_errors_not_no_response(setup, monkeypatch, failure):
    from test_target_probes import FakeConnection

    _, _, _, service = setup
    device, target = await add_target(setup, 'samsung.remote.control')
    connection = FakeConnection([]) if failure == 'handshake_timeout' else StrategyConnection([[], []])

    async def stall(frame):
        await asyncio.Event().wait()

    if failure == 'send_error':
        connection.send.side_effect = OSError('secret connection URL')
    elif failure == 'send_timeout':
        connection.send.side_effect = stall
    monkeypatch.setattr(websocket, 'connect', AsyncMock(return_value=connection))
    result = await asyncio.wait_for(service.explore_capability(device, target, 'apps.list'), timeout=1)
    assert result.status.value == 'error'
    assert len(result.strategy_attempts) == (0 if failure == 'handshake_timeout' else 1)
    assert 'secret' not in repr(result)
    connection.close.assert_awaited_once()


@pytest.mark.parametrize('timeout', [0, -1, float('inf'), float('nan')])
def test_invalid_strategy_timeout_cannot_be_registered(timeout):
    catalogue = CapabilityCatalogue()
    probe = replace(INSTALLED_APPS, strategies=(replace(INSTALLED_APPS.strategies[0], timeout=timeout),))
    with pytest.raises(ValueError, match='Strategy timeouts'):
        catalogue.register('samsung.remote.control', (probe,))


def test_duplicate_strategy_ids_cannot_be_registered():
    catalogue = CapabilityCatalogue()
    probe = replace(INSTALLED_APPS, strategies=(INSTALLED_APPS.strategies[0],) * 2)
    with pytest.raises(ValueError, match='Strategy IDs'):
        catalogue.register('samsung.remote.control', (probe,))
