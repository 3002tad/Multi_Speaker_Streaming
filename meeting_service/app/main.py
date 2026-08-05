from __future__ import annotations

from fastapi import FastAPI
import socketio

from meeting_service.app.api.internal import router as internal_router
from meeting_service.app.api.socketio import sio
from meeting_service.app.config import settings
from meeting_service.app.application.runtime_service import RuntimeService
from meeting_service.app.infrastructure.database import create_session_factory
from meeting_service.app.infrastructure.repositories import SqlAlchemyRuntimeRepository


app = FastAPI(title="Meeting Service", version="0.1.0")
app.include_router(internal_router)
if settings.persistence_enabled:
    app.state.runtime_service = RuntimeService(
        SqlAlchemyRuntimeRepository(create_session_factory(settings.database_url))
    )
else:
    app.state.runtime_service = RuntimeService()


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
