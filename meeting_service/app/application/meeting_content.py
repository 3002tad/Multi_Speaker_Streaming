from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from threading import RLock
from typing import Any
from uuid import UUID

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session, sessionmaker

from meeting_service.app.infrastructure.models import MinutesRevisionRecord, TranscriptSegmentRecord


class MeetingContentStore:
    """In-memory implementation used when persistence is disabled."""

    def __init__(self) -> None:
        self._lock = RLock()
        self._transcripts: dict[UUID, list[dict[str, Any]]] = {}
        self._minutes: dict[UUID, dict[str, Any]] = {}

    def transcript(self, meeting_id: UUID) -> list[dict[str, Any]]:
        with self._lock:
            return deepcopy(self._transcripts.get(meeting_id, []))

    def append_transcript(self, meeting_id: UUID, segment: dict[str, Any]) -> dict[str, Any]:
        item = dict(segment)
        with self._lock:
            item.setdefault("segment_id", f"segment-{len(self._transcripts.get(meeting_id, [])) + 1}")
            item.setdefault("created_at", datetime.now(timezone.utc).isoformat())
            self._transcripts.setdefault(meeting_id, []).append(item)
            return deepcopy(item)

    def minutes(self, meeting_id: UUID) -> dict[str, Any]:
        with self._lock:
            return deepcopy(self._minutes.get(meeting_id) or _default_minutes(meeting_id))

    def save_minutes(self, meeting_id: UUID, document: dict[str, Any], status: str | None = None) -> dict[str, Any]:
        with self._lock:
            previous = self._minutes.get(meeting_id) or _default_minutes(meeting_id)
            item = {
                "meeting_id": str(meeting_id),
                "revision": int(previous.get("revision", 0)) + 1,
                "status": status or ("DRAFT" if previous.get("status") == "APPROVED" else previous.get("status", "DRAFT")),
                "document": deepcopy(document),
                "source_segment_ids": [str(x.get("segment_id")) for x in self._transcripts.get(meeting_id, [])],
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
            self._minutes[meeting_id] = item
            return deepcopy(item)

    def delete_meeting(self, meeting_id: UUID) -> int:
        with self._lock:
            existed = int(meeting_id in self._transcripts or meeting_id in self._minutes)
            self._transcripts.pop(meeting_id, None)
            self._minutes.pop(meeting_id, None)
            return existed


def _default_minutes(meeting_id: UUID) -> dict[str, Any]:
    return {
        "meeting_id": str(meeting_id),
        "revision": 0,
        "status": "DRAFT",
        "document": {"title": "Biên bản cuộc họp", "summary": "", "topics": [], "decisions": [], "actions": []},
        "source_segment_ids": [],
    }


class SqlAlchemyMeetingContentRepository:
    """Durable Meeting Service-owned transcript and minutes repository."""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._sessions = session_factory

    def transcript(self, meeting_id: UUID) -> list[dict[str, Any]]:
        with self._sessions() as session:
            rows = session.scalars(
                select(TranscriptSegmentRecord)
                .where(TranscriptSegmentRecord.meeting_id == meeting_id)
                .order_by(TranscriptSegmentRecord.created_at, TranscriptSegmentRecord.id)
            ).all()
            return [deepcopy(row.payload) for row in rows]

    def append_transcript(self, meeting_id: UUID, segment: dict[str, Any]) -> dict[str, Any]:
        item = dict(segment)
        with self._sessions.begin() as session:
            if not item.get("segment_id"):
                count = session.scalar(
                    select(func.count()).select_from(TranscriptSegmentRecord).where(TranscriptSegmentRecord.meeting_id == meeting_id)
                ) or 0
                item["segment_id"] = f"segment-{count + 1}"
            item.setdefault("created_at", datetime.now(timezone.utc).isoformat())
            existing = session.scalar(
                select(TranscriptSegmentRecord).where(
                    TranscriptSegmentRecord.meeting_id == meeting_id,
                    TranscriptSegmentRecord.segment_id == str(item["segment_id"]),
                )
            )
            if existing:
                return deepcopy(existing.payload)
            session.add(TranscriptSegmentRecord(meeting_id=meeting_id, segment_id=str(item["segment_id"]), payload=item))
            return deepcopy(item)

    def minutes(self, meeting_id: UUID) -> dict[str, Any]:
        with self._sessions() as session:
            row = session.scalar(
                select(MinutesRevisionRecord)
                .where(MinutesRevisionRecord.meeting_id == meeting_id)
                .order_by(MinutesRevisionRecord.revision.desc())
            )
            if not row:
                return _default_minutes(meeting_id)
            return {
                "meeting_id": str(meeting_id),
                "revision": row.revision,
                "status": row.status,
                "document": deepcopy(row.document_json),
                "source_segment_ids": list(row.source_segment_ids or []),
                "updated_at": row.created_at.isoformat(),
            }

    def save_minutes(self, meeting_id: UUID, document: dict[str, Any], status: str | None = None) -> dict[str, Any]:
        with self._sessions.begin() as session:
            previous = session.scalar(
                select(MinutesRevisionRecord)
                .where(MinutesRevisionRecord.meeting_id == meeting_id)
                .order_by(MinutesRevisionRecord.revision.desc())
            )
            revision = (previous.revision if previous else 0) + 1
            source_ids = [
                str(row.segment_id)
                for row in session.scalars(
                    select(TranscriptSegmentRecord)
                    .where(TranscriptSegmentRecord.meeting_id == meeting_id)
                    .order_by(TranscriptSegmentRecord.created_at)
                ).all()
            ]
            row = MinutesRevisionRecord(
                meeting_id=meeting_id,
                revision=revision,
                status=status or ("DRAFT" if previous and previous.status == "APPROVED" else (previous.status if previous else "DRAFT")),
                document_json=deepcopy(document),
                source_segment_ids=source_ids,
            )
            session.add(row)
            session.flush()
            return {
                "meeting_id": str(meeting_id),
                "revision": revision,
                "status": row.status,
                "document": deepcopy(document),
                "source_segment_ids": source_ids,
                "updated_at": row.created_at.isoformat(),
            }

    def delete_meeting(self, meeting_id: UUID) -> int:
        with self._sessions.begin() as session:
            transcript_result = session.execute(delete(TranscriptSegmentRecord).where(TranscriptSegmentRecord.meeting_id == meeting_id))
            minutes_result = session.execute(delete(MinutesRevisionRecord).where(MinutesRevisionRecord.meeting_id == meeting_id))
            return int((transcript_result.rowcount or 0) + (minutes_result.rowcount or 0))


content_store = MeetingContentStore()
