from __future__ import annotations

from base64 import b64encode

import httpx
import pytest

from stevie_explorer.api import ApiService
from stevie_explorer.capabilities import (
    CapabilityProbe,
    CapabilityProbeService,
    CapabilityResult,
    CapabilityStatus,
    ProbeCapture,
    ProbeMode,
    ProbeSafety,
)
from stevie_explorer.capabilities.probes import PROBE_REGISTRY, SAMSUNG_BASIC
from stevie_explorer.capture import CaptureService
from stevie_explorer.identifiers import (
    MessageDirection,
    PayloadType,
    ServiceName,
    TransportType,
)
from stevie_explorer.kernel import ExplorerKernel
from stevie_explorer.routes.capabilities import _probe_response
from stevie_explorer.sessions.models import CapturedMessage, ExplorerSession
from stevie_explorer.targets import TargetRegistry
from stevie_explorer.targets.models import Target


class FakeKernel:
    def __init__(self, services: dict[ServiceName, object]) -> None:
        self.services = services

    def get(self, name: ServiceName) -> object:
        return self.services[name]


class FakeSessionManager:
    def __init__(self, inbound_payloads: list[object]) -> None:
        self.inbound_payloads = inbound_payloads
        self.session = ExplorerSession(target_id="target")

    async def create(self, target_id: str) -> ExplorerSession:
        self.session = ExplorerSession(target_id=target_id)
        return self.session

    async def connect(self, session_id: str) -> ExplorerSession:
        return self.session

    async def send(self, session_id: str, payload_type: PayloadType, payload: object) -> None:
        self.session.messages.append(CapturedMessage(
            session_id=session_id, direction=MessageDirection.OUTBOUND,
            payload_type=payload_type, payload=payload,
        ))
        self.session.messages.extend(
            CapturedMessage(
                session_id=session_id, direction=MessageDirection.INBOUND,
                payload_type=PayloadType.BINARY if isinstance(item, bytes) else PayloadType.JSON,
                payload=item,
            )
            for item in self.inbound_payloads
        )

    async def disconnect(self, session_id: str) -> ExplorerSession:
        return self.session

    def messages(self, session_id: str) -> tuple[CapturedMessage, ...]:
        return tuple(self.session.messages)


def probe() -> CapabilityProbe:
    return CapabilityProbe(
        probe_id="test.probe", name="Test probe", transport=TransportType.WEBSOCKET,
        payload_type=PayloadType.JSON, payload={}, mode=ProbeMode.EXPECT_MATCH,
        expected_event="expected", timeout=0,
    )


async def run_with(payloads: list[object]) -> tuple[CapabilityProbeService, CapabilityResult]:
    manager = FakeSessionManager(payloads)
    capture = CaptureService(manager)  # type: ignore[arg-type]
    service = CapabilityProbeService(FakeKernel({  # type: ignore[arg-type]
        ServiceName.SESSION_MANAGER: manager,
        ServiceName.CAPTURE: capture,
    }))
    return service, await service.run("target", probe())


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("payloads", "status"),
    [
        ([], CapabilityStatus.NO_RESPONSE),
        ([{"event": "other"}], CapabilityStatus.NO_MATCH),
        ([{"event": "expected"}], CapabilityStatus.SUPPORTED),
    ],
)
async def test_probe_match_statuses(payloads: list[object], status: CapabilityStatus) -> None:
    _, result = await run_with(payloads)
    assert result.status == status
    assert len(result.captures) == len(payloads)


@pytest.mark.asyncio
async def test_latest_probe_result_replaces_previous() -> None:
    service, first = await run_with([])
    manager = service.kernel.get(ServiceName.SESSION_MANAGER)
    manager.inbound_payloads = [{"event": "expected"}]
    second = await service.run("target", probe())
    assert first.result_id != second.result_id
    assert service.get_results("target") == (second,)


def test_registry_and_safe_pack() -> None:
    assert PROBE_REGISTRY.get("samsung.apps.installed").safety == ProbeSafety.READ_ONLY
    assert PROBE_REGISTRY.get("samsung.remote.home").safety == ProbeSafety.STATE_CHANGE
    assert SAMSUNG_BASIC.probe_ids == ("samsung.apps.installed",)


def test_binary_capture_response_is_base64() -> None:
    raw = b"\xff\x00"
    result = CapabilityResult(
        target_id="target", probe_id="probe", probe_name="Probe",
        status=CapabilityStatus.NO_MATCH, duration_ms=1,
        captures=(ProbeCapture(payload_type=PayloadType.BINARY, payload=raw),),
    )
    assert _probe_response(result).captures[0].payload == b64encode(raw).decode("ascii")


@pytest.mark.asyncio
async def test_capability_manifest_and_unknown_probe() -> None:
    kernel = ExplorerKernel()
    targets = TargetRegistry(kernel)
    targets._targets["target"] = Target(  # noqa: SLF001
        target_id="target", name="TV", uri="ws://tv", transport=TransportType.WEBSOCKET,
    )
    service = CapabilityProbeService(kernel)
    service._results["target"]["probe"] = CapabilityResult(  # noqa: SLF001
        target_id="target", probe_id="probe", probe_name="Probe",
        status=CapabilityStatus.NO_RESPONSE, duration_ms=1,
    )
    kernel.register(targets)
    kernel.register(service)
    api = ApiService(kernel)
    transport = httpx.ASGITransport(app=api.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        manifest = await client.get("/targets/target/capabilities")
        unknown = await client.post("/targets/target/capabilities/probes/unknown")
    assert manifest.status_code == 200
    assert manifest.json()["capabilities"][0]["capability_id"] == "probe"
    assert manifest.json()["generated_at"]
    assert unknown.status_code == 404
