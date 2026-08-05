from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid4

from sqlalchemy import DateTime, Integer, JSON, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import Uuid

from meeting_service.app.infrastructure.database import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class RuntimeSessionRecord(Base):
    __tablename__ = "meeting_runtime_sessions"

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
    response_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
