from __future__ import annotations

import os
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel, Field

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


@router.post("/ai-events")
def receive_ai_event(request: Request, event: AIEvent, x_internal_api_key: str | None = Header(default=None)) -> dict[str, str]:
    expected = os.getenv("MEETING_SERVICE_KEY", "")
    if expected and x_internal_api_key != expected:
        raise HTTPException(status_code=403, detail="invalid service key")
    repository = getattr(request.app.state, "ai_event_repository", None)
    if repository is None:
        return {"status": "accepted"}
    data = event.dict()
    data = {**data, "event_id": str(event.event_id), "meeting_id": str(event.meeting_id), "runtime_session_id": str(event.runtime_session_id)}
    return {"status": repository.accept(data)}
