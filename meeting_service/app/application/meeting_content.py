from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from threading import RLock
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session, sessionmaker

from meeting_service.app.infrastructure.models import MinutesExportRecord, MinutesRevisionRecord, TranscriptSegmentRecord


class MinutesRevisionConflict(RuntimeError):
    """Raised when an editor saves against a stale minutes revision."""


class MinutesStateConflict(RuntimeError):
    """Raised when a minutes lifecycle transition is invalid."""


class MeetingContentStore:
    """In-memory implementation used when persistence is disabled."""

    def __init__(self) -> None:
        self._lock = RLock()
        self._transcripts: dict[UUID, list[dict[str, Any]]] = {}
        self._minutes: dict[UUID, dict[str, Any]] = {}
        self._exports: dict[UUID, dict[str, Any]] = {}

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

    def save_minutes(
        self,
        meeting_id: UUID,
        document: dict[str, Any],
        status: str | None = None,
        base_revision: int | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            previous = self._minutes.get(meeting_id) or _default_minutes(meeting_id)
            current_revision = int(previous.get("revision", 0))
            if base_revision is not None and base_revision != current_revision:
                raise MinutesRevisionConflict(f"stale minutes revision; expected {current_revision}")
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
            export_ids = [export_id for export_id, item in self._exports.items() if item["meeting_id"] == str(meeting_id)]
            existed = int(meeting_id in self._transcripts or meeting_id in self._minutes or bool(export_ids))
            self._transcripts.pop(meeting_id, None)
            self._minutes.pop(meeting_id, None)
            for export_id in export_ids:
                self._exports.pop(export_id, None)
            return existed

    def find_export(self, meeting_id: UUID, revision: int, export_format: str) -> dict[str, Any] | None:
        with self._lock:
            return next((deepcopy(item) for item in self._exports.values() if item["meeting_id"] == str(meeting_id) and item["minutes_revision"] == revision and item["format"] == export_format), None)

    def get_export(self, meeting_id: UUID, export_id: UUID) -> dict[str, Any] | None:
        with self._lock:
            item = self._exports.get(export_id)
            return deepcopy(item) if item and item["meeting_id"] == str(meeting_id) else None

    def list_exports(self, meeting_id: UUID) -> list[dict[str, Any]]:
        with self._lock:
            return [deepcopy(item) for item in self._exports.values() if item["meeting_id"] == str(meeting_id)]

    def create_export(self, metadata: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            export_id = UUID(str(metadata.get("id") or uuid4()))
            item = {**metadata, "id": str(export_id)}
            existing = self.find_export(UUID(item["meeting_id"]), int(item["minutes_revision"]), str(item["format"]))
            if existing:
                return existing
            self._exports[export_id] = deepcopy(item)
            return deepcopy(item)

    def transition_minutes(self, meeting_id: UUID, target_status: str) -> dict[str, Any]:
        with self._lock:
            previous = self._minutes.get(meeting_id) or _default_minutes(meeting_id)
            current_status = previous.get("status", "DRAFT")
            expected = {"REVIEWING": "DRAFT", "APPROVED": "REVIEWING"}.get(target_status)
            if expected is None or current_status != expected:
                raise MinutesStateConflict(f"cannot transition minutes from {current_status} to {target_status}")
            item = {
                **deepcopy(previous),
                "revision": int(previous.get("revision", 0)) + 1,
                "status": target_status,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
            self._minutes[meeting_id] = item
            return deepcopy(item)


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

    def save_minutes(
        self,
        meeting_id: UUID,
        document: dict[str, Any],
        status: str | None = None,
        base_revision: int | None = None,
    ) -> dict[str, Any]:
        with self._sessions.begin() as session:
            previous = session.scalar(
                select(MinutesRevisionRecord)
                .where(MinutesRevisionRecord.meeting_id == meeting_id)
                .order_by(MinutesRevisionRecord.revision.desc())
            )
            current_revision = previous.revision if previous else 0
            if base_revision is not None and base_revision != current_revision:
                raise MinutesRevisionConflict(f"stale minutes revision; expected {current_revision}")
            revision = current_revision + 1
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
            export_result = session.execute(delete(MinutesExportRecord).where(MinutesExportRecord.meeting_id == meeting_id))
            return int((transcript_result.rowcount or 0) + (minutes_result.rowcount or 0) + (export_result.rowcount or 0))

    @staticmethod
    def _export_dict(row: MinutesExportRecord) -> dict[str, Any]:
        return {
            "id": str(row.id),
            "meeting_id": str(row.meeting_id),
            "minutes_revision": row.minutes_revision,
            "minutes_status": row.minutes_status,
            "format": row.format,
            "storage_key": row.storage_key,
            "filename": row.filename,
            "content_type": row.content_type,
            "size_bytes": row.size_bytes,
            "checksum": row.checksum,
            "created_by": row.created_by,
            "created_at": row.created_at.isoformat(),
        }

    def find_export(self, meeting_id: UUID, revision: int, export_format: str) -> dict[str, Any] | None:
        with self._sessions() as session:
            row = session.scalar(select(MinutesExportRecord).where(MinutesExportRecord.meeting_id == meeting_id, MinutesExportRecord.minutes_revision == revision, MinutesExportRecord.format == export_format))
            return self._export_dict(row) if row else None

    def get_export(self, meeting_id: UUID, export_id: UUID) -> dict[str, Any] | None:
        with self._sessions() as session:
            row = session.scalar(select(MinutesExportRecord).where(MinutesExportRecord.meeting_id == meeting_id, MinutesExportRecord.id == export_id))
            return self._export_dict(row) if row else None

    def list_exports(self, meeting_id: UUID) -> list[dict[str, Any]]:
        with self._sessions() as session:
            rows = session.scalars(select(MinutesExportRecord).where(MinutesExportRecord.meeting_id == meeting_id)).all()
            return [self._export_dict(row) for row in rows]

    def create_export(self, metadata: dict[str, Any]) -> dict[str, Any]:
        with self._sessions.begin() as session:
            existing = session.scalar(select(MinutesExportRecord).where(MinutesExportRecord.meeting_id == UUID(metadata["meeting_id"]), MinutesExportRecord.minutes_revision == int(metadata["minutes_revision"]), MinutesExportRecord.format == metadata["format"]))
            if existing:
                return self._export_dict(existing)
            row = MinutesExportRecord(
                id=UUID(str(metadata.get("id") or uuid4())),
                meeting_id=UUID(metadata["meeting_id"]),
                minutes_revision=int(metadata["minutes_revision"]),
                minutes_status=metadata["minutes_status"],
                format=metadata["format"],
                storage_key=metadata["storage_key"],
                filename=metadata["filename"],
                content_type=metadata["content_type"],
                size_bytes=int(metadata["size_bytes"]),
                checksum=metadata["checksum"],
                created_by=metadata.get("created_by"),
            )
            session.add(row)
            session.flush()
            return self._export_dict(row)

    def transition_minutes(self, meeting_id: UUID, target_status: str) -> dict[str, Any]:
        with self._sessions.begin() as session:
            previous = session.scalar(
                select(MinutesRevisionRecord)
                .where(MinutesRevisionRecord.meeting_id == meeting_id)
                .order_by(MinutesRevisionRecord.revision.desc())
            )
            current_status = previous.status if previous else "DRAFT"
            expected = {"REVIEWING": "DRAFT", "APPROVED": "REVIEWING"}.get(target_status)
            if expected is None or current_status != expected:
                raise MinutesStateConflict(f"cannot transition minutes from {current_status} to {target_status}")
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
                status=target_status,
                document_json=deepcopy(previous.document_json if previous else _default_minutes(meeting_id)["document"]),
                source_segment_ids=source_ids,
            )
            session.add(row)
            session.flush()
            return {
                "meeting_id": str(meeting_id),
                "revision": revision,
                "status": target_status,
                "document": deepcopy(row.document_json),
                "source_segment_ids": source_ids,
                "updated_at": row.created_at.isoformat(),
            }


content_store = MeetingContentStore()
