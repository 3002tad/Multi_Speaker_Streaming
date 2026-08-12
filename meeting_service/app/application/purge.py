from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from threading import RLock
from typing import Any, Protocol
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from meeting_service.app.infrastructure.models import MeetingPurgeTombstoneRecord


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class PurgeTombstoneStore(Protocol):
    def prepare(self, meeting_id: UUID, storage_keys: list[str]) -> dict[str, Any]: ...
    def record_attempt(self, meeting_id: UUID, pending_keys: list[str], error: str | None) -> dict[str, Any]: ...


class InMemoryPurgeTombstoneStore:
    def __init__(self) -> None:
        self._lock = RLock()
        self._items: dict[UUID, dict[str, Any]] = {}

    def prepare(self, meeting_id: UUID, storage_keys: list[str]) -> dict[str, Any]:
        with self._lock:
            item = self._items.setdefault(
                meeting_id,
                {
                    "meeting_id": str(meeting_id),
                    "status": "PENDING",
                    "pending_storage_keys": [],
                    "attempts": 0,
                    "last_error": None,
                    "created_at": _now(),
                },
            )
            known = set(item["pending_storage_keys"])
            item["pending_storage_keys"] = sorted(known | {str(key) for key in storage_keys if key})
            item["status"] = "PENDING" if item["pending_storage_keys"] else "COMPLETED"
            item["updated_at"] = _now()
            return deepcopy(item)

    def record_attempt(self, meeting_id: UUID, pending_keys: list[str], error: str | None) -> dict[str, Any]:
        with self._lock:
            item = self._items.setdefault(meeting_id, {
                "meeting_id": str(meeting_id), "status": "PENDING", "pending_storage_keys": [],
                "attempts": 0, "last_error": None, "created_at": _now(),
            })
            item["pending_storage_keys"] = sorted({str(key) for key in pending_keys if key})
            item["attempts"] = int(item.get("attempts", 0)) + 1
            item["last_error"] = error
            item["status"] = "PENDING" if item["pending_storage_keys"] else "COMPLETED"
            item["updated_at"] = _now()
            return deepcopy(item)


class SqlAlchemyPurgeTombstoneStore:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._sessions = session_factory

    @staticmethod
    def _item(row: MeetingPurgeTombstoneRecord) -> dict[str, Any]:
        return {
            "meeting_id": str(row.meeting_id),
            "status": row.status,
            "pending_storage_keys": list(row.pending_storage_keys or []),
            "attempts": row.attempts,
            "last_error": row.last_error,
            "created_at": row.created_at.isoformat(),
            "updated_at": row.updated_at.isoformat(),
        }

    def prepare(self, meeting_id: UUID, storage_keys: list[str]) -> dict[str, Any]:
        with self._sessions.begin() as session:
            row = session.get(MeetingPurgeTombstoneRecord, meeting_id)
            if row is None:
                row = MeetingPurgeTombstoneRecord(
                    meeting_id=meeting_id,
                    status="PENDING",
                    pending_storage_keys=[],
                    attempts=0,
                )
                session.add(row)
                session.flush()
            known = set(row.pending_storage_keys or [])
            row.pending_storage_keys = sorted(known | {str(key) for key in storage_keys if key})
            row.status = "PENDING" if row.pending_storage_keys else "COMPLETED"
            session.flush()
            return self._item(row)

    def record_attempt(self, meeting_id: UUID, pending_keys: list[str], error: str | None) -> dict[str, Any]:
        with self._sessions.begin() as session:
            row = session.get(MeetingPurgeTombstoneRecord, meeting_id)
            if row is None:
                row = MeetingPurgeTombstoneRecord(meeting_id=meeting_id, pending_storage_keys=[])
                session.add(row)
                session.flush()
            row.pending_storage_keys = sorted({str(key) for key in pending_keys if key})
            row.attempts = int(row.attempts or 0) + 1
            row.last_error = error
            row.status = "PENDING" if row.pending_storage_keys else "COMPLETED"
            session.flush()
            return self._item(row)


purge_tombstones = InMemoryPurgeTombstoneStore()

