from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, HTTPException

from meeting_service.app.application.runtime_service import RuntimeService


router = APIRouter(prefix="/internal/v1")
runtime_service = RuntimeService()


@router.post("/meetings/{meeting_id}/runtime", status_code=201)
def create_runtime(meeting_id: UUID) -> dict[str, object]:
    return runtime_service.start(meeting_id).as_dict()


@router.get("/meetings/{meeting_id}/status")
def runtime_status(meeting_id: UUID) -> dict[str, object]:
    session = runtime_service.status(meeting_id)
    if session is None:
        raise HTTPException(status_code=404, detail="runtime not found")
    return session.as_dict()


@router.post("/runtimes/{runtime_session_id}/stop")
def stop_runtime(runtime_session_id: UUID) -> dict[str, object]:
    session = runtime_service.stop(runtime_session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="runtime not found")
    return session.as_dict()
