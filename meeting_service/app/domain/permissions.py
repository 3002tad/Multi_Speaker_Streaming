from __future__ import annotations

from typing import Any


def claims_can_join(claims: dict[str, Any], meeting_id: str) -> bool:
    """Check only signed claims; never query eCabinet from this service."""
    if str(claims.get("meeting_id", "")) != meeting_id or not claims.get("sub"):
        return False
    permissions = claims.get("permissions")
    if permissions is not None and "JOIN" not in {str(item).upper() for item in permissions}:
        return False
    return True


def claims_match_runtime(claims: dict[str, Any], runtime_session_id: str) -> bool:
    """Bind a room join to the short-lived runtime assignment when present."""
    claimed_runtime = claims.get("runtime_session_id")
    return not claimed_runtime or str(claimed_runtime) == str(runtime_session_id)
