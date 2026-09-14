from enum import StrEnum

class SessionState(StrEnum):
    CONNECTED = "connected"
    CONNECTING = "connecting"
    CREATED = "created"
    DISCONNECTED = "disconnected"
    DISCONNECTING = "disconnecting"
    FAILED = "failed"

class MessageDirection(StrEnum):
    INBOUND = "inbound"
    OUTBOUND = "outbound"

class PayloadType(StrEnum):
    BINARY = "binary"
    JSON = "json"
    TEXT = "text"

class TransportType(StrEnum):
    HTTP = "http"
    WEBSOCKET = "websocket"
