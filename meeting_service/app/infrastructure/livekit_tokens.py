from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import jwt

from meeting_service.app.config import settings


class LiveKitConfigurationError(RuntimeError):
    """Raised when the Meeting Service cannot issue a LiveKit token."""


def issue_livekit_token(
    *,
    room: str,
    identity: str,
    name: str,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if not settings.livekit_url or not settings.livekit_api_key or not settings.livekit_api_secret:
        raise LiveKitConfigurationError("LiveKit token service is not configured")
    if not room or not identity:
        raise ValueError("room and identity are required")

    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(seconds=max(60, settings.livekit_token_ttl_seconds))
    claims: dict[str, Any] = {
        "iss": settings.livekit_api_key,
        "sub": identity,
        "name": name or identity,
        "nbf": int(now.timestamp()),
        "exp": int(expires_at.timestamp()),
        "video": {
            "roomJoin": True,
            "room": room,
            "canPublish": True,
            "canSubscribe": True,
            "canPublishData": True,
        },
    }
    if metadata:
        claims["metadata"] = metadata
    token = jwt.encode(claims, settings.livekit_api_secret, algorithm="HS256")
    return {
        "livekit_url": settings.livekit_url,
        "room": room,
        "identity": identity,
        "token": token,
        "expires_at": expires_at.isoformat(),
    }
