from dataclasses import dataclass
from enum import Enum, IntEnum, StrEnum

class TelemetryLevel(IntEnum):
    TRACE = 10
    DEBUG = 20
    INFO = 30
    WARNING = 40
    ERROR = 50
    CRITICAL = 60

class TelemetryCategory(StrEnum):
    API = "api"
    CAPTURE = "capture"
    DISCOVERY = "discovery"
    EXPERIMENT = "experiment"
    EXPORT = "export"
    SECURITY = "security"
    SESSION = "session"
    SYSTEM = "system"
    TRANSPORT = "transport"

@dataclass(frozen=True, slots=True)
class TelemetryMessageDefinition:
    key: str
    level: TelemetryLevel
    category: TelemetryCategory
    grafana_key: str
    description: str

class TelemetryMessage(Enum):
    API_STARTING = TelemetryMessageDefinition(
        key="api.starting",
        level=TelemetryLevel.INFO,
        category=TelemetryCategory.API,
        grafana_key="stevie_explorer.api.starting",
        description="API server is starting."
    )

    API_STARTED = TelemetryMessageDefinition(
        key="api.started",
        level=TelemetryLevel.INFO,
        category=TelemetryCategory.API,
        grafana_key="stevie_explorer.api.started",
        description="API server started."
    )

    API_STOPPING = TelemetryMessageDefinition(
        key="api.stopping",
        level=TelemetryLevel.INFO,
        category=TelemetryCategory.API,
        grafana_key="stevie_explorer.api.stopping",
        description="API server is stopping."
    )

    KERNEL_STARTING = TelemetryMessageDefinition(
        key="kernel.starting",
        level=TelemetryLevel.INFO,
        category=TelemetryCategory.SYSTEM,
        grafana_key="stevie_explorer.system.kernel.starting",
        description="Stevie Explorer kernel is starting."
    )

    KERNEL_STARTED = TelemetryMessageDefinition(
        key="kernel.started",
        level=TelemetryLevel.INFO,
        category=TelemetryCategory.SYSTEM,
        grafana_key="stevie_explorer.system.kernel.started",
        description="Stevie Explorer kernel started."
    )

    KERNEL_STOPPING = TelemetryMessageDefinition(
        key="kernel.stopping",
        level=TelemetryLevel.INFO,
        category=TelemetryCategory.SYSTEM,
        grafana_key="stevie_explorer.system.kernel.stopping",
        description="Stevie Explorer kernel is stopping."
    )

    KERNEL_STOPPED = TelemetryMessageDefinition(
        key="kernel.stopped",
        level=TelemetryLevel.INFO,
        category=TelemetryCategory.SYSTEM,
        grafana_key="stevie_explorer.system.kernel.stopped",
        description="Stevie Explorer kernel stopped."
    )

    KERNEL_SERVICE_STARTING = TelemetryMessageDefinition(
        key="kernel.service_starting",
        level=TelemetryLevel.DEBUG,
        category=TelemetryCategory.SYSTEM,
        grafana_key="stevie_explorer.system.kernel.service_starting",
        description="Kernel is starting a service."
    )

    KERNEL_SERVICE_STARTED = TelemetryMessageDefinition(
        key="kernel.service_started",
        level=TelemetryLevel.DEBUG,
        category=TelemetryCategory.SYSTEM,
        grafana_key="stevie_explorer.system.kernel.service_started",
        description="Kernel started a service."
    )

    KERNEL_SERVICE_STOPPING = TelemetryMessageDefinition(
        key="kernel.service_stopping",
        level=TelemetryLevel.DEBUG,
        category=TelemetryCategory.SYSTEM,
        grafana_key="stevie_explorer.system.kernel.service_stopping",
        description="Kernel is stopping a service."
    )

    KERNEL_SERVICE_STOPPED = TelemetryMessageDefinition(
        key="kernel.service_stopped",
        level=TelemetryLevel.DEBUG,
        category=TelemetryCategory.SYSTEM,
        grafana_key="stevie_explorer.system.kernel.service_stopped",
        description="Kernel stopped a service."
    )

    KERNEL_START_FAILED = TelemetryMessageDefinition(
        key="kernel.start_failed",
        level=TelemetryLevel.CRITICAL,
        category=TelemetryCategory.SYSTEM,
        grafana_key="stevie_explorer.system.kernel.start_failed",
        description="Kernel failed to start."
    )

    KERNEL_SERVICE_STOP_FAILED = TelemetryMessageDefinition(
        key="kernel.service_stop_failed",
        level=TelemetryLevel.ERROR,
        category=TelemetryCategory.SYSTEM,
        grafana_key="stevie_explorer.system.kernel.service_stop_failed",
        description="A service failed to stop cleanly."
    )

    TARGET_CREATED = TelemetryMessageDefinition(
        key="target.created",
        level=TelemetryLevel.INFO,
        category=TelemetryCategory.SESSION,
        grafana_key="stevie_explorer.target.created",
        description="An exploration target was created.",
    )

    SESSION_CREATED = TelemetryMessageDefinition(
        key="session.created",
        level=TelemetryLevel.INFO,
        category=TelemetryCategory.SESSION,
        grafana_key="stevie_explorer.session.created",
        description="An exploration session was created.",
    )

    SESSION_CONNECTING = TelemetryMessageDefinition(
        key="session.connecting",
        level=TelemetryLevel.INFO,
        category=TelemetryCategory.SESSION,
        grafana_key="stevie_explorer.session.connecting",
        description="An exploration session is connecting.",
    )

    SESSION_CONNECTED = TelemetryMessageDefinition(
        key="session.connected",
        level=TelemetryLevel.INFO,
        category=TelemetryCategory.SESSION,
        grafana_key="stevie_explorer.session.connected",
        description="An exploration session connected.",
    )

    SESSION_DISCONNECTED = TelemetryMessageDefinition(
        key="session.disconnected",
        level=TelemetryLevel.INFO,
        category=TelemetryCategory.SESSION,
        grafana_key="stevie_explorer.session.disconnected",
        description="An exploration session disconnected.",
    )

    SESSION_CONNECTION_FAILED = TelemetryMessageDefinition(
        key="session.connection_failed",
        level=TelemetryLevel.ERROR,
        category=TelemetryCategory.SESSION,
        grafana_key="stevie_explorer.session.connection_failed",
        description="An exploration session failed to connect.",
    )

    TRANSPORT_MESSAGE_SENT = TelemetryMessageDefinition(
        key="transport.message_sent",
        level=TelemetryLevel.DEBUG,
        category=TelemetryCategory.TRANSPORT,
        grafana_key="stevie_explorer.transport.message_sent",
        description="A protocol message was sent.",
    )

    TRANSPORT_MESSAGE_RECEIVED = TelemetryMessageDefinition(
        key="transport.message_received",
        level=TelemetryLevel.DEBUG,
        category=TelemetryCategory.TRANSPORT,
        grafana_key="stevie_explorer.transport.message_received",
        description="A protocol message was received.",
    )

    TRANSPORT_RECEIVE_FAILED = TelemetryMessageDefinition(
        key="transport.receive_failed",
        level=TelemetryLevel.ERROR,
        category=TelemetryCategory.TRANSPORT,
        grafana_key="stevie_explorer.transport.receive_failed",
        description="The transport receiver failed.",
    )
