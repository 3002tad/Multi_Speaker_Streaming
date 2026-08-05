from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class RealtimeEvent:
    """Event envelope emitted to a meeting room.

    Persistence and sequence assignment will be added with the Meeting Service
    database slice; this skeleton only defines the boundary.
    """

    event_type: str
    payload: dict[str, Any]
    sequence: int | None = None
