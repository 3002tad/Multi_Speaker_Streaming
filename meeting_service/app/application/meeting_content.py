from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from threading import RLock
from typing import Any
from uuid import UUID


class MeetingContentStore:
    """Small runtime store for the first vertical slice.

    Persistence is intentionally behind this interface; the next migration can
    replace the maps with Meeting Service-owned transcript/minutes repositories
    without changing the REST contract.
    """

    def __init__(self) -> None:
        self._lock = RLock()
        self._transcripts: dict[UUID, list[dict[str, Any]]] = {}
        self._minutes: dict[UUID, dict[str, Any]] = {}

    def transcript(self, meeting_id: UUID) -> list[dict[str, Any]]:
        with self._lock:
            return deepcopy(self._transcripts.get(meeting_id, []))

    def append_transcript(self, meeting_id: UUID, segment: dict[str, Any]) -> dict[str, Any]:
        item = dict(segment)
        item.setdefault("segment_id", f"segment-{len(self._transcripts.get(meeting_id, [])) + 1}")
        item.setdefault("created_at", datetime.now(timezone.utc).isoformat())
        with self._lock:
            self._transcripts.setdefault(meeting_id, []).append(item)
            return deepcopy(item)

    def minutes(self, meeting_id: UUID) -> dict[str, Any]:
        with self._lock:
            return deepcopy(self._minutes.get(meeting_id) or {
                "meeting_id": str(meeting_id),
                "revision": 0,
                "status": "DRAFT",
                "document": {
                    "title": "Biên bản cuộc họp",
                    "summary": "",
                    "topics": [],
                    "decisions": [],
                    "actions": [],
                },
                "source_segment_ids": [],
            })

    def save_minutes(self, meeting_id: UUID, document: dict[str, Any], status: str | None = None) -> dict[str, Any]:
        with self._lock:
            previous = self._minutes.get(meeting_id) or self.minutes(meeting_id)
            revision = int(previous.get("revision", 0)) + 1
            item = {
                "meeting_id": str(meeting_id),
                "revision": revision,
                "status": status or previous.get("status", "DRAFT"),
                "document": deepcopy(document),
                "source_segment_ids": [str(x.get("segment_id")) for x in self._transcripts.get(meeting_id, [])],
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
            self._minutes[meeting_id] = item
            return deepcopy(item)


content_store = MeetingContentStore()
