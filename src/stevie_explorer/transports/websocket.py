from __future__ import annotations

import asyncio
import json
import ssl

from typing import Any

from websockets.asyncio.client import (
    ClientConnection,
    connect
)
from websockets.exceptions import ConnectionClosed

from stevie_explorer.identifiers import PayloadType, TransportType
from stevie_explorer.targets.models import Target
from stevie_explorer.transports.base import (
    BaseTransport,
    ErrorHandler,
    MessageHandler
)
from stevie_explorer.transports.payload import TransportPayload

class WebSocketTransport(BaseTransport):
    transport_type = TransportType.WEBSOCKET

    def __init__(self) -> None:
        self._connection: ClientConnection | None = None
        self._receiver_task: asyncio.Task[None] | None = None
        self._on_message: MessageHandler | None = None
        self._on_error: ErrorHandler | None = None

    @property
    def connected(self) -> bool:
        return self._connection is not None

    async def connect(self, target: Target, on_message: MessageHandler, on_error: ErrorHandler) -> None:
        if self._connection is not None:
            raise RuntimeError(
                "Websocket transport is already connected"
            )

            ssl_context = None

            if target.uri.startswith("wss://"):
                ssl_context = ssl.create_default_context()

                ssl_context.check_hostname = False
                ssl_context.verify_mode = ssl.CERT_NONE

            self._on_message = on_message
            self._on_error = on_error

            self._connection = await connect(
                target.uri,
                ssl=ssl_context,
                additional_headers=target.headers or None,
                ping_interval=20,
                ping_timeout=20
            )

            self._receiver_task = asyncio.create_task(
                self._receive_loop(),
                name=f"websocket-receiver: {target.target_id}"
            )
    
    async def disconnect(self) -> None:
        connection = self._connection
        receiver_task = self._receiver_task

        self._connection = None
        self._receiver_task = None

        if connection is not None:
            await connection.close()
        
        if receiver_task is not None:
            receiver_task.cancel()

            try:
                await receiver_task
            except asyncio.CancelledError:
                pass
    
    async def send(self, payload: TransportPayload) -> None:
        if self._connection is None:
            raise RuntimeError(
                "WebSocket transport is not connected"
            )
        
        frame = self._serialize(payload)

        await self._connection.send(frame)


    @staticmethod
    def _deserialize(payload: str | bytes) -> Any:
        if isinstance(payload, bytes):
            return TransportPayload(
                payload_type=PayloadType.BINARY,
                value=payload
            )

        try:
            decoded = json.loads(payload)
        except json.JSONDecodeError:
            return TransportPayload(
                payload_type=PayloadType.TEXT,
                value=payload
            )

        return TransportPayload(
            payload_type=PayloadType.JSON,
            value=decoded
        )

    @staticmethod
    def _serialize(payload: TransportPayload) -> str | bytes:
        if payload.payload_type == PayloadType.JSON:
            return json.dumps(
                payload.value,
                seperators=(",", ":"),
                ensure_ascii=False
            )
        
        if payload.payload_type == PayloadType.TEXT:
            if not isinstance(payload.value, str):
                raise TypeError("Text payload value must be a string")
            return payload.value
        
        if payload.payload_type == PayloadType.BINARY:
            if not isinstance(payload.value, bytes):
                raise TypeError("Binary payload value must be bytes")
    
            return payload.value

        raise ValueError(f"Unsupported payload type: {payload.payload_type}")

    async def _receive_loop(self) -> None:
        if self._connection is None:
            return
        
        try:
            async for raw_message in self._connection:
                if self._on_message is not None:
                    await self._on_message(
                        self._deserialize(raw_message)
                    )
        except asyncio.CancelledError:
            raise
        
        except ConnectionClosed:
            return
        
        except Exception as exc:
            if self._on_error is not None:
                await self._on_error(exc)
