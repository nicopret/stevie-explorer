from __future__ import annotations

import base64
import binascii

from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, model_validator

from stevie_explorer.identifiers import (
    MessageDirection,
    PayloadType,
    ServiceName,
    SessionState
)
from stevie_explorer.kernel import ExplorerKernel
from stevie_explorer.sessions import (
    CapturedMessage,
    ExplorerSession,
    SessionManager
)

class CreateSessionRequest(BaseModel):
    target_id: str

class SendMessageRequest(BaseModel):
    payload_type: Literal["json", "text", "binary"]
    payload: Any

    encoding: Literal["base64", "hex"] | None = None

    @model_validator(mode="after")
    def validate_payload(self) -> "SendMessageRequest":
        if self.payload_type == "json":
            if isinstance(self.payload, bytes):
                raise ValueError("JSON payload cannot be bytes")

            if self.encoding is not None:
                raise ValueError("Encoding is only valid for binary payloads")

        elif self.payload_type == "text":
            if not isinstance(self.payload, str):
                raise ValueError("Text payload must be a string")
            
            if self.encoding is not None:
                raise ValueError("Encoding is only valid for binary payloads")

        elif self.payload_type == "binary":
            if not isinstance(self.payload, str):
                raise ValueError("Binary payload must be an encoded string")
            
            if self.encoding is None:
                raise ValueError("Binary payload requires base64 or hex encoding")
        
        return self

class SessionResponse(BaseModel):
    session_id: str
    target_id: str
    state: SessionState
    error: str | None
    message_count: int
    created_at: datetime
    connected_at: datetime | None
    disconnected_at: datetime | None

class MessageResponse(BaseModel):
    message_id: str
    session_id: str
    direction: MessageDirection
    payload_type: PayloadType
    payload: Any
    timestamp: datetime

def create_router(kernel: ExplorerKernel) -> APIRouter:
    router = APIRouter(prefix="/sessions", tags=["sessions"])

    @router.post("", response_model=SessionResponse, status_code=201)
    async def create_session(request: CreateSessionRequest) -> SessionResponse:
        manager: SessionManager = kernel.get(ServiceName.SESSION_MANAGER)

        try:
            session = await manager.create(request.target_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        
        return _session_response(session)
    
    @router.get("", response_model=list[SessionResponse])
    async def list_sessions() -> list[SessionResponse]:
        manager: SessionManager = kernel.get(ServiceName.SESSION_MANAGER)

        return [
            _session_response(session) for session in manager.list()
        ]
    
    @router.get("/{session_id}", response_model=SessionResponse)
    async def get_session(session_id: str) -> SessionResponse:
        manager: SessionManager = kernel.get(ServiceName.SESSION_MANAGER)

        try:
            return _session_response(manager.get(session_id))
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
    
    @router.post("/{session_id}/connect", response_model=SessionResponse)
    async def connect_session(session_id: str) -> SessionResponse:
        manager: SessionManager = kernel.get(ServiceName.SESSION_MANAGER)

        try:
            session = await manager.connect(session_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        
        return _session_response(session)
    
    @router.post("/{session_id}/messages", response_model=SessionResponse, status_code=202)
    async def send_message(session_id: str, request: SendMessageRequest) -> MessageResponse:
        manager: SessionManager = kernel.get(ServiceName.SESSION_MANAGER)

        try:
            message = await manager.send(session_id, request.payload)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        
        return _message_response(message)
    
    @router.get("/{session_id}/messages", response_model=MessageResponse, status_code=202)
    async def get_messages(session_id: str, request: SendMessageRequest) -> MessageResponse:
        manager: SessionManager = kernel.get(ServiceName.SESSION_MANAGER)

        payload_type, payload = _decode_request_payload(request)

        try:
            message = await manager.send(session_id=session_id, payload_type=payload_type, payload=payload)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        
        return _message_response(message)
    
    @router.post("/{session_id}/disconnect", response_model=SessionResponse)
    async def disconnect_session(session_id: str) -> SessionResponse:
        manager: SessionManager = kernel.get(ServiceName.SESSION_MANAGER)

        try:
            session = await manager.disconnect(session_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

        return _session_response(session)
    
    return router

def _decode_request_payload(request: SendMessageRequest) -> tuple[PayloadType, Any]:
    payload_type = PayloadType(request.payload_type)

    if payload_type != PayloadType.BINARY:
        return payload_type, request.payload
    
    try:
        if request.encoding == "base64":
            return (
                payload_type,
                base64.b64decode(
                    request.payload,
                    validate=True
                )
            )
        
        if request.encoding == "hex":
            return (
                payload_type,
                bytes.fromhex(request.payload)
            )

    except(ValueError, binascii.Error) as exc:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid {request.encoding} binary payload"
        ) from exc
    
    raise HTTPException(
        status_code=422,
        detail="Unsupported binary encoding"
    )

def _message_response(message: CapturedMessage) -> MessageResponse:
    payload: Any = message.payload

    if isinstance(payload, bytes):
        payload = {
            "encoding": "hex",
            "value": payload.hex()
        }
    
    return MessageResponse(
        message_id=message.message_id,
        session_id=message.session_id,
        direction=message.direction,
        payload_type=message.payload_type,
        payload=payload,
        timestamp=message.timestamp
    )

def _session_response(session: ExplorerSession) -> SessionResponse:
    return SessionResponse(
        session_id=session.session_id,
        target_id=session.target_id,
        state=session.state,
        error=session.error,
        message_count=len(session.messages),
        created_at=session.created_at,
        connected_at=session.connected_at,
        disconnected_at=session.disconnected_at
    )
