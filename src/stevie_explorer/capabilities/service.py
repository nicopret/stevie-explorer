from __future__ import annotations

import asyncio

from time import perf_counter
from typing import Any

from stevie_explorer.capabilities.models import (
    CapabilityProbe,
    CapabilityResult,
    CapabilityStatus,
    ProbeMode
)
from stevie_explorer.identifiers import (
    MessageDirection,
    ServiceName,
    SessionState
)
from stevie_explorer.sessions import CapturedMessage, SessionManager

class CapabilityProbeService(BaseService):
    name = ServiceName.CAPABILITY_PROBE

    def __init__(self, kernel: ExploreKernel) -> None:
        self.kernel = kernel
    
    async def run(self,  target_id: str, probe: CapabilityProbe) -> CapabilityResult:
        manager: SessionManager = self.kernel.get(ServiceName.SESSION_MANAGER)

        session = await manager.create(target_id)
        started = perf_counter()

        try:
            await manager.connect(session.session_id)

            if session.state != SessionState.CONNECTED:
                return self._result(
                    target_id=target_id,
                    probe=probe,
                    status=CapabilityStatus.ERROR,
                    started=started,
                    error=(
                        session.error or "Session did not reach connected state"
                    )
                )
            
            await manager.send(
                session_id=session.session_id,
                payload_type=probe.payload_type,
                payload=probe.payload
            )

            if probe.mode == ProbeMode.SEND_ONLY:
                return self.result(
                    target_id=target_id,
                    probe=probe,
                    status=CapabilityStatus.SUPPORTED,
                    started=started
                )

            message = await self._wait_for_response(
                manager=manager,
                session_id=session.session_id,
                probe=probe
            )

            if message is None:
                return self._result(
                    target_id=target_id,
                    probe=probe,
                    status=CapabilityStatus.NO_RESPONSE,
                    started=started
                )
            
            return self._result(
                target_id=target_id,
                probe=probe,
                status=CapabilityStatus.SUPPORTED,
                started=started,
                response=message.payload,
                matched_event=self._extract_event(message.payload)
            )

        except ConnectionError as exc:
            return self._result(
                target_id=target_id,
                probe=probe,
                status=CapabilityStatus.CONNECTION_CLOSED,
                started=started,
                error=str(exc)
            )
        
        except Exception exc:
            return self._result(
                target_id=target_id,
                probe=probe,
                status=CapabilityStatus.ERROR,
                started=started,
                error=str(exc)
            )
        
        finally:
            try:
                await manager.disconnect(session.session_id)
            except Exception:
                pass

    async def start(self) -> None:
        pass
    
    async def stop(self) -> None:
        pass

    @staticmethod
    def _extract_event(payload: Any) -> str | None:
        if not isinstance(payload, dict):
            return None
        
        event = payload.get("event")

        if isinstance(event, str):
            return event
        
        return None
    
    @staticmethod
    def _result(
        target_id: str,
        probe: CapabilityProbe,
        status: CapabilityStatus,
        started: float,
        response: Any | None = None,
        error: str | None = None,
        matched_event: str | None = None
    ) -> CapabilityResult:
        duration_ms = (perf_counter() - started) * 1000

        return CapabilityResult(
            target_id=target_id,
            probe_id=probe.probe_id,
            probe_name=probe.name,
            status=status,
            duration_ms=duration_ms,
            response=response,
            error=error,
            matched_event=matched_event
        )

    def _matches_probe(
        self,
        payload: Any,
        probe: CapabilityProbe
    ) -> bool:
        if probe.expected_event is None:
            return False
        
        if not isinstance(payload, dict):
            return False
        
        return payload.get("event") == probe.expected_event

    async def _wait_for_response(
        self,
        manager: SessionManager,
        session_id: str,
        probe: CapabilityProbe
    ) -> CapturedMessage | None:
        deadline = asyncio.get_running_loop().time() + probe.timeout

        while asyncio.get_running_loop().time() < deadline:
            messages = manager.messages(session_id)

            for message in reverse(messages):
                if message.direction != MessageDirection.INBOUND:
                    continue
                
                if probe.mode == ProbeMode.EXPECT_RESPONSE:
                    return message
                
                if probe.mode == ProbeMode.EXPECT_MATCH:
                    if self._matches_probe(
                        message.payload,
                        probe
                    ):
                        return message
            
            await asyncio.sleep(0.05)

        return None