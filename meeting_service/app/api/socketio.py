from __future__ import annotations

from typing import Any

import socketio

from meeting_service.app.config import settings
from meeting_service.app.domain.permissions import claims_can_join
from meeting_service.app.infrastructure.token_verifier import RuntimeTokenVerifier


sio = socketio.AsyncServer(
    async_mode="asgi",
    cors_allowed_origins=list(settings.allowed_origins),
    logger=False,
    engineio_logger=False,
)
verifier = RuntimeTokenVerifier()


@sio.event
async def connect(sid: str, environ: dict[str, Any], auth: Any) -> bool:
    try:
        claims = verifier.verify(auth.get("token") if isinstance(auth, dict) else auth)
    except (AttributeError, ValueError):
        return False
    await sio.save_session(sid, {"claims": claims})
    return True


@sio.event
async def disconnect(sid: str) -> None:
    return None


@sio.event
async def join_meeting_room(sid: str, data: dict[str, Any]) -> dict[str, str]:
    meeting_id = str(data.get("meeting_id", ""))
    session = await sio.get_session(sid)
    claims = session.get("claims", {})
    if not claims_can_join(claims, meeting_id):
        raise ConnectionRefusedError("meeting claim does not permit room join")
    room = f"meeting:{meeting_id}"
    await sio.enter_room(sid, room)
    return {"room": room, "status": "joined"}
