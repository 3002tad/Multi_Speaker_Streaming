from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid4

from sqlalchemy import BigInteger, DateTime, Index, Integer, JSON, String, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import Uuid

from meeting_service.app.infrastructure.database import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class RuntimeSessionRecord(Base):
    __tablename__ = "meeting_runtime_sessions"
    __table_args__ = (
        # One non-terminal runtime per meeting.  The index is enforced by
        # PostgreSQL and SQLite; the repository handles the concurrent insert
        # race by returning the winner row.
        Index(
            "uq_meeting_runtime_active_meeting",
            "meeting_id",
            unique=True,
            postgresql_where=text("status NOT IN ('COMPLETED', 'FAILED')"),
            sqlite_where=text("status NOT IN ('COMPLETED', 'FAILED')"),
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    meeting_id: Mapped[UUID] = mapped_column(Uuid, index=True, nullable=False)
    meeting_snapshot_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    livekit_room: Mapped[str] = mapped_column(String(200), unique=True, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_code: Mapped[str | None] = mapped_column(String(80))
    error_message: Mapped[str | None] = mapped_column(String)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class IdempotencyRecord(Base):
    __tablename__ = "meeting_idempotency_records"
    __table_args__ = (UniqueConstraint("operation", "key", name="uq_meeting_idempotency_operation_key"),)

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    operation: Mapped[str] = mapped_column(String(100), nullable=False)
    key: Mapped[str] = mapped_column(String(255), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    response_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AIEventRecord(Base):
    __tablename__ = "meeting_ai_events"

    event_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    meeting_id: Mapped[UUID] = mapped_column(Uuid, nullable=False, index=True)
    runtime_session_id: Mapped[UUID] = mapped_column(Uuid, nullable=False, index=True)
    event_type: Mapped[str] = mapped_column(String(80), nullable=False)
    # Callback sequences can be epoch-millisecond values from AI workers;
    # they exceed a PostgreSQL 32-bit INTEGER even though they remain small
    # Python ints.
    sequence: Mapped[int] = mapped_column(BigInteger, nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class TranscriptSegmentRecord(Base):
    __tablename__ = "meeting_transcript_segments"
    __table_args__ = (UniqueConstraint("meeting_id", "segment_id", name="uq_meeting_transcript_segment"),)

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    meeting_id: Mapped[UUID] = mapped_column(Uuid, nullable=False, index=True)
    segment_id: Mapped[str] = mapped_column(String(160), nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class MinutesRevisionRecord(Base):
    __tablename__ = "meeting_minutes_revisions"
    __table_args__ = (UniqueConstraint("meeting_id", "revision", name="uq_meeting_minutes_revision"),)

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    meeting_id: Mapped[UUID] = mapped_column(Uuid, nullable=False, index=True)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    document_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    source_segment_ids: Mapped[list] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class MinutesAnalysisRecord(Base):
    """Durable control-plane state for a requested AI minutes analysis."""

    __tablename__ = "meeting_minutes_analyses"
    __table_args__ = (
        UniqueConstraint(
            "meeting_id",
            "base_transcript_revision",
            name="uq_meeting_minutes_analysis_snapshot",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    meeting_id: Mapped[UUID] = mapped_column(Uuid, nullable=False, index=True)
    runtime_session_id: Mapped[UUID] = mapped_column(Uuid, nullable=False, index=True)
    # The revision is a deterministic 63-bit snapshot fingerprint, not a
    # small row counter.  Keep the database type wide enough for the value
    # produced by MinutesAnalysisService.
    base_transcript_revision: Mapped[int] = mapped_column(BigInteger, nullable=False)
    evidence_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    error_message: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class MinutesExportRecord(Base):
    __tablename__ = "meeting_minutes_exports"
    __table_args__ = (UniqueConstraint("meeting_id", "minutes_revision", "format", name="uq_meeting_minutes_export"),)

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    meeting_id: Mapped[UUID] = mapped_column(Uuid, nullable=False, index=True)
    minutes_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    minutes_status: Mapped[str] = mapped_column(String(20), nullable=False)
    format: Mapped[str] = mapped_column(String(20), nullable=False, default="docx")
    storage_key: Mapped[str] = mapped_column(String(500), nullable=False)
    filename: Mapped[str] = mapped_column(String(500), nullable=False)
    content_type: Mapped[str] = mapped_column(String(160), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    checksum: Mapped[str] = mapped_column(String(128), nullable=False)
    created_by: Mapped[str | None] = mapped_column(String(160), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class MeetingPurgeTombstoneRecord(Base):
    """Durable cleanup intent for a meeting purge and object-storage retries."""

    __tablename__ = "meeting_purge_tombstones"

    meeting_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="PENDING")
    pending_storage_keys: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_error: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
