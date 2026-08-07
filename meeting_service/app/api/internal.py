from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Body, HTTPException, Request

from meeting_service.app.application.runtime_service import RuntimeService
from meeting_service.app.application.meeting_content import content_store


router = APIRouter(prefix="/internal/v1")
runtime_service = RuntimeService()


def _service(request: Request) -> RuntimeService:
    return getattr(request.app.state, "runtime_service", runtime_service)


def _content(request: Request):
    return getattr(request.app.state, "content_store", content_store)


@router.post("/meetings/{meeting_id}/runtime", status_code=201)
async def create_runtime(meeting_id: UUID, request: Request, snapshot: dict | None = None) -> dict[str, object]:
    return (await _service(request).start(meeting_id, snapshot)).as_dict()


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


@router.get("/meetings/{meeting_id}/transcript")
def get_transcript(meeting_id: UUID, request: Request) -> dict[str, object]:
    return {"meeting_id": str(meeting_id), "segments": _content(request).transcript(meeting_id)}


@router.post("/meetings/{meeting_id}/transcript", status_code=201)
def append_transcript(meeting_id: UUID, request: Request, segment: dict[str, object] = Body(...)) -> dict[str, object]:
    return _content(request).append_transcript(meeting_id, segment)


@router.get("/meetings/{meeting_id}/minutes")
def get_minutes(meeting_id: UUID, request: Request) -> dict[str, object]:
    return _content(request).minutes(meeting_id)


@router.put("/meetings/{meeting_id}/minutes")
def update_minutes(meeting_id: UUID, request: Request, payload: dict[str, object] = Body(...)) -> dict[str, object]:
    document = payload.get("document")
    if not isinstance(document, dict):
        raise HTTPException(status_code=422, detail="document phải là object")
    status = payload.get("status")
    if status is not None and status not in {"DRAFT", "REVIEWING", "APPROVED"}:
        raise HTTPException(status_code=422, detail="status không hợp lệ")
    return _content(request).save_minutes(meeting_id, document, str(status) if status else None)
