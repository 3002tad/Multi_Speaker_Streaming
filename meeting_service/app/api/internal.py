from __future__ import annotations

import hashlib
from uuid import UUID

from fastapi import APIRouter, Body, Depends, File, Form, HTTPException, Request, Response, UploadFile

from meeting_service.app.application.runtime_service import RuntimeService, RuntimeStateError
from meeting_service.app.domain.models import RuntimeStatus
from meeting_service.app.application.meeting_content import MinutesRevisionConflict, MinutesStateConflict, content_store
from meeting_service.app.application.minutes_analysis import (
    MinutesAnalysisConflict,
    MinutesAnalysisService,
)
from meeting_service.app.infrastructure.livekit_tokens import LiveKitConfigurationError, issue_livekit_token
from meeting_service.app.application.docx_export import CONTENT_TYPE, render_minutes_docx
from meeting_service.app.infrastructure.object_storage import object_storage
from meeting_service.app.api.security import require_service_key


router = APIRouter(prefix="/internal/v1", dependencies=[Depends(require_service_key)])
runtime_service = RuntimeService()


def _service(request: Request) -> RuntimeService:
    return getattr(request.app.state, "runtime_service", runtime_service)


def _content(request: Request):
    return getattr(request.app.state, "content_store", content_store)


def _storage(request: Request):
    return getattr(request.app.state, "object_storage", object_storage)


def _purge_tombstones(request: Request):
    return request.app.state.purge_tombstones


def _ai_client(request: Request):
    client = getattr(request.app.state, "ai_client", None)
    if client is None:
        raise HTTPException(status_code=503, detail="Meeting AI is not configured")
    return client


def _minutes_analysis(request: Request) -> MinutesAnalysisService:
    return request.app.state.minutes_analysis


def _public_analysis(item: dict[str, object]) -> dict[str, object]:
    """Never expose the immutable evidence body outside service-to-AI traffic."""
    return {key: value for key, value in item.items() if key != "evidence"}


def _idempotency_key(request: Request) -> str:
    key = (request.headers.get("Idempotency-Key") or "").strip()
    if not key or len(key) < 8 or len(key) > 160:
        raise HTTPException(status_code=422, detail="Idempotency-Key must be 8-160 characters")
    return key


@router.post("/enrollments/{user_id}")
async def create_enrollment(
    user_id: str,
    request: Request,
    display_name: str = Form(...),
    audio: UploadFile = File(...),
) -> dict[str, object]:
    content = await audio.read()
    if not content:
        raise HTTPException(status_code=422, detail="audio is empty")
    if len(content) > 25 * 1024 * 1024:
        raise HTTPException(status_code=422, detail="audio is too large")
    try:
        return await _ai_client(request).create_enrollment(
            user_id=user_id,
            display_name=display_name.strip(),
            audio=content,
            filename=audio.filename or "enrollment.wav",
        )
    except HTTPException:
        raise
    except Exception as exc:
        status_code = getattr(getattr(exc, "response", None), "status_code", 502)
        if status_code in {404, 422, 503}:
            raise HTTPException(status_code=status_code, detail="Enrollment failed") from exc
        raise HTTPException(status_code=502, detail="Meeting AI enrollment unavailable") from exc


@router.get("/enrollments/{user_id}")
async def get_enrollment(user_id: str, request: Request) -> dict[str, object]:
    try:
        return await _ai_client(request).get_enrollment(user_id)
    except Exception as exc:
        status_code = getattr(getattr(exc, "response", None), "status_code", 502)
        if status_code == 404:
            raise HTTPException(status_code=404, detail="voice profile not found") from exc
        raise HTTPException(status_code=502, detail="Meeting AI enrollment unavailable") from exc


@router.delete("/enrollments/{user_id}", status_code=204, response_class=Response)
async def delete_enrollment(user_id: str, request: Request) -> Response:
    try:
        await _ai_client(request).delete_enrollment(user_id)
    except Exception as exc:
        status_code = getattr(getattr(exc, "response", None), "status_code", 502)
        if status_code not in {404, 204}:
            raise HTTPException(status_code=502, detail="Meeting AI enrollment unavailable") from exc
    return Response(status_code=204)


@router.delete("/meetings/{meeting_id}")
def purge_meeting(meeting_id: UUID, request: Request) -> dict[str, object]:
    try:
        runtime_deleted = _service(request).purge(meeting_id, _idempotency_key(request))
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    exports = _content(request).list_exports(meeting_id)
    tombstone = _purge_tombstones(request).prepare(
        meeting_id,
        [str(export["storage_key"]) for export in exports],
    )
    pending_storage_keys: list[str] = []
    export_storage_deleted = 0
    export_storage_cleanup_failed = 0
    for storage_key in list(tombstone.get("pending_storage_keys") or []):
        try:
            _storage(request).delete(storage_key)
            export_storage_deleted += 1
        except Exception:
            # Keep the key in the durable tombstone so a later purge retry can
            # remove the object after a transient MinIO outage.
            pending_storage_keys.append(storage_key)
            export_storage_cleanup_failed += 1
    tombstone = _purge_tombstones(request).record_attempt(
        meeting_id,
        pending_storage_keys,
        "object storage cleanup failed" if pending_storage_keys else None,
    )
    content_deleted = _content(request).delete_meeting(meeting_id)
    ai_repository = getattr(request.app.state, "ai_event_repository", None)
    ai_deleted = ai_repository.delete_meeting(meeting_id) if ai_repository else 0
    analysis_deleted = _minutes_analysis(request).repository.delete_meeting(meeting_id)
    return {
        "meeting_id": str(meeting_id),
        "status": "PURGED",
        "runtime_rows_deleted": runtime_deleted,
        "content_rows_deleted": content_deleted,
        "ai_event_rows_deleted": ai_deleted,
        "minutes_analysis_rows_deleted": analysis_deleted,
        "export_objects_deleted": export_storage_deleted,
        "export_objects_cleanup_failed": export_storage_cleanup_failed,
        "purge_cleanup_status": tombstone["status"],
        "purge_cleanup_attempts": tombstone["attempts"],
        "pending_storage_objects": len(tombstone.get("pending_storage_keys") or []),
    }


@router.post("/meetings/{meeting_id}/runtime", status_code=201)
async def create_runtime(meeting_id: UUID, request: Request, snapshot: dict | None = None) -> dict[str, object]:
    try:
        return (await _service(request).start(meeting_id, snapshot, _idempotency_key(request))).as_dict()
    except RuntimeStateError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/meetings/{meeting_id}/status")
def runtime_status(meeting_id: UUID, request: Request) -> dict[str, object]:
    session = _service(request).status(meeting_id)
    if session is None:
        raise HTTPException(status_code=404, detail="runtime not found")
    return session.as_dict()


@router.put("/meetings/{meeting_id}/snapshot")
def update_runtime_snapshot(meeting_id: UUID, request: Request, snapshot: dict[str, object] = Body(...)) -> dict[str, object]:
    revision = snapshot.get("snapshot_revision")
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 1:
        raise HTTPException(status_code=422, detail="snapshot_revision must be a positive integer")
    try:
        return _service(request).update_snapshot(meeting_id, snapshot)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="runtime not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/runtimes/{runtime_session_id}/stop")
async def stop_runtime(runtime_session_id: UUID, request: Request) -> dict[str, object]:
    try:
        session = await _service(request).stop(runtime_session_id, _idempotency_key(request))
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
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
            can_publish="PUBLISH_AUDIO" in {
                str(item).upper() for item in (metadata or {}).get("permissions", [])
            },
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
    display_name = str(payload.get("display_name") or user_id).strip()
    device_id = str(payload.get("device_id") or "")
    if not user_id or not device_id:
        raise HTTPException(status_code=422, detail="user_id and device_id are required")
    identity = f"user:{user_id}:device:{device_id}"
    return _issue_livekit_token(
        request,
        meeting_id,
        runtime_session_id,
        identity=identity,
        name=display_name or user_id,
        metadata={
            "user_id": user_id,
            "device_id": device_id,
            "permissions": payload.get("permissions", []),
        },
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


@router.get("/meetings/{meeting_id}/minutes/analyze")
def minutes_analysis_status(meeting_id: UUID, request: Request) -> dict[str, object]:
    item = _minutes_analysis(request).repository.get(meeting_id)
    if item is None:
        return {"meeting_id": str(meeting_id), "status": "IDLE"}
    return _public_analysis(item)


@router.post("/meetings/{meeting_id}/minutes/analyze", status_code=202)
async def analyze_minutes(meeting_id: UUID, request: Request) -> dict[str, object]:
    runtime = _service(request).status(meeting_id)
    if runtime is None:
        raise HTTPException(status_code=404, detail="runtime not found")
    if runtime.status in {RuntimeStatus.COMPLETED, RuntimeStatus.FAILED}:
        raise HTTPException(status_code=409, detail="runtime is no longer active")
    try:
        current_minutes = _content(request).minutes(meeting_id)
        result = await _minutes_analysis(request).request(
            meeting_id=meeting_id,
            runtime_session_id=runtime.runtime_session_id,
            meeting_snapshot=_service(request).snapshot(meeting_id),
            transcript=_content(request).transcript(meeting_id),
            previous_document=current_minutes.get("document"),
            idempotency_key=_idempotency_key(request),
            previous_revision=int(current_minutes.get("revision", 0)),
        )
        return _public_analysis(result)
    except MinutesAnalysisConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


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
    except (MinutesRevisionConflict, MinutesStateConflict) as exc:
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


@router.post("/meetings/{meeting_id}/minutes/exports/docx", status_code=201)
def export_minutes_docx(meeting_id: UUID, request: Request, payload: dict[str, object] | None = Body(default=None)) -> dict[str, object]:
    payload = payload or {}
    minutes = _content(request).minutes(meeting_id)
    revision = int(payload.get("revision") or minutes.get("revision", 0))
    if revision != int(minutes.get("revision", 0)):
        raise HTTPException(status_code=404, detail="minutes revision not found")
    status = str(minutes.get("status", "DRAFT"))
    if status != "APPROVED" and not bool(payload.get("allow_draft")):
        raise HTTPException(status_code=403, detail="draft export is not allowed")
    export_format = "docx"
    existing = _content(request).find_export(meeting_id, revision, export_format)
    if existing:
        return existing
    official = status == "APPROVED"
    content = render_minutes_docx(minutes.get("document") or {}, official=official)
    filename = f"Bien_ban_{meeting_id}_{'CHINH_THUC' if official else 'DU_THAO'}_v{revision}.docx"
    storage_key = f"meeting-minutes/{meeting_id}/{revision}.docx"
    metadata = {
        "meeting_id": str(meeting_id),
        "minutes_revision": revision,
        "minutes_status": status,
        "format": export_format,
        "storage_key": storage_key,
        "filename": filename,
        "content_type": CONTENT_TYPE,
        "size_bytes": len(content),
        "checksum": hashlib.sha256(content).hexdigest(),
        "created_by": str(payload.get("created_by")) if payload.get("created_by") else None,
    }
    try:
        _storage(request).put(storage_key, content, CONTENT_TYPE)
        try:
            return _content(request).create_export(metadata)
        except Exception:
            _storage(request).delete(storage_key)
            raise
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=502, detail="minutes export storage unavailable") from exc


@router.get("/meetings/{meeting_id}/minutes/exports/{export_id}")
def download_minutes_export(meeting_id: UUID, export_id: UUID, request: Request) -> Response:
    metadata = _content(request).get_export(meeting_id, export_id)
    if metadata is None:
        raise HTTPException(status_code=404, detail="export not found")
    try:
        content = _storage(request).get(metadata["storage_key"])
    except Exception as exc:
        raise HTTPException(status_code=502, detail="minutes export storage unavailable") from exc
    return Response(
        content=content,
        media_type=metadata["content_type"],
        headers={"Content-Disposition": f'attachment; filename="{metadata["filename"]}"', "X-Meeting-Export-Id": str(metadata["id"])},
    )


@router.get("/meetings/{meeting_id}/minutes/exports/{export_id}/metadata")
def minutes_export_metadata(meeting_id: UUID, export_id: UUID, request: Request) -> dict[str, object]:
    metadata = _content(request).get_export(meeting_id, export_id)
    if metadata is None:
        raise HTTPException(status_code=404, detail="export not found")
    return metadata
