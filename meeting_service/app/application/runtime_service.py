from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import datetime
from uuid import UUID

from meeting_service.app.config import settings
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
        self._operation_locks: dict[str, asyncio.Lock] = {}

    def _lock_for(self, operation: str) -> asyncio.Lock:
        return self._operation_locks.setdefault(operation, asyncio.Lock())

    @staticmethod
    def _fingerprint(value: object) -> str:
        encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()

    @staticmethod
    def _session_from_response(response: dict) -> RuntimeSession:
        return RuntimeSession(
            meeting_id=UUID(str(response["meeting_id"])),
            runtime_session_id=UUID(str(response["runtime_session_id"])),
            status=RuntimeStatus(str(response["status"])),
            livekit_room=str(response.get("livekit_room") or ""),
            created_at=datetime.fromisoformat(str(response["created_at"]).replace("Z", "+00:00")),
        )

    async def start(
        self,
        meeting_id: UUID,
        snapshot: dict | None = None,
        idempotency_key: str | None = None,
    ) -> RuntimeSession:
        snapshot = snapshot or {}
        idempotency_key = idempotency_key or f"legacy-start-{meeting_id}"
        operation = f"start:{meeting_id}"
        request_hash = self._fingerprint(snapshot)
        meeting_status = str((snapshot.get("meeting") or {}).get("status") or "").upper()
        if meeting_status not in {"APPROVED", "ONGOING"}:
            raise RuntimeStateError("AI runtime chỉ được khởi động khi phiên họp APPROVED hoặc ONGOING")
        async with self._lock_for(operation):
            cached = self.store.get_idempotency(operation, idempotency_key, request_hash)
            if cached is not None:
                return self._session_from_response(cached)

            # The DB partial unique index is the cross-process guard.  The
            # service lock also prevents duplicate AI side effects in one
            # worker when two callers arrive at the same time.
            session = self.store.get(meeting_id)
            if session is None or session.status in {RuntimeStatus.COMPLETED, RuntimeStatus.FAILED}:
                session = self.store.create(meeting_id, snapshot)
                if self.ai_client:
                    payload = dict(snapshot)
                    payload.update({
                        "schema_version": 1,
                        "runtime_session_id": str(session.runtime_session_id),
                        "meeting_id": str(meeting_id),
                        "assignment_generation": 1,
                        "livekit": {
                            "url": settings.livekit_url,
                            "room": session.livekit_room,
                        },
                        "callback": {
                            "url": settings.ai_callback_url,
                        },
                    })
                    try:
                        result = await self.ai_client.create_session(payload, str(session.runtime_session_id))
                        if result.get("status") in {"READY", "RECORDING"}:
                            self.store.set_status(session.runtime_session_id, RuntimeStatus.READY)
                    except Exception:
                        self.store.set_status(session.runtime_session_id, RuntimeStatus.FAILED)
                        raise
                session = self.store.get(meeting_id) or session

            self.store.put_idempotency(operation, idempotency_key, request_hash, session.as_dict())
            return session

    def status(self, meeting_id: UUID) -> RuntimeSession | None:
        return self.store.get(meeting_id)

    def runtime(self, runtime_session_id: UUID) -> RuntimeSession | None:
        return self.store.get_by_id(runtime_session_id)

    def update_snapshot(self, meeting_id: UUID, snapshot: dict) -> dict:
        return self.store.update_snapshot(meeting_id, snapshot)

    def snapshot(self, meeting_id: UUID) -> dict:
        return self.store.get_snapshot(meeting_id)

    async def stop(self, runtime_session_id: UUID, idempotency_key: str | None = None) -> RuntimeSession | None:
        idempotency_key = idempotency_key or f"legacy-stop-{runtime_session_id}"
        operation = f"stop:{runtime_session_id}"
        request_hash = self._fingerprint({"runtime_session_id": str(runtime_session_id)})
        async with self._lock_for(operation):
            cached = self.store.get_idempotency(operation, idempotency_key, request_hash)
            if cached is not None:
                return self._session_from_response(cached)

            claimed, did_claim = self.store.claim_stop(runtime_session_id)
            if claimed is None:
                return None
            session = claimed
            if did_claim:
                try:
                    if self.ai_client:
                        await self.ai_client.stop_session(str(runtime_session_id), idempotency_key)
                    session = self.store.set_status(runtime_session_id, RuntimeStatus.COMPLETED) or session
                except Exception:
                    self.store.set_status(runtime_session_id, RuntimeStatus.FAILED)
                    raise
            # Do not freeze a transient STOPPING response in the durable
            # replay table when another worker owns the stop side effect.
            # A later retry must observe the eventual COMPLETED/FAILED state.
            if session.status != RuntimeStatus.STOPPING:
                self.store.put_idempotency(operation, idempotency_key, request_hash, session.as_dict())
            return session

    def purge(self, meeting_id: UUID, idempotency_key: str | None = None) -> int:
        """Delete all runtime rows for a meeting; safe to retry."""
        idempotency_key = idempotency_key or f"legacy-purge-{meeting_id}"
        operation = f"purge:{meeting_id}"
        request_hash = self._fingerprint({"meeting_id": str(meeting_id)})
        cached = self.store.get_idempotency(operation, idempotency_key, request_hash)
        if cached is not None:
            return int(cached.get("runtime_rows_deleted", 0))
        deleted = self.store.delete_meeting(meeting_id)
        self.store.put_idempotency(
            operation,
            idempotency_key,
            request_hash,
            {"meeting_id": str(meeting_id), "runtime_rows_deleted": deleted},
        )
        return deleted
