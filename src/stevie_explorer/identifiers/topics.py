from enum import StrEnum

class Topic(StrEnum):
    SYSTEM_TELEMETRY_CREATED = "system.telemetry.created"
    DEVICE_CREATED = "device.created"
    DEVICE_UPDATED = "device.updated"
    DEVICE_REMOVED = "device.removed"
