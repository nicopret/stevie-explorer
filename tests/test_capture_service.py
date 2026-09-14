import pytest

from stevie_explorer.capabilities import (
    CapabilityProbe,
    CapabilityProbeService,
    CapabilityStatus,
    ProbeMode,
)
from stevie_explorer.capture import CaptureService
from stevie_explorer.identifiers import (
    MessageDirection,
    PayloadType,
    ServiceName,
    TransportType,
)
from stevie_explorer.sessions.models import CapturedMessage, ExplorerSession


class StubSessionManager:
    def __init__(self, session: ExplorerSession) -> None:
        self.session = session

    def messages(self, session_id: str) -> tuple[CapturedMessage, ...]:
        assert session_id == self.session.session_id
        return tuple(self.session.messages)


class ProbeSessionManager(StubSessionManager):
    async def create(self, target_id: str) -> ExplorerSession:
        assert target_id == self.session.target_id
        return self.session

    async def connect(self, session_id: str) -> ExplorerSession:
        assert session_id == self.session.session_id
        return self.session

    async def send(
        self,
        session_id: str,
        payload_type: PayloadType,
        payload: object,
    ) -> CapturedMessage:
        outbound = CapturedMessage(
            session_id=session_id,
            direction=MessageDirection.OUTBOUND,
            payload_type=payload_type,
            payload=payload,
        )
        inbound = CapturedMessage(
            session_id=session_id,
            direction=MessageDirection.INBOUND,
            payload_type=PayloadType.JSON,
            payload={"event": "ed.installedApp.get", "data": []},
        )
        self.session.messages.extend((outbound, inbound))
        return outbound

    async def disconnect(self, session_id: str) -> ExplorerSession:
        assert session_id == self.session.session_id
        return self.session


class StubKernel:
    def __init__(self, services: dict[ServiceName, object]) -> None:
        self.services = services

    def get(self, name: ServiceName) -> object:
        return self.services[name]


def make_message(session_id: str, payload: str) -> CapturedMessage:
    return CapturedMessage(
        session_id=session_id,
        direction=MessageDirection.INBOUND,
        payload_type=PayloadType.TEXT,
        payload=payload,
    )


def test_capture_returns_all_messages_added_after_start() -> None:
    session = ExplorerSession(target_id="target")
    session.messages.append(make_message(session.session_id, "before"))
    service = CaptureService(StubSessionManager(session))  # type: ignore[arg-type]

    capture = service.start_capture(session.session_id)
    first = make_message(session.session_id, "first")
    second = make_message(session.session_id, "second")
    session.messages.extend((first, second))

    assert service.messages(capture.capture_id) == [first, second]

    stopped = service.stop_capture(capture.capture_id)

    assert stopped.messages == [first, second]
    assert service.messages(capture.capture_id) == [first, second]


def test_capture_can_stop_without_new_messages() -> None:
    session = ExplorerSession(target_id="target")
    service = CaptureService(StubSessionManager(session))  # type: ignore[arg-type]

    capture = service.start_capture(session.session_id)
    stopped = service.stop_capture(capture.capture_id)

    assert stopped.messages == []


@pytest.mark.asyncio
async def test_probe_converts_captured_inbound_messages() -> None:
    session = ExplorerSession(target_id="target")
    manager = ProbeSessionManager(session)
    capture_service = CaptureService(manager)  # type: ignore[arg-type]
    kernel = StubKernel(
        {
            ServiceName.SESSION_MANAGER: manager,
            ServiceName.CAPTURE: capture_service,
        }
    )
    service = CapabilityProbeService(kernel)  # type: ignore[arg-type]
    probe = CapabilityProbe(
        probe_id="samsung.apps.installed",
        name="Samsung installed app discovery",
        transport=TransportType.WEBSOCKET,
        payload_type=PayloadType.JSON,
        payload={"method": "ms.channel.emit"},
        mode=ProbeMode.EXPECT_MATCH,
        expected_event="ed.installedApp.get",
        timeout=0,
    )

    result = await service.run("target", probe)

    assert result.status == CapabilityStatus.SUPPORTED
    assert result.error is None
    assert len(result.captures) == 1
    assert result.captures[0].payload == {
        "event": "ed.installedApp.get",
        "data": [],
    }
    assert result.captures[0].timestamp == session.messages[1].timestamp
