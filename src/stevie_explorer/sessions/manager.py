from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from stevie_explorer.identifiers import (
    MessageDirection,
    PayloadType,
    ServiceName,
    SessionState,
    TelemetryMessage,
    TransportType
)
from stevie_explorer.kernel import (
    BaseService,
    ExplorerKernel
)
from stevie_explorer.sessions.models import (
    CapturedMessage,
    ExplorerSession
)
from stevie_explorer.targets.registry import TargetRegistry
from stevie_explorer.telemetry import TelemetryService
from stevie_explorer.transports import (
    BaseTransport,
    TransportPayload,
    WebSocketTransport
)

class SessionManager(BaseService):
    name = ServiceName.SESSION_MANAGER

    def __init__(self, kernel:ExplorerKernel) -> None:
        self.kernel = kernel

        self._sessions: dict[str, ExplorerSession] = {}
        self._transports: dict[str, BaseService] = {}

    def get(self, session_id: str) -> ExplorerSession:
        try:
            return self._sessions[session_id]
        except KeyError as exc:
            raise KeyError(f"Unknown session: {session_id}") from exc

    def list(self) -> tuple[ExplorerSession, ...]:
        return tuple(self._sessions.values())

    def messages(self, session_id: str) -> tuple[CapturedMessage, ...]:
        return tuple(self.get(session_id).messages)

    async def connect(self, session_id: str) -> ExplorerSession:
        session = self.get(session_id)

        if session.state not in {
            SessionState.CREATED,
            SessionState.DISCONNECTED,
            SessionState.FAILED
        }:
            raise RuntimeError(
                f"Cannot connect session in state {session.state.value}"
            )
        
        targets: TargetRegistry = self.kernel.get(
            ServiceName.TARGET_REGISTRY
        )

        target = targets.get(session.target_id)
        telemetry = self._telemetry()

        session.state = SessionState.CONNECTING
        session.error = None

        await telemetry.emit(
            TelemetryMessage.SESSION_CONNECTING,
            source=self.name,
            session_id=session.session_id,
            target_id=target.target_id,
            uri=target.uri
        )

        transport = self._create_transport(target.transport)

        self._transports[session.session_id] = transport

        async def on_message(transport_payload: TransportPayload) -> None:
            message = CapturedMessage(
                session_id=session.session_id,
                direction=MessageDirection.INBOUND,
                payload_type=transport_payload.payload_type,
                payload=transport_payload.value
            )

            session.messages.append(message)

            await telemetry.emit(
                TelemetryMessage.TRANSPORT_MESSAGE_RECEIVED,
                source=self.name,
                session_id=session.session_id,
                message_id=message.message_id,
                payload_type=message.payload_type.value
            )

        async def on_error(error: Exception) -> None:
            session.sate = SessionState.FAILED
            session.error = str(error)

            await telemetry.emit(
                TelemetryMessage.TRANSPORT_RECEIVE_FAILED,
                source=self.name,
                session_id=session.session_id,
                error=str(error)
            )

        try:
            await transport.connect(
                target=target,
                on_message=on_message,
                on_error=on_error
            )
        except Exception as exc:
            session.state = SessionState.FAILED
            session.error = str(exc)

            self._transports.pop(session.session_id, None)

            await telemetry.emit(
                TelemetryMessage.SESSION_CONNECTION_FAILED,
                source=self.name,
                session_id=session.session_id,
                target_id=target.target_id,
                error=str(exc)
            )

            raise

        session.state = SessionState.CONNECTED
        session.connected_at = datetime.now(UTC)

        await telemetry.emit(
            TelemetryMessage.SESSION_CONNECTED,
            source=self.name,
            session_id=session.session_id,
            target_id=target.target_id
        )

        return session

    async def create(self, target_id: str) -> ExplorerSession:
        targets: TargetRegistry = self.kernel.get(ServiceName.TARGET_REGISTRY)

        target = targets.get(target_id)

        session = ExplorerSession(target_id=target.target_id)

        self._sessions[session.session_id] = session

        telemetry = self._telemetry()

        await telemetry.emit(
            TelemetryMessage.SESSION_CREATED,
            source=self.name,
            session_id=session.session_id,
            target_id=target.target_id
        )

        return session

    async def disconnect(self, session_id: str) -> ExplorerSession:
        session = self.get(session_id)

        if session.state in {
            SessionState.DISCONNECTED,
            SessionState.CREATED
        }:
            return session
        
        session.state = SessionState.DISCONNECTED

        transport = self._transports.pop(
            session_id,
            None
        )

        if transport is not None:
            await transport.disconnect()
        
        session.state = SessionState.DISCONNECTED
        session.disconnected_at = datetime.now(UTC)

        await self._telemetry().emit(
            TelemetryMessage.SESSION_DISCONNECTED,
            source=self.name,
            session_id=session.session_id,
            target_id=session.target_id
        )

        return session

    async def send(self, session_id: str, payload_type: PayloadType, payload: Any) -> CapturedMessage:
        session = self.get(session_id)

        if session.state != SessionState.CONNECTED:
            raise RuntimeError("Session is not connected")
        
        try:
            transport = self._transports[session_id]
        except KeyError as exc:
            raise RuntimeError("Session transport is unavailable") from exc
        
        transport_payload = TransportPayload(
            payload_type=payload_type,
            value=payload
        )
        await transport.send(transport_payload)

        message = CapturedMessage(
            session_id = session.session_id,
            direction = MessageDirection.OUTBOUND,
            payload_type = payload_type,
            payload = payload
        )

        session.messages.append(message)

        await self._telemetry().emit(
            TelemetryMessage.TRANSPORT_MESSAGE_SENT,
            source=self.name,
            session_id=session.session_id,
            message_id=message.message_id,
            payload_type=message.payload_type.value
        )

        return message

    async def start(self) -> None:
        pass

    async def stop(self) -> None:
        for session_id in tuple(self._sessions):
            session = self._sessions[session_id]

            if session.state in {
                SessionState.CONNECTING,
                SessionState.CONNECTED
            }:
                await self.disconnect(session_id)

    def _create_transport(self, transport_type: TransportType) -> BaseTransport:
        if transport_type == TransportType.WEBSOCKET:
            return WebSocketTransport()
        
        raise ValueError(f"Unsupported transport: {transport_type}")

    def _telemetry(self) -> TelemetryService:
        return self.kernel.get(ServiceName.TELEMETRY)