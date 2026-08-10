from __future__ import annotations

import json
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
    can_publish: bool = False,
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
            "canPublish": bool(can_publish),
            "canSubscribe": True,
            "canPublishData": True,
        },
    }
    if metadata:
        # LiveKit's JWT metadata claim is a string. Passing the Python dict
        # through produces a token that signs correctly but is rejected by
        # the Go server during room connect ("cannot unmarshal object into
        # string"). Keep the external API typed while encoding the claim at
        # the boundary.
        claims["metadata"] = json.dumps(
            metadata, ensure_ascii=False, separators=(",", ":")
        )
    token = jwt.encode(claims, settings.livekit_api_secret, algorithm="HS256")
    return {
        "livekit_url": settings.livekit_url,
        "room": room,
        "identity": identity,
        "can_publish": bool(can_publish),
        "token": token,
        "expires_at": expires_at.isoformat(),
    }
