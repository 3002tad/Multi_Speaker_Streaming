"""Compatibility wrapper for the legacy LiveKit Agent entrypoint."""

from __future__ import annotations

import asyncio

from meeting_ai.agent.worker import (
    AssignmentCursor,
    EventPublisher,
    assignment_requires_rejoin,
    main,
)

__all__ = ["AssignmentCursor", "EventPublisher", "assignment_requires_rejoin", "main"]


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
