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

    def delete(self, meeting_id: str, segment_id: str) -> None:
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                DELETE FROM transcript_segments
                WHERE meeting_id = ? AND segment_id = ?
                """,
                (meeting_id, segment_id),
            )
