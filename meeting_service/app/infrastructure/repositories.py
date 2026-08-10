from __future__ import annotations

from typing import Protocol
from uuid import UUID

from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from meeting_service.app.domain.models import RuntimeSession, RuntimeStatus
from meeting_service.app.infrastructure.models import AIEventRecord, IdempotencyRecord, RuntimeSessionRecord, TranscriptSegmentRecord


class RuntimeRepository(Protocol):
    def create(self, meeting_id: UUID, snapshot: dict) -> RuntimeSession: ...
    def get(self, meeting_id: UUID) -> RuntimeSession | None: ...
    def get_by_id(self, runtime_id: UUID) -> RuntimeSession | None: ...
    def claim_stop(self, runtime_id: UUID) -> tuple[RuntimeSession | None, bool]: ...
    def get_idempotency(self, operation: str, key: str, request_hash: str) -> dict | None: ...
    def put_idempotency(self, operation: str, key: str, request_hash: str, response: dict) -> dict: ...
    def update_snapshot(self, meeting_id: UUID, snapshot: dict) -> dict: ...
    def set_status(self, runtime_id: UUID, status: RuntimeStatus) -> RuntimeSession | None: ...
    def delete_meeting(self, meeting_id: UUID) -> int: ...


def _to_domain(record: RuntimeSessionRecord) -> RuntimeSession:
    return RuntimeSession(
        meeting_id=record.meeting_id,
        runtime_session_id=record.id,
        status=RuntimeStatus(record.status),
        livekit_room=record.livekit_room,
        created_at=record.created_at,
    )


class SqlAlchemyRuntimeRepository:
    """Meeting-owned repository. `meeting_id` is an external ID, never an FK."""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._sessions = session_factory

    def create(self, meeting_id: UUID, snapshot: dict) -> RuntimeSession:
        with self._sessions.begin() as session:
            existing = session.scalar(select(RuntimeSessionRecord).where(RuntimeSessionRecord.meeting_id == meeting_id, RuntimeSessionRecord.status.not_in([RuntimeStatus.COMPLETED.value, RuntimeStatus.FAILED.value])))
            if existing:
                return _to_domain(existing)
            # A terminal demo runtime owns the unique room name. Reuse that
            # row for a retry instead of inserting a second record that would
            # violate ``meeting_runtime_sessions.livekit_room``. This keeps
            # start idempotent while preserving the Meeting Service's single
            # active-room invariant.
            terminal = session.scalar(
                select(RuntimeSessionRecord)
                .where(RuntimeSessionRecord.meeting_id == meeting_id)
                .order_by(RuntimeSessionRecord.created_at.desc())
            )
            if terminal:
                terminal.meeting_snapshot_json = snapshot
                terminal.status = RuntimeStatus.STARTING.value
                terminal.started_at = None
                terminal.ended_at = None
                terminal.error_code = None
                terminal.error_message = None
                session.flush()
                return _to_domain(terminal)
            record = RuntimeSessionRecord(meeting_id=meeting_id, meeting_snapshot_json=snapshot, livekit_room=f"meeting-{meeting_id}", status=RuntimeStatus.STARTING.value)
            try:
                # Keep the unique active-meeting constraint inside a savepoint
                # so a concurrent loser can still read and return the winner.
                with session.begin_nested():
                    session.add(record)
                    session.flush()
            except IntegrityError:
                existing = session.scalar(
                    select(RuntimeSessionRecord).where(
                        RuntimeSessionRecord.meeting_id == meeting_id,
                        RuntimeSessionRecord.status.not_in(
                            [RuntimeStatus.COMPLETED.value, RuntimeStatus.FAILED.value]
                        ),
                    )
                )
                if existing is None:
                    raise
                return _to_domain(existing)
            return _to_domain(record)

    def get(self, meeting_id: UUID) -> RuntimeSession | None:
        with self._sessions() as session:
            record = session.scalar(select(RuntimeSessionRecord).where(RuntimeSessionRecord.meeting_id == meeting_id).order_by(RuntimeSessionRecord.created_at.desc()))
            return _to_domain(record) if record else None

    def get_by_id(self, runtime_id: UUID) -> RuntimeSession | None:
        with self._sessions() as session:
            record = session.get(RuntimeSessionRecord, runtime_id)
            return _to_domain(record) if record else None

    def claim_stop(self, runtime_id: UUID) -> tuple[RuntimeSession | None, bool]:
        """Atomically claim an active runtime for the single stop side effect."""
        with self._sessions.begin() as session:
            result = session.execute(
                update(RuntimeSessionRecord)
                .where(
                    RuntimeSessionRecord.id == runtime_id,
                    RuntimeSessionRecord.status.in_(
                        [
                            RuntimeStatus.STARTING.value,
                            RuntimeStatus.READY.value,
                            RuntimeStatus.RECORDING.value,
                        ]
                    ),
                )
                .values(status=RuntimeStatus.STOPPING.value)
            )
            record = session.get(RuntimeSessionRecord, runtime_id)
            return (_to_domain(record) if record else None, bool(result.rowcount))

    def get_idempotency(self, operation: str, key: str, request_hash: str) -> dict | None:
        with self._sessions() as session:
            record = session.scalar(
                select(IdempotencyRecord).where(
                    IdempotencyRecord.operation == operation,
                    IdempotencyRecord.key == key,
                )
            )
            if record is None:
                return None
            if record.request_hash and record.request_hash != request_hash:
                raise ValueError("idempotency key was reused with a different request")
            return dict(record.response_json)

    def put_idempotency(self, operation: str, key: str, request_hash: str, response: dict) -> dict:
        with self._sessions.begin() as session:
            existing = session.scalar(
                select(IdempotencyRecord).where(
                    IdempotencyRecord.operation == operation,
                    IdempotencyRecord.key == key,
                )
            )
            if existing is not None:
                if existing.request_hash and existing.request_hash != request_hash:
                    raise ValueError("idempotency key was reused with a different request")
                return dict(existing.response_json)
            record = IdempotencyRecord(
                operation=operation,
                key=key,
                request_hash=request_hash,
                response_json=dict(response),
            )
            try:
                with session.begin_nested():
                    session.add(record)
                    session.flush()
            except IntegrityError:
                existing = session.scalar(
                    select(IdempotencyRecord).where(
                        IdempotencyRecord.operation == operation,
                        IdempotencyRecord.key == key,
                    )
                )
                if existing is None:
                    raise
                if existing.request_hash and existing.request_hash != request_hash:
                    raise ValueError("idempotency key was reused with a different request")
                return dict(existing.response_json)
            return dict(response)

    def update_snapshot(self, meeting_id: UUID, snapshot: dict) -> dict:
        with self._sessions.begin() as session:
            record = session.scalar(
                select(RuntimeSessionRecord)
                .where(RuntimeSessionRecord.meeting_id == meeting_id)
                .order_by(RuntimeSessionRecord.created_at.desc())
            )
            if record is None:
                raise LookupError("runtime not found")
            if record.status in {RuntimeStatus.COMPLETED.value, RuntimeStatus.FAILED.value}:
                raise ValueError("runtime is no longer active")
            current = dict(record.meeting_snapshot_json or {})
            current_revision = int(current.get("snapshot_revision") or 0)
            next_revision = int(snapshot.get("snapshot_revision") or 0)
            if next_revision <= current_revision:
                raise ValueError(f"snapshot revision must be greater than {current_revision}")
            record.meeting_snapshot_json = dict(snapshot)
            session.flush()
            return {
                "meeting_id": str(meeting_id),
                "runtime_session_id": str(record.id),
                "snapshot_revision": next_revision,
                "status": record.status,
                "snapshot": dict(snapshot),
            }

    def set_status(self, runtime_id: UUID, status: RuntimeStatus) -> RuntimeSession | None:
        with self._sessions.begin() as session:
            record = session.get(RuntimeSessionRecord, runtime_id)
            if not record:
                return None
            record.status = status.value
            return _to_domain(record)

    def delete_meeting(self, meeting_id: UUID) -> int:
        with self._sessions.begin() as session:
            result = session.execute(delete(RuntimeSessionRecord).where(RuntimeSessionRecord.meeting_id == meeting_id))
            return int(result.rowcount or 0)


class SqlAlchemyAIEventRepository:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._sessions = session_factory

    def accept(self, event: dict) -> str:
        with self._sessions.begin() as session:
            event_id = UUID(str(event["event_id"]))
            if session.get(AIEventRecord, event_id):
                return "duplicate"
            runtime_id = UUID(str(event["runtime_session_id"]))
            latest = session.scalar(select(AIEventRecord).where(AIEventRecord.runtime_session_id == runtime_id).order_by(AIEventRecord.sequence.desc()))
            if latest and int(event["sequence"]) <= latest.sequence:
                return "stale"
            if event["type"] == "transcript.final":
                payload = dict(event["payload"])
                segment_id = str(payload["segment_id"])
                existing_segment = session.scalar(
                    select(TranscriptSegmentRecord).where(
                        TranscriptSegmentRecord.meeting_id == UUID(str(event["meeting_id"])),
                        TranscriptSegmentRecord.segment_id == segment_id,
                    )
                )
                if existing_segment is None:
                    payload.setdefault("created_at", event["occurred_at"])
                    session.add(
                        TranscriptSegmentRecord(
                            meeting_id=UUID(str(event["meeting_id"])),
                            segment_id=segment_id,
                            payload=payload,
                        )
                    )
            session.add(AIEventRecord(event_id=event_id, meeting_id=UUID(str(event["meeting_id"])), runtime_session_id=runtime_id, event_type=event["type"], sequence=int(event["sequence"]), payload=event["payload"]))
            return "accepted"

    def delete_meeting(self, meeting_id: UUID) -> int:
        with self._sessions.begin() as session:
            result = session.execute(delete(AIEventRecord).where(AIEventRecord.meeting_id == meeting_id))
            return int(result.rowcount or 0)
