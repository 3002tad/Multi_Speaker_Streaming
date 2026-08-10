from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel, Field

from meeting_service.app.api.socketio import sio
from meeting_service.app.application.meeting_content import content_store
from meeting_service.app.infrastructure.repositories import SqlAlchemyAIEventRepository
from meeting_service.app.api.security import require_service_key


class AIEvent(BaseModel):
    schema_version: int = Field(ge=1)
    event_id: UUID
    type: str
    meeting_id: UUID
    runtime_session_id: UUID
    occurred_at: str
    sequence: int = Field(ge=0)
    payload: dict[str, Any]


router = APIRouter(prefix="/internal/v1")


def _event_dict(event: AIEvent) -> dict[str, Any]:
    # Socket.IO serializes payloads with the stdlib JSON encoder.  Pydantic's
    # model dump keeps UUID instances by default, which makes an otherwise
    # valid AI event fail with a 500 while broadcasting to realtime clients.
    # Use FastAPI's encoder so UUID/datetime values are converted to JSON-safe
    # primitives before both persistence and Socket.IO emission.
    return jsonable_encoder(event)


@router.post("/ai-events", dependencies=[Depends(require_service_key)])
async def receive_ai_event(request: Request, event: AIEvent) -> dict[str, str]:
    repository = getattr(request.app.state, "ai_event_repository", None)
    if event.type == "transcript.final" and not event.payload.get("segment_id"):
        raise HTTPException(status_code=422, detail="transcript.final requires payload.segment_id")
    if repository is None:
        if event.type == "transcript.final":
            getattr(request.app.state, "content_store", content_store).append_transcript(event.meeting_id, event.payload)
        status = "accepted"
    else:
        data = _event_dict(event)
        data = {**data, "event_id": str(event.event_id), "meeting_id": str(event.meeting_id), "runtime_session_id": str(event.runtime_session_id)}
        status = repository.accept(data)
    if status == "accepted":
        await sio.emit(event.type, _event_dict(event), room=f"meeting:{event.meeting_id}")
    return {"status": status}
