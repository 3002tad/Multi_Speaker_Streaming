from __future__ import annotations

from uuid import UUID

from meeting_service.app.domain.models import RuntimeSession, RuntimeStatus
from meeting_service.app.infrastructure.runtime_store import InMemoryRuntimeStore


class RuntimeService:
    """Lifecycle use case; persistence will be injected in the DB slice."""

    def __init__(self, store: InMemoryRuntimeStore | None = None) -> None:
        self.store = store or InMemoryRuntimeStore()

    def start(self, meeting_id: UUID) -> RuntimeSession:
        return self.store.create(meeting_id)

    def status(self, meeting_id: UUID) -> RuntimeSession | None:
        return self.store.get(meeting_id)

    def stop(self, runtime_session_id: UUID) -> RuntimeSession | None:
        return self.store.set_status(runtime_session_id, RuntimeStatus.COMPLETED)
