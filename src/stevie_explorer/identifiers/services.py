from enum import StrEnum

class ServiceName(StrEnum):
        API = "api"
        CONFIGURATION = "configuration"
        EVENTBUS = "eventbus"
        TARGET_REGISTRY = "target_registry"
        TELEMETRY = "telemetry"
        SESSION_MANAGER = "session_manager"
