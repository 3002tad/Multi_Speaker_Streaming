"""P1-01 control plane for evidence-backed minutes analysis.

Meeting Service owns snapshots and state.  Meeting AI only receives the
immutable evidence payload and returns an acceptance response; composition and
the ``minutes.updated`` result callback are deliberately deferred to P1-02.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from hashlib import sha256
import json
from threading import RLock
from typing import Any, Protocol
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from meeting_service.app.infrastructure.models import MinutesAnalysisRecord


ANALYSIS_PENDING = "PENDING"
ANALYSIS_RUNNING = "RUNNING"
ANALYSIS_SUCCEEDED = "SUCCEEDED"
ANALYSIS_FAILED = "FAILED"


class MinutesAnalysisConflict(RuntimeError):
    """Raised when an evidence snapshot cannot be analyzed safely."""


class MinutesAnalysisRepository(Protocol):
    def get(self, meeting_id: UUID) -> dict[str, Any] | None: ...
    def create_or_get(
        self,
        meeting_id: UUID,
        runtime_session_id: UUID,
        base_transcript_revision: int,
        evidence: dict[str, Any],
    ) -> tuple[dict[str, Any], bool]: ...
    def set_status(
        self, analysis_id: UUID, status: str, error_message: str | None = None
    ) -> dict[str, Any]: ...
    def delete_meeting(self, meeting_id: UUID) -> int: ...


def _as_dict(value: dict[str, Any]) -> dict[str, Any]:
    return deepcopy(value)


class InMemoryMinutesAnalysisRepository:
    def __init__(self) -> None:
        self._lock = RLock()
        self._items: dict[UUID, dict[str, Any]] = {}
        self._by_snapshot: dict[tuple[UUID, int], UUID] = {}

    def get(self, meeting_id: UUID) -> dict[str, Any] | None:
        with self._lock:
            candidates = [item for item in self._items.values() if item["meeting_id"] == str(meeting_id)]
            if not candidates:
                return None
            return _as_dict(max(candidates, key=lambda item: item["updated_at"]))

    def create_or_get(self, meeting_id: UUID, runtime_session_id: UUID, base_transcript_revision: int, evidence: dict[str, Any]) -> tuple[dict[str, Any], bool]:
        with self._lock:
            key = (meeting_id, base_transcript_revision)
            existing_id = self._by_snapshot.get(key)
            if existing_id:
                return _as_dict(self._items[existing_id]), False
            now = datetime.now(timezone.utc).isoformat()
            analysis_id = UUID(str(evidence["analysis_id"]))
            item = {
                "analysis_id": str(analysis_id),
                "meeting_id": str(meeting_id),
                "runtime_session_id": str(runtime_session_id),
                "base_transcript_revision": base_transcript_revision,
                "status": ANALYSIS_PENDING,
                "error_message": None,
                "evidence": _as_dict(evidence),
                "created_at": now,
                "updated_at": now,
            }
            self._items[analysis_id] = item
            self._by_snapshot[key] = analysis_id
            return _as_dict(item), True

    def set_status(self, analysis_id: UUID, status: str, error_message: str | None = None) -> dict[str, Any]:
        with self._lock:
            item = self._items[analysis_id]
            item["status"] = status
            item["error_message"] = error_message
            item["updated_at"] = datetime.now(timezone.utc).isoformat()
            return _as_dict(item)

    def delete_meeting(self, meeting_id: UUID) -> int:
        with self._lock:
            ids = [key for key, item in self._items.items() if item["meeting_id"] == str(meeting_id)]
            for analysis_id in ids:
                item = self._items.pop(analysis_id)
                self._by_snapshot.pop((meeting_id, item["base_transcript_revision"]), None)
            return len(ids)


class SqlAlchemyMinutesAnalysisRepository:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._sessions = session_factory

    @staticmethod
    def _item(row: MinutesAnalysisRecord) -> dict[str, Any]:
        return {
            "analysis_id": str(row.id),
            "meeting_id": str(row.meeting_id),
            "runtime_session_id": str(row.runtime_session_id),
            "base_transcript_revision": row.base_transcript_revision,
            "status": row.status,
            "error_message": row.error_message,
            "evidence": deepcopy(row.evidence_json),
            "created_at": row.created_at.isoformat(),
            "updated_at": row.updated_at.isoformat(),
        }

    def get(self, meeting_id: UUID) -> dict[str, Any] | None:
        with self._sessions() as session:
            row = session.scalar(
                select(MinutesAnalysisRecord)
                .where(MinutesAnalysisRecord.meeting_id == meeting_id)
                .order_by(MinutesAnalysisRecord.updated_at.desc())
            )
            return self._item(row) if row else None

    def create_or_get(self, meeting_id: UUID, runtime_session_id: UUID, base_transcript_revision: int, evidence: dict[str, Any]) -> tuple[dict[str, Any], bool]:
        with self._sessions.begin() as session:
            existing = session.scalar(
                select(MinutesAnalysisRecord).where(
                    MinutesAnalysisRecord.meeting_id == meeting_id,
                    MinutesAnalysisRecord.base_transcript_revision == base_transcript_revision,
                )
            )
            if existing:
                return self._item(existing), False
            row = MinutesAnalysisRecord(
                id=UUID(str(evidence["analysis_id"])),
                meeting_id=meeting_id,
                runtime_session_id=runtime_session_id,
                base_transcript_revision=base_transcript_revision,
                evidence_json=deepcopy(evidence),
                status=ANALYSIS_PENDING,
            )
            session.add(row)
            session.flush()
            return self._item(row), True

    def set_status(self, analysis_id: UUID, status: str, error_message: str | None = None) -> dict[str, Any]:
        with self._sessions.begin() as session:
            row = session.get(MinutesAnalysisRecord, analysis_id)
            if row is None:
                raise LookupError("minutes analysis not found")
            row.status = status
            row.error_message = error_message
            session.flush()
            return self._item(row)

    def delete_meeting(self, meeting_id: UUID) -> int:
        with self._sessions.begin() as session:
            rows = session.scalars(
                select(MinutesAnalysisRecord).where(MinutesAnalysisRecord.meeting_id == meeting_id)
            ).all()
            for row in rows:
                session.delete(row)
            return len(rows)


class MinutesAnalysisService:
    def __init__(self, repository: MinutesAnalysisRepository, ai_client: Any | None = None) -> None:
        self.repository = repository
        self.ai_client = ai_client

    @staticmethod
    def build_evidence(
        *,
        meeting_id: UUID,
        runtime_session_id: UUID,
        meeting_snapshot: dict[str, Any],
        transcript: list[dict[str, Any]],
        previous_document: dict[str, Any] | None,
    ) -> tuple[int, dict[str, Any]]:
        segments = []
        for item in transcript:
            content = str(item.get("content_text") or item.get("text") or "").strip()
            segment_id = str(item.get("segment_id") or "").strip()
            if not segment_id or not content:
                continue
            speaker = item.get("speaker") if isinstance(item.get("speaker"), dict) else {}
            segments.append(
                {
                    "segment_id": segment_id,
                    "speaker_user_id": speaker.get("ecabinet_user_id") or speaker.get("user_id"),
                    "speaker_label": str(speaker.get("label") or item.get("speaker_name") or "Không xác định"),
                    "content_text": content,
                    "started_at": str(item.get("started_at") or item.get("start_time") or ""),
                    "ended_at": str(item.get("ended_at") or item.get("end_time") or ""),
                }
            )
        if not segments:
            raise MinutesAnalysisConflict("cannot analyze minutes without final transcript")
        # ``base_transcript_revision`` is an opaque, deterministic fingerprint
        # of the persisted final-segment state.  A max(segment revision) is
        # insufficient: adding a second revision-1 segment could otherwise
        # collide with editing a single segment to revision 2.  P1-03 compares
        # this snapshot token rather than relying on numeric ordering.
        revision_input = [
            {
                "segment_id": item["segment_id"],
                "content_text": item["content_text"],
                "started_at": item["started_at"],
                "ended_at": item["ended_at"],
            }
            for item in segments
        ]
        revision_bytes = json.dumps(
            revision_input, ensure_ascii=False, separators=(",", ":"), sort_keys=True
        ).encode("utf-8")
        base_revision = int.from_bytes(sha256(revision_bytes).digest()[:8], "big") & ((1 << 63) - 1)
        base_revision = max(1, base_revision)
        meeting = dict(meeting_snapshot.get("meeting") or {})
        evidence = {
            "schema_version": 1,
            "analysis_id": str(uuid4()),
            "meeting_id": str(meeting_id),
            "runtime_session_id": str(runtime_session_id),
            "generation_id": str(uuid4()),
            "meeting": {
                "title": str(meeting.get("title") or "Biên bản cuộc họp"),
                "started_at": meeting.get("started_at"),
            },
            "base_transcript_revision": base_revision,
            "segments": segments,
            "previous_document": deepcopy(previous_document) if previous_document else None,
        }
        return base_revision, evidence

    async def request(self, *, meeting_id: UUID, runtime_session_id: UUID, meeting_snapshot: dict[str, Any], transcript: list[dict[str, Any]], previous_document: dict[str, Any] | None, idempotency_key: str) -> dict[str, Any]:
        base_revision, evidence = self.build_evidence(
            meeting_id=meeting_id,
            runtime_session_id=runtime_session_id,
            meeting_snapshot=meeting_snapshot,
            transcript=transcript,
            previous_document=previous_document,
        )
        record, created = self.repository.create_or_get(
            meeting_id, runtime_session_id, base_revision, evidence
        )
        if not created and record["status"] in {ANALYSIS_PENDING, ANALYSIS_RUNNING, ANALYSIS_SUCCEEDED}:
            return record
        if self.ai_client is None:
            return self.repository.set_status(
                UUID(record["analysis_id"]), ANALYSIS_FAILED, "Meeting AI is not configured"
            )
        try:
            accepted = await self.ai_client.analyze_evidence(
                runtime_session_id,
                record["evidence"],
                idempotency_key,
            )
        except Exception as exc:
            return self.repository.set_status(
                UUID(record["analysis_id"]), ANALYSIS_FAILED, "Meeting AI analysis unavailable"
            )
        if str(accepted.get("status") or "").lower() != "accepted":
            return self.repository.set_status(
                UUID(record["analysis_id"]), ANALYSIS_FAILED, "Meeting AI rejected evidence"
            )
        return self.repository.set_status(UUID(record["analysis_id"]), ANALYSIS_RUNNING)


analysis_store = InMemoryMinutesAnalysisRepository()
