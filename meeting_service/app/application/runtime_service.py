from __future__ import annotations

from uuid import UUID

from meeting_service.app.domain.models import RuntimeSession, RuntimeStatus
from meeting_service.app.infrastructure.repositories import RuntimeRepository
from meeting_service.app.infrastructure.runtime_store import InMemoryRuntimeStore


class AIControlClient:
    async def create_session(self, payload: dict, idempotency_key: str) -> dict: ...
    async def stop_session(self, runtime_session_id: str, idempotency_key: str) -> dict: ...


class RuntimeStateError(ValueError):
    """Raised when the eCabinet meeting snapshot cannot start AI safely."""


class RuntimeService:
    """Lifecycle use case; persistence will be injected in the DB slice."""

    def __init__(self, store: RuntimeRepository | None = None, ai_client: AIControlClient | None = None) -> None:
        self.store = store or InMemoryRuntimeStore()
        self.ai_client = ai_client

    async def start(self, meeting_id: UUID, snapshot: dict | None = None) -> RuntimeSession:
        snapshot = snapshot or {}
        meeting_status = str((snapshot.get("meeting") or {}).get("status") or "").upper()
        if meeting_status not in {"APPROVED", "ONGOING"}:
            raise RuntimeStateError("AI runtime chỉ được khởi động khi phiên họp APPROVED hoặc ONGOING")
        session = self.store.create(meeting_id, snapshot)
        if self.ai_client:
            payload = dict(snapshot)
            payload.update({"schema_version": 1, "runtime_session_id": str(session.runtime_session_id), "meeting_id": str(meeting_id), "assignment_generation": 1})
            try:
                result = await self.ai_client.create_session(payload, str(session.runtime_session_id))
                if result.get("status") in {"READY", "RECORDING"}:
                    self.store.set_status(session.runtime_session_id, RuntimeStatus.READY)
            except Exception:
                self.store.set_status(session.runtime_session_id, RuntimeStatus.FAILED)
                raise
        return self.store.get(meeting_id) or session

    def status(self, meeting_id: UUID) -> RuntimeSession | None:
        return self.store.get(meeting_id)

    async def stop(self, runtime_session_id: UUID) -> RuntimeSession | None:
        if self.ai_client:
            await self.ai_client.stop_session(str(runtime_session_id), str(runtime_session_id))
        return self.store.set_status(runtime_session_id, RuntimeStatus.COMPLETED)

    def purge(self, meeting_id: UUID) -> int:
        """Delete all runtime rows for a meeting; safe to retry."""
        return self.store.delete_meeting(meeting_id)
