from __future__ import annotations

from uuid import UUID

from meeting_service.app.domain.models import RuntimeSession, RuntimeStatus
from meeting_service.app.infrastructure.repositories import RuntimeRepository
from meeting_service.app.infrastructure.runtime_store import InMemoryRuntimeStore


class RuntimeService:
    """Lifecycle use case; persistence will be injected in the DB slice."""

    def __init__(self, store: RuntimeRepository | None = None) -> None:
        self.store = store or InMemoryRuntimeStore()

    def start(self, meeting_id: UUID, snapshot: dict | None = None) -> RuntimeSession:
        return self.store.create(meeting_id, snapshot or {})

    def status(self, meeting_id: UUID) -> RuntimeSession | None:
        return self.store.get(meeting_id)

    def stop(self, runtime_session_id: UUID) -> RuntimeSession | None:
        return self.store.set_status(runtime_session_id, RuntimeStatus.COMPLETED)
