from __future__ import annotations

from typing import Protocol
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.orm import Session, sessionmaker

from meeting_service.app.domain.models import RuntimeSession, RuntimeStatus
from meeting_service.app.infrastructure.models import AIEventRecord, RuntimeSessionRecord, TranscriptSegmentRecord


class RuntimeRepository(Protocol):
    def create(self, meeting_id: UUID, snapshot: dict) -> RuntimeSession: ...
    def get(self, meeting_id: UUID) -> RuntimeSession | None: ...
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
            record = RuntimeSessionRecord(meeting_id=meeting_id, meeting_snapshot_json=snapshot, livekit_room=f"meeting-{meeting_id}", status=RuntimeStatus.STARTING.value)
            session.add(record)
            session.flush()
            return _to_domain(record)

    def get(self, meeting_id: UUID) -> RuntimeSession | None:
        with self._sessions() as session:
            record = session.scalar(select(RuntimeSessionRecord).where(RuntimeSessionRecord.meeting_id == meeting_id).order_by(RuntimeSessionRecord.created_at.desc()))
            return _to_domain(record) if record else None

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
