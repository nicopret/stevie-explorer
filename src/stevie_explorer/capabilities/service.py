from __future__ import annotations

import asyncio
from time import perf_counter

from stevie_explorer.capabilities.models import (
    CapabilityProbe,
    CapabilityResult,
    CapabilityStatus,
    ProbeCapture,
    ProbeMode,
)
from stevie_explorer.capture import CaptureService
from stevie_explorer.identifiers import (
    MessageDirection,
    ServiceName,
)
from stevie_explorer.kernel import BaseService, ExplorerKernel
from stevie_explorer.sessions import SessionManager


class CapabilityProbeService(BaseService):
    name = ServiceName.CAPABILITY_PROBE

    def __init__(self, kernel: ExplorerKernel) -> None:
        self.kernel = kernel

    async def start(self) -> None:
        pass
    
    async def stop(self) -> None:
        pass

    async def run(
        self,
        target_id: str,
        probe: CapabilityProbe,
    ) -> CapabilityResult:
        session_manager: SessionManager = self.kernel.get(
            ServiceName.SESSION_MANAGER
        )

        capture_service: CaptureService = self.kernel.get(
            ServiceName.CAPTURE
        )

        started = perf_counter()

        session = await session_manager.create(target_id)

        try:
            await session_manager.connect(
                session.session_id
            )

            # Start recording immediately BEFORE sending
            # the actual probe request.
            capture = capture_service.start_capture(
                session.session_id
            )

            await session_manager.send(
                session_id=session.session_id,
                payload_type=probe.payload_type,
                payload=probe.payload,
            )

            if probe.mode != ProbeMode.SEND_ONLY:
                await asyncio.sleep(probe.timeout)

            capture = capture_service.stop_capture(
                capture.capture_id
            )

            inbound_messages = [
                message
                for message in capture.messages
                if message.direction
                == MessageDirection.INBOUND
            ]

            captures = tuple(
                ProbeCapture(
                    payload_type=message.payload_type,
                    payload=message.payload,
                    timestamp=message.timestamp,
                )
                for message in inbound_messages
            )

            status = CapabilityStatus.SUPPORTED
            response = None
            matched_event = None

            if probe.mode == ProbeMode.SEND_ONLY:
                status = CapabilityStatus.SUPPORTED

            elif probe.mode == ProbeMode.EXPECT_RESPONSE:
                if inbound_messages:
                    status = CapabilityStatus.SUPPORTED
                    response = inbound_messages[0].payload
                else:
                    status = CapabilityStatus.NO_RESPONSE

            elif probe.mode == ProbeMode.EXPECT_MATCH:
                matched_message = self._find_match(
                    inbound_messages,
                    probe.expected_event,
                )

                if matched_message is not None:
                    status = CapabilityStatus.SUPPORTED
                    response = matched_message.payload
                    matched_event = probe.expected_event

                elif inbound_messages:
                    status = CapabilityStatus.NO_MATCH

                else:
                    status = CapabilityStatus.NO_RESPONSE

            duration_ms = (
                perf_counter() - started
            ) * 1000

            return CapabilityResult(
                target_id=target_id,
                probe_id=probe.probe_id,
                probe_name=probe.name,
                status=status,
                duration_ms=duration_ms,
                response=response,
                error=None,
                matched_event=matched_event,
                captures=captures,
            )

        except Exception as exc:
            duration_ms = (
                perf_counter() - started
            ) * 1000

            return CapabilityResult(
                target_id=target_id,
                probe_id=probe.probe_id,
                probe_name=probe.name,
                status=CapabilityStatus.ERROR,
                duration_ms=duration_ms,
                response=None,
                error=str(exc),
                matched_event=None,
                captures=(),
            )

        finally:
            try:
                await session_manager.disconnect(
                    session.session_id
                )
            except Exception:
                pass

    @staticmethod
    def _find_match(
        messages,
        expected_event: str | None,
    ):
        if expected_event is None:
            return None

        for message in messages:
            payload = message.payload

            if not isinstance(payload, dict):
                continue

            if payload.get("event") == expected_event:
                return message

        return None
