from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import Any


class TranscriptRepository:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS transcript_segments (
                    segment_id TEXT PRIMARY KEY,
                    meeting_id TEXT NOT NULL,
                    source_id TEXT,
                    speaker_id TEXT,
                    speaker TEXT NOT NULL,
                    raw_text TEXT NOT NULL,
                    text TEXT NOT NULL,
                    start_time REAL NOT NULL,
                    end_time REAL NOT NULL,
                    revision INTEGER NOT NULL DEFAULT 1,
                    created_at REAL NOT NULL,
                    payload_json TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS meeting_minutes (
                    meeting_id TEXT PRIMARY KEY,
                    version INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    updated_at REAL NOT NULL,
                    metadata_json TEXT NOT NULL,
                    document_json TEXT NOT NULL
                )
                """
            )

    def upsert(self, payload: dict[str, Any]) -> None:
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                INSERT INTO transcript_segments (
                    segment_id, meeting_id, source_id, speaker_id, speaker,
                    raw_text, text, start_time, end_time, revision, created_at,
                    payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(segment_id) DO UPDATE SET
                    speaker_id=excluded.speaker_id,
                    speaker=excluded.speaker,
                    raw_text=excluded.raw_text,
                    text=excluded.text,
                    start_time=excluded.start_time,
                    end_time=excluded.end_time,
                    revision=excluded.revision,
                    payload_json=excluded.payload_json
                """,
                (
                    payload["segment_id"],
                    payload["meeting_id"],
                    payload.get("source_id"),
                    payload.get("speaker_id"),
                    payload.get("speaker", "Chưa xác định"),
                    payload.get("raw_text", payload.get("text", "")),
                    payload.get("text", ""),
                    float(payload.get("start_time", 0)),
                    float(payload.get("end_time", 0)),
                    int(payload.get("revision", 1)),
                    float(payload.get("created_at", payload.get("timestamp", 0))),
                    json.dumps(payload, ensure_ascii=False),
                ),
            )

    def list_for_meeting(self, meeting_id: str) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT payload_json
                FROM transcript_segments
                WHERE meeting_id = ?
                ORDER BY start_time ASC
                """,
                (meeting_id,),
            ).fetchall()
        return [json.loads(row["payload_json"]) for row in rows]

    def clear(self, meeting_id: str) -> None:
        with self._lock, self._connect() as connection:
            connection.execute(
                "DELETE FROM transcript_segments WHERE meeting_id = ?",
                (meeting_id,),
            )

    def get_minutes(self, meeting_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT version, status, updated_at, metadata_json, document_json
                FROM meeting_minutes
                WHERE meeting_id = ?
                """,
                (meeting_id,),
            ).fetchone()
        if row is None:
            return None
        return {
            "meeting_id": meeting_id,
            "version": int(row["version"]),
            "status": row["status"],
            "updated_at": float(row["updated_at"]),
            "metadata": json.loads(row["metadata_json"]),
            "document": json.loads(row["document_json"]),
        }

    def upsert_minutes(
        self,
        meeting_id: str,
        *,
        document: dict[str, Any],
        status: str,
        metadata: dict[str, Any] | None = None,
        updated_at: float,
    ) -> dict[str, Any]:
        """Store a new immutable-version view of the current minutes.

        SQLite keeps only the current document for this demo.  ``version`` is
        still incremented so clients can reject an older websocket update.
        """
        with self._lock, self._connect() as connection:
            existing = connection.execute(
                "SELECT version FROM meeting_minutes WHERE meeting_id = ?",
                (meeting_id,),
            ).fetchone()
            version = (int(existing["version"]) if existing else 0) + 1
            metadata_json = json.dumps(metadata or {}, ensure_ascii=False)
            document_json = json.dumps(document, ensure_ascii=False)
            connection.execute(
                """
                INSERT INTO meeting_minutes (
                    meeting_id, version, status, updated_at, metadata_json,
                    document_json
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(meeting_id) DO UPDATE SET
                    version=excluded.version,
                    status=excluded.status,
                    updated_at=excluded.updated_at,
                    metadata_json=excluded.metadata_json,
                    document_json=excluded.document_json
                """,
                (
                    meeting_id,
                    version,
                    status,
                    updated_at,
                    metadata_json,
                    document_json,
                ),
            )
        return {
            "meeting_id": meeting_id,
            "version": version,
            "status": status,
            "updated_at": updated_at,
            "metadata": metadata or {},
            "document": document,
        }

    def clear_minutes(self, meeting_id: str) -> None:
        with self._lock, self._connect() as connection:
            connection.execute(
                "DELETE FROM meeting_minutes WHERE meeting_id = ?",
                (meeting_id,),
            )

    def delete(self, meeting_id: str, segment_id: str) -> None:
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                DELETE FROM transcript_segments
                WHERE meeting_id = ? AND segment_id = ?
                """,
                (meeting_id, segment_id),
            )
