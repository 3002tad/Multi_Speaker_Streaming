from __future__ import annotations

from threading import RLock
from uuid import UUID

from meeting_service.app.domain.models import RuntimeSession, RuntimeStatus
from meeting_service.app.infrastructure.repositories import RuntimeRepository


class InMemoryRuntimeStore(RuntimeRepository):
    """Temporary store for the skeleton; replaced by Meeting Service DB."""

    def __init__(self) -> None:
        self._items: dict[UUID, RuntimeSession] = {}
        self._snapshots: dict[UUID, dict] = {}
        self._idempotency: dict[tuple[str, str], tuple[str, dict]] = {}
        self._lock = RLock()

    def create(self, meeting_id: UUID, snapshot: dict | None = None) -> RuntimeSession:
        with self._lock:
            current = next(
                (
                    x
                    for x in reversed(list(self._items.values()))
                    if x.meeting_id == meeting_id and x.status not in {RuntimeStatus.COMPLETED, RuntimeStatus.FAILED}
                ),
                None,
            )
            if current:
                return current
            terminal = next((x for x in reversed(list(self._items.values())) if x.meeting_id == meeting_id), None)
            if terminal:
                terminal.status = RuntimeStatus.STARTING
                self._snapshots[terminal.runtime_session_id] = dict(snapshot or {})
                return terminal
            session = RuntimeSession(meeting_id=meeting_id, livekit_room=f"meeting-{meeting_id}")
            self._items[session.runtime_session_id] = session
            self._snapshots[session.runtime_session_id] = dict(snapshot or {})
            return session

    def get(self, meeting_id: UUID) -> RuntimeSession | None:
        with self._lock:
            return next((x for x in reversed(list(self._items.values())) if x.meeting_id == meeting_id), None)

    def get_by_id(self, runtime_id: UUID) -> RuntimeSession | None:
        with self._lock:
            return self._items.get(runtime_id)

    def claim_stop(self, runtime_id: UUID) -> tuple[RuntimeSession | None, bool]:
        with self._lock:
            session = self._items.get(runtime_id)
            if session is None:
                return None, False
            if session.status in {RuntimeStatus.STARTING, RuntimeStatus.READY, RuntimeStatus.RECORDING}:
                session.status = RuntimeStatus.STOPPING
                return session, True
            return session, False

    def get_idempotency(self, operation: str, key: str, request_hash: str) -> dict | None:
        with self._lock:
            stored = self._idempotency.get((operation, key))
            if stored is None:
                return None
            stored_hash, response = stored
            if stored_hash and stored_hash != request_hash:
                raise ValueError("idempotency key was reused with a different request")
            return dict(response)

    def put_idempotency(self, operation: str, key: str, request_hash: str, response: dict) -> dict:
        with self._lock:
            stored = self._idempotency.get((operation, key))
            if stored is not None:
                stored_hash, existing = stored
                if stored_hash and stored_hash != request_hash:
                    raise ValueError("idempotency key was reused with a different request")
                return dict(existing)
            self._idempotency[(operation, key)] = (request_hash, dict(response))
            return dict(response)

    def set_status(self, runtime_id: UUID, status: RuntimeStatus) -> RuntimeSession | None:
        with self._lock:
            session = self._items.get(runtime_id)
            if session:
                session.status = status
            return session

    def update_snapshot(self, meeting_id: UUID, snapshot: dict) -> dict:
        with self._lock:
            session = self.get(meeting_id)
            if session is None:
                raise LookupError("runtime not found")
            if session.status in {RuntimeStatus.COMPLETED, RuntimeStatus.FAILED}:
                raise ValueError("runtime is no longer active")
            current = self._snapshots.get(session.runtime_session_id, {})
            current_revision = int(current.get("snapshot_revision") or 0)
            next_revision = int(snapshot.get("snapshot_revision") or 0)
            if next_revision <= current_revision:
                raise ValueError(f"snapshot revision must be greater than {current_revision}")
            self._snapshots[session.runtime_session_id] = dict(snapshot)
            return {
                "meeting_id": str(meeting_id),
                "runtime_session_id": str(session.runtime_session_id),
                "snapshot_revision": next_revision,
                "status": session.status.value,
                "snapshot": dict(snapshot),
            }

    def get_snapshot(self, meeting_id: UUID) -> dict:
        with self._lock:
            session = self.get(meeting_id)
            if session is None:
                raise LookupError("runtime not found")
            return dict(self._snapshots.get(session.runtime_session_id, {}))

    def delete_meeting(self, meeting_id: UUID) -> int:
        with self._lock:
            ids = [runtime_id for runtime_id, item in self._items.items() if item.meeting_id == meeting_id]
            for runtime_id in ids:
                del self._items[runtime_id]
                self._snapshots.pop(runtime_id, None)
            return len(ids)
