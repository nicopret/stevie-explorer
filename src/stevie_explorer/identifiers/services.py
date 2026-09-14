from enum import StrEnum

class ServiceName(StrEnum):
        API = "api"
        CAPABILITY_PROBE = "capability_probe"
        CAPTURE = "Capture"
        CONFIGURATION = "configuration"
        DEVICE_REGISTRY = "device_registry"
        EVENTBUS = "eventbus"
        TARGET_REGISTRY = "target_registry"
        TELEMETRY = "telemetry"
        SESSION_MANAGER = "session_manager"
