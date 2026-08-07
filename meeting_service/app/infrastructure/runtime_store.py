from __future__ import annotations

from threading import RLock
from uuid import UUID

from meeting_service.app.domain.models import RuntimeSession, RuntimeStatus
from meeting_service.app.infrastructure.repositories import RuntimeRepository


class InMemoryRuntimeStore(RuntimeRepository):
    """Temporary store for the skeleton; replaced by Meeting Service DB."""

    def __init__(self) -> None:
        self._items: dict[UUID, RuntimeSession] = {}
        self._lock = RLock()

    def create(self, meeting_id: UUID, snapshot: dict | None = None) -> RuntimeSession:
        with self._lock:
            current = next((x for x in self._items.values() if x.meeting_id == meeting_id and x.status not in {RuntimeStatus.COMPLETED, RuntimeStatus.FAILED}), None)
            if current:
                return current
            session = RuntimeSession(meeting_id=meeting_id, livekit_room=f"meeting-{meeting_id}")
            self._items[session.runtime_session_id] = session
            return session

    def get(self, meeting_id: UUID) -> RuntimeSession | None:
        with self._lock:
            return next((x for x in self._items.values() if x.meeting_id == meeting_id), None)

    def set_status(self, runtime_id: UUID, status: RuntimeStatus) -> RuntimeSession | None:
        with self._lock:
            session = self._items.get(runtime_id)
            if session:
                session.status = status
            return session

    def delete_meeting(self, meeting_id: UUID) -> int:
        with self._lock:
            ids = [runtime_id for runtime_id, item in self._items.items() if item.meeting_id == meeting_id]
            for runtime_id in ids:
                del self._items[runtime_id]
            return len(ids)
