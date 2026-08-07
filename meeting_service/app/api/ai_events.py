from __future__ import annotations

import os
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel, Field

from meeting_service.app.api.socketio import sio
from meeting_service.app.application.meeting_content import content_store
from meeting_service.app.infrastructure.repositories import SqlAlchemyAIEventRepository


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
    model_dump = getattr(event, "model_dump", None)
    return model_dump() if model_dump else event.dict()


@router.post("/ai-events")
async def receive_ai_event(request: Request, event: AIEvent, x_internal_api_key: str | None = Header(default=None)) -> dict[str, str]:
    expected = os.getenv("MEETING_SERVICE_KEY", "")
    if expected and x_internal_api_key != expected:
        raise HTTPException(status_code=403, detail="invalid service key")
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
