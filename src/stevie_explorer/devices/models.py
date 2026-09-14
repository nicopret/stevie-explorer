from __future__ import annotations

from dataclasses import replace
from ipaddress import IPv4Address, IPv6Address
from typing import Any
from uuid import UUID, NAMESPACE_URL, uuid5

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator

from stevie_explorer.targets.models import Target


class SamsungAuthentication(BaseModel):
    model_config = ConfigDict(frozen=True)
    token: SecretStr | None = None


class DeviceAuthentication(BaseModel):
    model_config = ConfigDict(frozen=True)
    samsung: SamsungAuthentication | None = None


class Device(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: UUID
    device_name: str = Field(min_length=1, max_length=128, pattern=r"^[a-zA-Z0-9][a-zA-Z0-9._-]*$")
    display_name: str = Field(min_length=1, max_length=256)
    ip_address: IPv4Address | IPv6Address

    authentication: DeviceAuthentication | None = Field(default=None, exclude=True, repr=False)
    targets: tuple[Target, ...] = ()

    @model_validator(mode="after")
    def assign_target_devices(self) -> "Device":
        for target in self.targets:
            if target.device_id is not None and target.device_id != self.id:
                raise ValueError("Target belongs to another device")
        object.__setattr__(self, "targets", tuple(
            replace(target, device_id=self.id) for target in self.targets
        ))
        return self

    @model_validator(mode="before")
    @classmethod
    def add_legacy_id(cls, value: Any) -> Any:
        if isinstance(value, dict) and not value.get("id") and value.get("device_name"):
            value = dict(value)
            value["id"] = uuid5(NAMESPACE_URL, f"stevie-device:{value['device_name']}")
        return value

    @field_validator("targets", mode="before")
    @classmethod
    def require_persisted_target_ids(cls, value: Any) -> Any:
        if isinstance(value, (list, tuple)):
            for target in value:
                if isinstance(target, dict) and not target.get("target_id"):
                    raise ValueError("Persisted target requires target_id")
        return value

class DeviceApiData(BaseModel):
    samsung: dict[str, Any] | None = None

class DeviceDetail(Device):
    targets: tuple[Target, ...] = Field(default=(), exclude=True)
    api: DeviceApiData | None = None

class DevicePatch(BaseModel):
    display_name: str | None = Field(default=None, min_length=1, max_length=256)
    ip_address: IPv4Address | IPv6Address | None = None
