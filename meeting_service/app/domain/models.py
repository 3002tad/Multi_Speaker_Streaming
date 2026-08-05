from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from uuid import UUID, uuid4


class RuntimeStatus(StrEnum):
    STARTING = "STARTING"
    READY = "READY"
    RECORDING = "RECORDING"
    STOPPING = "STOPPING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


@dataclass
class RuntimeSession:
    meeting_id: UUID
    runtime_session_id: UUID = field(default_factory=uuid4)
    status: RuntimeStatus = RuntimeStatus.STARTING
    livekit_room: str = ""
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def as_dict(self) -> dict[str, str]:
        return {
            "meeting_id": str(self.meeting_id),
            "runtime_session_id": str(self.runtime_session_id),
            "status": self.status.value,
            "livekit_room": self.livekit_room,
            "created_at": self.created_at.isoformat(),
        }
