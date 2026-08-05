from __future__ import annotations

from typing import Any


def claims_can_join(claims: dict[str, Any], meeting_id: str) -> bool:
    """Check only signed claims; never query eCabinet from this service."""
    return str(claims.get("meeting_id", "")) == meeting_id and bool(claims.get("sub"))
