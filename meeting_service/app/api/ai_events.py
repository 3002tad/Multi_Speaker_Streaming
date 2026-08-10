from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel, Field

from meeting_service.app.api.socketio import sio
from meeting_service.app.application.meeting_content import content_store
from meeting_service.app.domain.models import RuntimeStatus
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

EVENT_TYPES = {
    "session.status",
    "speaker.active",
    "transcript.partial",
    "transcript.updated",
    "transcript.final",
    "transcript.retracted",
    "minutes.updated",
    "pipeline.warning",
}
TRANSCRIPT_EVENT_TYPES = {
    "transcript.partial",
    "transcript.updated",
    "transcript.final",
    "transcript.retracted",
}


def _event_dict(event: AIEvent) -> dict[str, Any]:
    # Socket.IO serializes payloads with the stdlib JSON encoder.  Pydantic's
    # model dump keeps UUID instances by default, which makes an otherwise
    # valid AI event fail with a 500 while broadcasting to realtime clients.
    # Use FastAPI's encoder so UUID/datetime values are converted to JSON-safe
    # primitives before both persistence and Socket.IO emission.
    return jsonable_encoder(event)


def _validate_payload(event: AIEvent) -> None:
    if event.schema_version != 1:
        raise HTTPException(status_code=422, detail="unsupported event schema_version")
    if event.type not in EVENT_TYPES:
        raise HTTPException(status_code=422, detail="unsupported callback event type")
    try:
        datetime.fromisoformat(event.occurred_at.replace("Z", "+00:00"))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="occurred_at must be ISO-8601") from exc
    if event.type not in TRANSCRIPT_EVENT_TYPES:
        return
    payload = event.payload
    segment_id = payload.get("segment_id")
    if not isinstance(segment_id, str) or not segment_id.strip():
        raise HTTPException(status_code=422, detail=f"{event.type} requires payload.segment_id")
    revision = payload.get("revision")
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 1:
        raise HTTPException(status_code=422, detail=f"{event.type} requires positive payload.revision")
    if event.type == "transcript.retracted":
        if not isinstance(payload.get("reason"), str) or not payload["reason"].strip():
            raise HTTPException(status_code=422, detail="transcript.retracted requires payload.reason")
        return
    if event.type in {"transcript.partial", "transcript.final"}:
        if not isinstance(payload.get("source_identity"), str) or not payload["source_identity"].strip():
            raise HTTPException(status_code=422, detail=f"{event.type} requires payload.source_identity")
        if not isinstance(payload.get("speaker"), dict) or not payload["speaker"]:
            raise HTTPException(status_code=422, detail=f"{event.type} requires payload.speaker")
        speaker = payload["speaker"]
        if not isinstance(speaker.get("label"), str) or not speaker["label"].strip():
            raise HTTPException(status_code=422, detail=f"{event.type} requires speaker.label")
        if speaker.get("identity_method") not in {"voice_profile", "mic_fallback", "unknown"}:
            raise HTTPException(status_code=422, detail=f"{event.type} requires a valid speaker.identity_method")
    if not isinstance(payload.get("content_text"), str):
        raise HTTPException(status_code=422, detail=f"{event.type} requires payload.content_text")
    if event.type == "transcript.final":
        for key in ("raw_text", "started_at", "ended_at"):
            if not isinstance(payload.get(key), str):
                raise HTTPException(status_code=422, detail=f"transcript.final requires payload.{key}")


def _validate_runtime(request: Request, event: AIEvent) -> None:
    runtime_service = getattr(request.app.state, "runtime_service", None)
    if runtime_service is None:
        return
    runtime = runtime_service.runtime(event.runtime_session_id)
    if runtime is None:
        raise HTTPException(status_code=404, detail="callback runtime not found")
    if runtime.meeting_id != event.meeting_id:
        raise HTTPException(status_code=409, detail="callback meeting does not match runtime")
    if runtime.status in {RuntimeStatus.COMPLETED, RuntimeStatus.FAILED}:
        raise HTTPException(status_code=409, detail="callback runtime is no longer active")


@router.post("/ai-events", dependencies=[Depends(require_service_key)])
async def receive_ai_event(request: Request, event: AIEvent) -> dict[str, str]:
    _validate_payload(event)
    repository = getattr(request.app.state, "ai_event_repository", None)
    if repository is not None and repository.has_event(event.event_id):
        return {"status": "duplicate"}
    _validate_runtime(request, event)
    if repository is None:
        fallback_store = getattr(request.app.state, "content_store", content_store)
        if event.type in TRANSCRIPT_EVENT_TYPES and hasattr(fallback_store, "apply_transcript_event"):
            status, _ = fallback_store.apply_transcript_event(event.meeting_id, event.type, event.payload)
        else:
            status = "accepted"
        if status != "accepted":
            return {"status": status}
    else:
        data = _event_dict(event)
        data = {**data, "event_id": str(event.event_id), "meeting_id": str(event.meeting_id), "runtime_session_id": str(event.runtime_session_id)}
        status = repository.accept(data)
    if status == "accepted":
        await sio.emit(event.type, _event_dict(event), room=f"meeting:{event.meeting_id}")
    return {"status": status}
