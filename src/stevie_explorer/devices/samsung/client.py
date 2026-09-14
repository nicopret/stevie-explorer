from __future__ import annotations

import asyncio
import logging
from base64 import b64encode
from collections.abc import Awaitable, Callable
from dataclasses import replace
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

from stevie_explorer.devices.samsung.messages import (
    diagnostic_identifier, extract_event_name, message_nodes,
)
from stevie_explorer.devices.samsung.keys import remote_key_payload

if TYPE_CHECKING:
    from stevie_explorer.capabilities.models import ProbeStrategy, StrategyAttempt, StrategyDiagnostic
from urllib.parse import urlencode

from stevie_explorer.devices.models import Device, DeviceAuthentication, SamsungAuthentication
from stevie_explorer.identifiers import PayloadType, TransportType
from stevie_explorer.transports.payload import TransportPayload
from stevie_explorer.targets import Target
from stevie_explorer.transports import WebSocketTransport

import httpx

async def get_api_v2(ip_address: str, *, timeout: float = 5.0) -> dict:
    host = f"[{ip_address}]" if ":" in str(ip_address) else str(ip_address)
    url = f"http://{host}:8001/api/v2/"

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.get(url)
            response.raise_for_status()
        
        return {
            "available": True,
            "error": None,
            "data": response.json()
        }

    except Exception as exc:
        return {
            "available": False,
            "error": str(exc),
            "data": None
        }


# Keep the pairing identity identical across probes and restarts.
SAMSUNG_CLIENT_NAME = "Stevie Explorer"
# Remote keys have no reliable per-key acknowledgement. Observe immediate errors.
REMOTE_KEY_ERROR_WINDOW = 0.2
REMOTE_KEY_DELAY = 0.4


def extract_token(message: dict) -> str | None:
    if message.get("event") != "ms.channel.connect":
        return None
    # Samsung returns the pairing token in the connection event data.
    data = message.get("data")
    token = data.get("token") if isinstance(data, dict) else None
    return token if isinstance(token, str) and token.strip() else None


class _TokenRejected(PermissionError):
    pass


class SamsungCommandRejected(RuntimeError):
    pass


class RemoteControlConnection:
    """One authenticated transport and its current request; the receiver stays shared."""

    MAX_DIAGNOSTICS = 100

    def __init__(self, transport: WebSocketTransport, token: str | None) -> None:
        self.transport = transport
        self.connected = asyncio.get_running_loop().create_future()
        self.pending: asyncio.Future[str] | None = None
        self.strategy: ProbeStrategy | None = None
        self.diagnostics: list[StrategyDiagnostic] = []
        self.dropped_diagnostics = 0
        self.secrets = {token} if token else set()
        self.failure: Exception | None = None

    async def on_message(self, message: TransportPayload) -> None:
        from stevie_explorer.capabilities.models import StrategyDiagnostic

        value = message.value
        event = extract_event_name(value) if isinstance(value, dict) else None
        if isinstance(value, dict):
            for node in message_nodes(value):
                token = node.get("token")
                if isinstance(token, str) and token:
                    self.secrets.add(token)
        if self.strategy is not None and self.pending is not None and not self.pending.done():
            diagnostic = StrategyDiagnostic(
                event=diagnostic_identifier(event, self.secrets),
                message_keys=tuple(
                    diagnostic_identifier(key, self.secrets) or "<invalid>" for key in list(value)[:32]
                ) if isinstance(value, dict) else (),
                matched_event=event == self.strategy.expected_event,
            )
            if len(self.diagnostics) < self.MAX_DIAGNOSTICS:
                self.diagnostics.append(diagnostic)
                logging.getLogger(__name__).debug(
                    "Samsung strategy %s received event=%s keys=%s matched=%s",
                    self.strategy.strategy_id, diagnostic.event,
                    diagnostic.message_keys, diagnostic.matched_event,
                )
            else:
                self.dropped_diagnostics += 1
        if not self.connected.done():
            if event == "ms.channel.connect":
                self.connected.set_result(next((
                    extract_token(node) for node in message_nodes(value)
                    if extract_token(node) is not None
                ), None))
            elif event == "ms.channel.unauthorized":
                self.connected.set_exception(_TokenRejected("Samsung remote control authorization was rejected"))
            return
        if event == "ms.channel.unauthorized":
            await self.on_error(PermissionError("Samsung remote control authorization was rejected"))
            return
        if self.pending is None or self.pending.done():
            return
        if not isinstance(value, dict):
            await self.on_error(RuntimeError("Invalid Samsung protocol message"))
        elif event == "ms.error" or (
            event == (self.strategy.expected_event if self.strategy else "ms.remote.control")
            and any(node.get("error") is not None for node in message_nodes(value))
        ):
            self.pending.set_exception(SamsungCommandRejected("Samsung rejected the request"))
        elif self.strategy is not None and event == self.strategy.expected_event:
            try:
                matched = self.strategy.response_matcher(value)
            except Exception:
                await self.on_error(RuntimeError("Samsung response matcher failed"))
            else:
                if matched:
                    self.pending.set_result(event)

    async def on_error(self, error) -> None:
        self.failure = (error if isinstance(error, PermissionError)
                        else RuntimeError("Samsung WebSocket receive failed"))
        pending = self.pending if self.connected.done() else self.connected
        if pending is not None and not pending.done():
            pending.set_exception(self.failure)

    async def execute(self, strategy: ProbeStrategy) -> StrategyAttempt:
        from stevie_explorer.capabilities.models import CapabilityStatus, StrategyAttempt

        self.strategy = strategy
        self.diagnostics = []
        self.dropped_diagnostics = 0
        self.pending = asyncio.get_running_loop().create_future()
        status = CapabilityStatus.NO_RESPONSE
        event = None
        error = None
        connection_failed = False
        sent = False
        try:
            if self.failure is not None:
                raise self.failure
            async with asyncio.timeout(strategy.timeout):
                await self.transport.send(TransportPayload(PayloadType.JSON, strategy.payload))
                sent = True
                event = await self.pending
            status = CapabilityStatus.SUPPORTED
        except TimeoutError:
            if sent:
                error = "Strategy produced no valid response before its timeout"
            else:
                status = CapabilityStatus.ERROR
                error = "Samsung strategy send timed out"
                connection_failed = True
        except SamsungCommandRejected:
            status = CapabilityStatus.ERROR
            error = "Samsung rejected the strategy request"
        except PermissionError:
            status = CapabilityStatus.REJECTED
            error = "Samsung remote control authorization was rejected"
            connection_failed = True
        except Exception:
            status = CapabilityStatus.ERROR
            error = "Samsung strategy connection or protocol failed"
            connection_failed = True
        finally:
            self.pending.cancel()
            # Retrieve any error that raced with a send failure/cancellation.
            if not self.pending.cancelled():
                self.pending.exception()
            self.pending = None
            self.strategy = None
        return StrategyAttempt(
            strategy_id=strategy.strategy_id, status=status, matched_event=event, error=error,
            diagnostics=tuple(self.diagnostics), dropped_diagnostics=self.dropped_diagnostics,
            connection_failed=connection_failed,
        )

    async def send_key(self, key: str) -> None:
        """Send once and observe errors; never retry a possibly executed key."""
        payload = remote_key_payload(key)
        if not self.connected.done() or self.connected.cancelled():
            raise ConnectionError("Samsung remote control is not authenticated")
        self.connected.result()
        if self.failure is not None:
            raise self.failure
        self.pending = asyncio.get_running_loop().create_future()
        try:
            await self.transport.send(TransportPayload(PayloadType.JSON, payload))
            try:
                async with asyncio.timeout(REMOTE_KEY_ERROR_WINDOW):
                    await self.pending
            except TimeoutError:
                # Silence is normal for keys, and does not confirm physical effect.
                pass
        finally:
            self.pending.cancel()
            if not self.pending.cancelled():
                self.pending.exception()
            self.pending = None


@asynccontextmanager
async def remote_control_connection(
    device: Device,
    target: Target,
    timeout: float,
    save_authentication: Callable[[DeviceAuthentication], Awaitable[None]],
):
    authentication = device.authentication or DeviceAuthentication()
    samsung = authentication.samsung
    token = samsung.token.get_secret_value() if samsung and samsung.token else None
    scheme = target.scheme or "wss"
    if scheme not in {"ws", "wss"}:
        raise ValueError("Invalid Samsung remote control WebSocket scheme")
    port = target.port or (8002 if scheme == "wss" else 8001)
    host = f"[{device.ip_address}]" if device.ip_address.version == 6 else str(device.ip_address)

    for attempt in range(2):
        transport = WebSocketTransport()
        connection = RemoteControlConnection(transport, token)
        query = {**target.query, "name": b64encode(SAMSUNG_CLIENT_NAME.encode()).decode("ascii")}
        query.pop("token", None)
        if token:
            query["token"] = token
        endpoint = replace(
            target, transport=TransportType.WEBSOCKET,
            uri=f"{scheme}://{host}:{port}/api/v2/channels/samsung.remote.control?{urlencode(query)}",
        )
        try:
            try:
                # Suppress raw websockets frame/URL logging, including credentials.
                transport.logger = logging.Logger("stevie.samsung.websocket", level=logging.CRITICAL + 1)
                async with asyncio.timeout(timeout):
                    await transport.connect(endpoint, connection.on_message, connection.on_error)
                    new_token = await connection.connected
                if new_token and new_token != token:
                    await save_authentication(authentication.model_copy(update={
                        "samsung": SamsungAuthentication(token=new_token),
                    }))
                yield connection
                return
            finally:
                connection.connected.cancel()
                if not connection.connected.cancelled():
                    connection.connected.exception()
                async with asyncio.timeout(timeout):
                    await transport.disconnect()
        except _TokenRejected:
            if not token or attempt:
                raise PermissionError("Samsung remote control authorization was rejected") from None
            token = None
        except PermissionError:
            raise PermissionError("Samsung remote control authorization was rejected") from None
        except TimeoutError:
            raise TimeoutError("Samsung response timed out") from None
        except (ConnectionError, OSError):
            raise ConnectionError("Samsung WebSocket connection failed or timed out") from None
        except Exception:
            raise RuntimeError("Samsung WebSocket connection failed") from None
    raise AssertionError("Unreachable")


async def connect_remote_control(
    device: Device, target: Target, timeout: float,
    save_authentication: Callable[[DeviceAuthentication], Awaitable[None]],
) -> str:
    async with remote_control_connection(device, target, timeout, save_authentication):
        return "ms.channel.connect"
