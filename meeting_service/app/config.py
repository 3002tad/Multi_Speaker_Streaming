from __future__ import annotations

import os
from dataclasses import dataclass


def _origins() -> list[str]:
    raw = os.getenv("MEETING_ALLOWED_ORIGINS", "http://localhost:5173")
    return [item.strip() for item in raw.split(",") if item.strip()]


def _bool(name: str, default: bool = False) -> bool:
    return os.getenv(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    service_name: str = os.getenv("MEETING_SERVICE_NAME", "meeting-service")
    host: str = os.getenv("MEETING_SERVICE_HOST", "0.0.0.0")
    port: int = int(os.getenv("MEETING_SERVICE_PORT", "8002"))
    database_url: str = os.getenv(
        "MEETING_DATABASE_URL", "postgresql://meeting:meeting@localhost:5433/meeting_service"
    )
    persistence_enabled: bool = _bool("MEETING_PERSISTENCE_ENABLED")
    socketio_path: str = os.getenv(
        "MEETING_SOCKETIO_PATH", "meeting-runtime/socket.io"
    ).strip("/")
    allowed_origins: tuple[str, ...] = tuple(_origins())
    service_key: str = os.getenv("MEETING_SERVICE_KEY", "")
    ai_base_url: str = os.getenv("MEETING_AI_BASE_URL", "http://meeting-ai-api:8001")


settings = Settings()
