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


def _content(request: Request):
    return getattr(request.app.state, "content_store", content_store)

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
        if event.type == "minutes.updated":
            payload = event.payload
            if not isinstance(payload.get("analysis_id"), str) or not payload["analysis_id"].strip():
                raise HTTPException(status_code=422, detail="minutes.updated requires analysis_id")
            if not isinstance(payload.get("generation_id"), str) or not payload["generation_id"].strip():
                raise HTTPException(status_code=422, detail="minutes.updated requires generation_id")
            revision = payload.get("base_transcript_revision")
            if isinstance(revision, bool) or not isinstance(revision, int) or revision < 1:
                raise HTTPException(status_code=422, detail="minutes.updated requires base_transcript_revision")
            if not isinstance(payload.get("document"), dict):
                raise HTTPException(status_code=422, detail="minutes.updated requires document")
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


def _source_ids(value: object, known_ids: set[str]) -> list[str]:
    if not isinstance(value, list) or not value:
        raise HTTPException(status_code=422, detail="minutes document requires source_segment_ids")
    result: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip() or item in result:
            raise HTTPException(status_code=422, detail="minutes document has invalid source_segment_ids")
        if item not in known_ids:
            raise HTTPException(status_code=409, detail="minutes document cites unknown transcript segment")
        result.append(item)
    return result


def _validate_minutes_document(document: dict[str, object], known_ids: set[str]) -> None:
    """Validate the structured document and enforce transcript grounding."""
    if document.get("schema_version") != 1:
        raise HTTPException(status_code=422, detail="unsupported minutes document schema_version")
    meeting = document.get("meeting")
    if not isinstance(meeting, dict) or not isinstance(meeting.get("title"), str):
        raise HTTPException(status_code=422, detail="minutes document requires meeting metadata")
    started_at = meeting.get("started_at")
    if started_at is not None:
        if not isinstance(started_at, str):
            raise HTTPException(status_code=422, detail="minutes document started_at must be ISO-8601")
        try:
            datetime.fromisoformat(started_at.replace("Z", "+00:00"))
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="minutes document started_at must be ISO-8601") from exc
    summary = document.get("summary")
    topics = document.get("topics")
    if not isinstance(summary, list) or not isinstance(topics, list):
        raise HTTPException(status_code=422, detail="minutes document summary/topics must be arrays")
    document_sources: list[str] = []
    for item in summary:
        if not isinstance(item, dict) or not isinstance(item.get("content"), str) or not item["content"].strip():
            raise HTTPException(status_code=422, detail="minutes summary item is invalid")
        document_sources.extend(_source_ids(item.get("source_segment_ids"), known_ids))
    for topic in topics:
        if not isinstance(topic, dict) or not isinstance(topic.get("title"), str) or not topic["title"].strip():
            raise HTTPException(status_code=422, detail="minutes topic is invalid")
        topic_sources = _source_ids(topic.get("source_segment_ids"), known_ids)
        document_sources.extend(topic_sources)
        for key in ("details", "proposals", "decisions"):
            items = topic.get(key)
            if not isinstance(items, list):
                raise HTTPException(status_code=422, detail=f"minutes topic {key} must be an array")
            for item in items:
                if not isinstance(item, dict) or not isinstance(item.get("content"), str) or not item["content"].strip():
                    raise HTTPException(status_code=422, detail=f"minutes topic {key} item is invalid")
                document_sources.extend(_source_ids(item.get("source_segment_ids"), known_ids))
        actions = topic.get("actions")
        if not isinstance(actions, list):
            raise HTTPException(status_code=422, detail="minutes topic actions must be an array")
        for action in actions:
            if not isinstance(action, dict) or not isinstance(action.get("task"), str) or not action["task"].strip():
                raise HTTPException(status_code=422, detail="minutes action is invalid")
            document_sources.extend(_source_ids(action.get("source_segment_ids"), known_ids))
    top_sources = document.get("source_segment_ids")
    if not isinstance(top_sources, list):
        raise HTTPException(status_code=422, detail="minutes document requires source_segment_ids")
    for source_id in top_sources:
        if not isinstance(source_id, str) or source_id not in known_ids:
            raise HTTPException(status_code=409, detail="minutes document cites unknown transcript segment")


def _validate_minutes_event(request: Request, event: AIEvent) -> None:
    payload = event.payload
    try:
        UUID(str(payload.get("analysis_id")))
        UUID(str(payload.get("generation_id")))
    except (ValueError, TypeError, AttributeError) as exc:
        raise HTTPException(status_code=422, detail="minutes.updated ids must be UUIDs") from exc
    analysis_service = getattr(request.app.state, "minutes_analysis", None)
    if analysis_service is not None:
        analysis = analysis_service.repository.get_by_id(UUID(str(payload["analysis_id"])))
        if analysis is None:
            raise HTTPException(status_code=409, detail="minutes.updated references unknown analysis")
        if str(analysis.get("meeting_id")) != str(event.meeting_id):
            raise HTTPException(status_code=409, detail="minutes.updated analysis meeting mismatch")
        expected_generation = str(analysis.get("generation_id") or "")
        if expected_generation and expected_generation != str(payload["generation_id"]):
            raise HTTPException(status_code=409, detail="minutes.updated generation is stale")
        evidence = analysis.get("evidence") or {}
        if evidence.get("auto_generated") and not evidence.get("auto_update_enabled"):
            raise HTTPException(status_code=409, detail="minutes auto-update is disabled")
        expected_transcript_revision = int(analysis.get("base_transcript_revision") or 0)
        if expected_transcript_revision != int(payload["base_transcript_revision"]):
            raise HTTPException(status_code=409, detail="minutes.updated transcript snapshot is stale")
        supplied_minutes_revision = payload.get("base_minutes_revision")
        expected_minutes_revision = int(analysis.get("base_minutes_revision") or 0)
        if supplied_minutes_revision is not None and int(supplied_minutes_revision) != expected_minutes_revision:
            raise HTTPException(status_code=409, detail="minutes.updated minutes snapshot is stale")
    transcript = _content(request).transcript(event.meeting_id)
    known_ids = {
        str(item.get("segment_id"))
        for item in transcript
        if isinstance(item, dict) and item.get("segment_id")
    }
    if not known_ids:
        raise HTTPException(status_code=409, detail="minutes.updated requires persisted final transcript")
    _validate_minutes_document(payload["document"], known_ids)


def _validate_runtime(request: Request, event: AIEvent) -> None:
    runtime_service = getattr(request.app.state, "runtime_service", None)
    if runtime_service is None:
        return
    runtime = runtime_service.runtime(event.runtime_session_id)
    if runtime is None:
        raise HTTPException(status_code=404, detail="callback runtime not found")
    if runtime.meeting_id != event.meeting_id:
        raise HTTPException(status_code=409, detail="callback meeting does not match runtime")
    if runtime.status == RuntimeStatus.FAILED:
        raise HTTPException(status_code=409, detail="callback runtime has failed")
    if runtime.status == RuntimeStatus.COMPLETED and event.type != "minutes.updated":
        raise HTTPException(status_code=409, detail="callback runtime is no longer active")


@router.post("/ai-events", dependencies=[Depends(require_service_key)])
async def receive_ai_event(request: Request, event: AIEvent) -> dict[str, str]:
    _validate_payload(event)
    if event.type == "minutes.updated":
        _validate_minutes_event(request, event)
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
        if event.type == "minutes.updated":
            current = _content(request).minutes(event.meeting_id)
            analysis_service = getattr(request.app.state, "minutes_analysis", None)
            analysis = (
                analysis_service.repository.get_by_id(UUID(str(event.payload["analysis_id"])))
                if analysis_service is not None
                else None
            )
            expected_revision = int(
                (analysis or {}).get("base_minutes_revision")
                or event.payload.get("base_minutes_revision")
                or 0
            )
            if str(current.get("status") or "DRAFT") == "APPROVED" or int(current.get("revision", 0)) != expected_revision:
                if analysis_service is not None:
                    analysis_service.callback_status(
                        UUID(str(event.payload["analysis_id"])),
                        "STALE",
                        "minutes revision changed before AI callback",
                    )
                return {"status": "stale"}
            try:
                _content(request).save_minutes(
                    event.meeting_id,
                    event.payload["document"],
                    "DRAFT",
                    expected_revision,
                )
            except MinutesRevisionConflict:
                if analysis_service is not None:
                    analysis_service.callback_status(
                        UUID(str(event.payload["analysis_id"])),
                        "STALE",
                        "minutes revision changed before AI callback",
                    )
                return {"status": "stale"}
            if analysis_service is not None:
                analysis_service.callback_status(
                    UUID(str(event.payload["analysis_id"])),
                    "SUCCEEDED",
                )
        elif event.type == "pipeline.warning" and event.payload.get("code") == "MINUTES_COMPOSITION_FAILED":
            details = event.payload.get("details")
            if isinstance(details, dict) and details.get("analysis_id"):
                analysis_service = getattr(request.app.state, "minutes_analysis", None)
                if analysis_service is not None:
                    try:
                        analysis_service.callback_status(
                            UUID(str(details["analysis_id"])),
                            "FAILED",
                            str(event.payload.get("message") or "minutes composition failed"),
                        )
                    except (ValueError, TypeError):
                        pass
        elif event.type in {"transcript.final", "transcript.updated"}:
            # Auto-update is armed only after the first explicit analyze
            # request. The coordinator checks that flag and the DRAFT state,
            # then debounces a snapshot before calling Meeting AI.
            coordinator = getattr(request.app.state, "minutes_auto_update", None)
            if coordinator is not None:
                coordinator.schedule(event.meeting_id)
        await sio.emit(event.type, _event_dict(event), room=f"meeting:{event.meeting_id}")
    return {"status": status}
