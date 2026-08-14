from __future__ import annotations

from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
import socketio

from meeting_service.app.api.internal import router as internal_router
from meeting_service.app.api.ai_events import router as ai_events_router
from meeting_service.app.api.socketio import sio
from meeting_service.app.config import settings
from meeting_service.app.application.runtime_service import RuntimeService
from meeting_service.app.application.meeting_content import MeetingContentStore, SqlAlchemyMeetingContentRepository
from meeting_service.app.application.minutes_analysis import (
    MinutesAnalysisService,
    SqlAlchemyMinutesAnalysisRepository,
    analysis_store,
)
from meeting_service.app.application.minutes_auto_update import MinutesAutoUpdateCoordinator
from meeting_service.app.application.purge import SqlAlchemyPurgeTombstoneStore, purge_tombstones
from meeting_service.app.infrastructure.database import create_session_factory
from meeting_service.app.infrastructure.repositories import InMemoryAIEventRepository, SqlAlchemyAIEventRepository, SqlAlchemyRuntimeRepository
from meeting_service.app.infrastructure.ai_client import MeetingAIClient
from meeting_service.app.infrastructure.object_storage import build_object_storage
from meeting_service.app.infrastructure.readiness import collect_readiness


app = FastAPI(title="Meeting Service", version="0.1.0")


@app.exception_handler(HTTPException)
async def internal_problem_details(request: Request, exc: HTTPException):
    """Keep internal contract errors stable without changing public adapters."""
    if not request.url.path.startswith("/internal/v1"):
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail}, headers=exc.headers)
    detail = exc.detail if isinstance(exc.detail, str) else "Request failed"
    return JSONResponse(
        status_code=exc.status_code,
        media_type="application/problem+json",
        content={
            "code": f"HTTP_{exc.status_code}",
            "message": detail,
            "correlation_id": request.headers.get("X-Correlation-ID") or str(uuid4()),
        },
        headers=exc.headers,
    )


@app.exception_handler(RequestValidationError)
async def internal_validation_problem(request: Request, exc: RequestValidationError):
    if not request.url.path.startswith("/internal/v1"):
        return JSONResponse(status_code=422, content={"detail": exc.errors()})
    return JSONResponse(
        status_code=422,
        media_type="application/problem+json",
        content={
            "code": "HTTP_422",
            "message": "Request validation failed",
            "correlation_id": request.headers.get("X-Correlation-ID") or str(uuid4()),
            "details": {"errors": exc.errors()},
        },
    )

app.include_router(internal_router)
app.include_router(ai_events_router)
if settings.persistence_enabled:
    session_factory = create_session_factory(settings.database_url)
    app.state.session_factory = session_factory
    app.state.runtime_service = RuntimeService(SqlAlchemyRuntimeRepository(session_factory))
    app.state.content_store = SqlAlchemyMeetingContentRepository(session_factory)
    app.state.ai_event_repository = SqlAlchemyAIEventRepository(session_factory)
    app.state.minutes_analysis = MinutesAnalysisService(
        SqlAlchemyMinutesAnalysisRepository(session_factory)
    )
    app.state.purge_tombstones = SqlAlchemyPurgeTombstoneStore(session_factory)
else:
    app.state.runtime_service = RuntimeService()
    app.state.content_store = MeetingContentStore()
    app.state.ai_event_repository = InMemoryAIEventRepository(app.state.content_store)
    app.state.minutes_analysis = MinutesAnalysisService(analysis_store)
    app.state.purge_tombstones = purge_tombstones
app.state.object_storage = build_object_storage()
app.state.minutes_auto_update = MinutesAutoUpdateCoordinator(
    app.state.minutes_analysis,
    app.state.content_store,
    app.state.runtime_service,
    debounce_seconds=settings.minutes_auto_update_debounce_seconds,
)
if settings.ai_enabled:
    app.state.ai_client = MeetingAIClient(
        settings.ai_base_url,
        settings.service_key,
        timeout=settings.ai_timeout_seconds,
    )
    app.state.runtime_service.ai_client = app.state.ai_client
    app.state.minutes_analysis.ai_client = app.state.ai_client


@app.on_event("startup")
async def validate_deployment_configuration() -> None:
    settings.validate_startup()


@app.get("/health/live")
def health_live() -> dict[str, str]:
    return {"status": "ok", "service": settings.service_name}


@app.get("/health/ready")
def health_ready(request: Request):
    report = collect_readiness(
        persistence_enabled=settings.persistence_enabled,
        session_factory=getattr(request.app.state, "session_factory", None),
        redis_url=settings.redis_url,
        object_storage=request.app.state.object_storage,
        ai_enabled=settings.ai_enabled,
        ai_base_url=settings.ai_base_url,
        service_key=settings.service_key,
    )
    report["service"] = settings.service_name
    if report["status"] == "degraded":
        return JSONResponse(status_code=503, content=report)
    return report


socket_app = socketio.ASGIApp(
    sio,
    other_asgi_app=app,
    socketio_path=settings.socketio_path,
)
