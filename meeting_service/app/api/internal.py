from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Body, HTTPException, Request

from meeting_service.app.application.runtime_service import RuntimeService, RuntimeStateError
from meeting_service.app.domain.models import RuntimeStatus
from meeting_service.app.application.meeting_content import MinutesRevisionConflict, MinutesStateConflict, content_store
from meeting_service.app.infrastructure.livekit_tokens import LiveKitConfigurationError, issue_livekit_token


router = APIRouter(prefix="/internal/v1")
runtime_service = RuntimeService()


def _service(request: Request) -> RuntimeService:
    return getattr(request.app.state, "runtime_service", runtime_service)


def _content(request: Request):
    return getattr(request.app.state, "content_store", content_store)


@router.delete("/meetings/{meeting_id}")
def purge_meeting(meeting_id: UUID, request: Request) -> dict[str, object]:
    runtime_deleted = _service(request).purge(meeting_id)
    content_deleted = _content(request).delete_meeting(meeting_id)
    ai_repository = getattr(request.app.state, "ai_event_repository", None)
    ai_deleted = ai_repository.delete_meeting(meeting_id) if ai_repository else 0
    return {
        "meeting_id": str(meeting_id),
        "status": "PURGED",
        "runtime_rows_deleted": runtime_deleted,
        "content_rows_deleted": content_deleted,
        "ai_event_rows_deleted": ai_deleted,
    }


@router.post("/meetings/{meeting_id}/runtime", status_code=201)
async def create_runtime(meeting_id: UUID, request: Request, snapshot: dict | None = None) -> dict[str, object]:
    try:
        return (await _service(request).start(meeting_id, snapshot)).as_dict()
    except RuntimeStateError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/meetings/{meeting_id}/status")
def runtime_status(meeting_id: UUID, request: Request) -> dict[str, object]:
    session = _service(request).status(meeting_id)
    if session is None:
        raise HTTPException(status_code=404, detail="runtime not found")
    return session.as_dict()


@router.post("/runtimes/{runtime_session_id}/stop")
async def stop_runtime(runtime_session_id: UUID, request: Request) -> dict[str, object]:
    session = await _service(request).stop(runtime_session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="runtime not found")
    return session.as_dict()


def _issue_livekit_token(
    request: Request,
    meeting_uuid: UUID,
    runtime_session_id: UUID,
    *,
    identity: str,
    name: str,
    metadata: dict[str, object] | None = None,
) -> dict[str, object]:
    session = _service(request).status(meeting_uuid)
    if session is None or session.runtime_session_id != runtime_session_id:
        raise HTTPException(status_code=404, detail="runtime not found")
    if session.status in {RuntimeStatus.COMPLETED, RuntimeStatus.FAILED}:
        raise HTTPException(status_code=409, detail="runtime is no longer active")
    try:
        result = issue_livekit_token(
            room=session.livekit_room,
            identity=identity,
            name=name,
            metadata=metadata,
        )
        result["runtime_session_id"] = str(runtime_session_id)
        return result
    except LiveKitConfigurationError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.post("/meetings/{meeting_id}/tokens")
def meeting_livekit_token(meeting_id: UUID, request: Request, payload: dict[str, object] = Body(...)) -> dict[str, object]:
    """Issue the contract token using external user/device identifiers."""
    try:
        runtime_session_id = UUID(str(payload.get("runtime_session_id")))
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail="runtime_session_id must be a UUID") from exc
    user_id = str(payload.get("user_id") or "")
    device_id = str(payload.get("device_id") or "")
    if not user_id or not device_id:
        raise HTTPException(status_code=422, detail="user_id and device_id are required")
    identity = f"user:{user_id}:device:{device_id}"
    return _issue_livekit_token(
        request,
        meeting_id,
        runtime_session_id,
        identity=identity,
        name=user_id,
        metadata={"user_id": user_id, "device_id": device_id},
    )


@router.post("/runtimes/{runtime_session_id}/livekit-token", include_in_schema=False)
def legacy_livekit_token(runtime_session_id: UUID, request: Request, payload: dict[str, object] = Body(...)) -> dict[str, object]:
    """Compatibility alias for pre-contract eCabinet builds."""
    meeting_id = payload.get("meeting_id")
    try:
        meeting_uuid = UUID(str(meeting_id)) if meeting_id else None
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="meeting_id must be a UUID") from exc
    if meeting_uuid is None:
        raise HTTPException(status_code=422, detail="meeting_id must be a UUID")
    identity = str(payload.get("identity") or "")
    if not identity:
        raise HTTPException(status_code=422, detail="identity is required")
    return _issue_livekit_token(
        request,
        meeting_uuid,
        runtime_session_id,
        identity=identity,
        name=str(payload.get("name") or identity),
        metadata=payload.get("metadata") if isinstance(payload.get("metadata"), dict) else None,
    )


@router.get("/meetings/{meeting_id}/transcript")
def get_transcript(meeting_id: UUID, request: Request) -> dict[str, object]:
    return {"meeting_id": str(meeting_id), "segments": _content(request).transcript(meeting_id)}


@router.post("/meetings/{meeting_id}/transcript", status_code=201)
def append_transcript(meeting_id: UUID, request: Request, segment: dict[str, object] = Body(...)) -> dict[str, object]:
    return _content(request).append_transcript(meeting_id, segment)


@router.get("/meetings/{meeting_id}/minutes")
def get_minutes(meeting_id: UUID, request: Request) -> dict[str, object]:
    return _content(request).minutes(meeting_id)


@router.patch("/meetings/{meeting_id}/minutes")
@router.put("/meetings/{meeting_id}/minutes")
def update_minutes(meeting_id: UUID, request: Request, payload: dict[str, object] = Body(...)) -> dict[str, object]:
    document = payload.get("document")
    if not isinstance(document, dict):
        raise HTTPException(status_code=422, detail="document phải là object")
    status = payload.get("status")
    if status is not None and status not in {"DRAFT", "REVIEWING", "APPROVED"}:
        raise HTTPException(status_code=422, detail="status không hợp lệ")
    base_revision = payload.get("base_revision")
    if base_revision is not None and (isinstance(base_revision, bool) or not isinstance(base_revision, int) or base_revision < 0):
        raise HTTPException(status_code=422, detail="base_revision không hợp lệ")
    if status == "APPROVED":
        runtime = _service(request).status(meeting_id)
        if runtime is None or runtime.status.value != "COMPLETED":
            raise HTTPException(status_code=409, detail="Minutes can only be approved after the runtime has completed")
    try:
        return _content(request).save_minutes(meeting_id, document, str(status) if status else None, base_revision)
    except MinutesRevisionConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


def _transition_minutes(meeting_id: UUID, request: Request, target_status: str) -> dict[str, object]:
    runtime = _service(request).status(meeting_id)
    if runtime is None or runtime.status != RuntimeStatus.COMPLETED:
        raise HTTPException(status_code=409, detail="Minutes can only transition after the runtime has completed")
    try:
        return _content(request).transition_minutes(meeting_id, target_status)
    except MinutesStateConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/meetings/{meeting_id}/minutes/review")
def review_minutes(meeting_id: UUID, request: Request) -> dict[str, object]:
    return _transition_minutes(meeting_id, request, "REVIEWING")


@router.post("/meetings/{meeting_id}/minutes/approve")
def approve_minutes(meeting_id: UUID, request: Request) -> dict[str, object]:
    return _transition_minutes(meeting_id, request, "APPROVED")
