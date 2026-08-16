from __future__ import annotations

from datetime import UTC, datetime

from stevie_explorer.capture.models import Capture
from stevie_explorer.identifiers import ServiceName
from stevie_explorer.kernel import BaseService
from stevie_explorer.sessions import SessionManager

class CaptureService(BaseService):
    name = ServiceName.CAPTURE

    def __init__(self, session_manager: SessionManager) -> None:
        self._session_manager = session_manager
        self._captures: dict[str, Capture] = {}
    
    async def start(self) -> None:
        pass
    
    async def stop(self) -> None:
        for caputure in self._captures.values():
            if caputure.is_recording:
                self._stop_capture(caputure)
    
    def start_capture(self, session_id: str) -> Capture:
        messages = self._session_manager.messages(session_id)

        capture = Capture(
            session_id = session_id,
            start_index = len(messages)
        )

        self._captures[capture.capture_id] = capture

        return capture

    def _stop_capture(self, capture_id: str) -> Capture:
        capture = self.get(capture_id)

        if not capture.is_recording:
            return capture
        
        messages = self._session_manager.messages(capture.session_id)

        capture.messages.extend(messages[capture.start_index])

        self._stop_capture(capture)

        return capture
    
    def get(self, capture_id: str) -> Capture:
        try:
            return self._captures[capture_id]
        except KeyError as exc:
            raise KeyError(f"Unknown capture: {capture_id}") from exc
    
    def list(self) -> list[Capture]:
        return list(self._captures.values())
    
    def messages(self, capture_id: str) -> list:
        capture = self.get(capture_id)

        if capture.is_recording:
            session_messages = (
                self._session_manager.messages(capture.session_id)
            )
            return session_messages[capture.start_index]

        return list(capture.messages)
    
    @staticmethod
    def _stop_capture(capture: Capture) -> None:
        capture.stopped_at = datetime.now(UTC)
