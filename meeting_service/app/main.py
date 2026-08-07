from __future__ import annotations

from fastapi import FastAPI
import socketio

from meeting_service.app.api.internal import router as internal_router
from meeting_service.app.api.ai_events import router as ai_events_router
from meeting_service.app.api.socketio import sio
from meeting_service.app.config import settings
from meeting_service.app.application.runtime_service import RuntimeService
from meeting_service.app.application.meeting_content import MeetingContentStore, SqlAlchemyMeetingContentRepository
from meeting_service.app.infrastructure.database import create_session_factory
from meeting_service.app.infrastructure.repositories import SqlAlchemyAIEventRepository, SqlAlchemyRuntimeRepository
from meeting_service.app.infrastructure.ai_client import MeetingAIClient


app = FastAPI(title="Meeting Service", version="0.1.0")
app.include_router(internal_router)
app.include_router(ai_events_router)
if settings.persistence_enabled:
    session_factory = create_session_factory(settings.database_url)
    app.state.runtime_service = RuntimeService(SqlAlchemyRuntimeRepository(session_factory))
    app.state.ai_event_repository = SqlAlchemyAIEventRepository(session_factory)
    app.state.content_store = SqlAlchemyMeetingContentRepository(session_factory)
else:
    app.state.runtime_service = RuntimeService()
    app.state.content_store = MeetingContentStore()
if settings.ai_enabled:
    app.state.runtime_service.ai_client = MeetingAIClient(settings.ai_base_url, settings.service_key)


@app.get("/health/live")
def health_live() -> dict[str, str]:
    return {"status": "ok", "service": settings.service_name}


@app.get("/health/ready")
def health_ready() -> dict[str, str]:
    # Database/Redis/AI readiness checks are intentionally added in the next
    # slice; liveness remains independent from those dependencies.
    return {"status": "ok", "service": settings.service_name}


socket_app = socketio.ASGIApp(
    sio,
    other_asgi_app=app,
    socketio_path=settings.socketio_path,
)
