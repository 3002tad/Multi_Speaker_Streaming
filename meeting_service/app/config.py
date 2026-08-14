from __future__ import annotations

import os
from dataclasses import dataclass

def _origins() -> list[str]:
    raw = os.getenv("MEETING_ALLOWED_ORIGINS", "http://localhost:5173")
    return [item.strip() for item in raw.split(",") if item.strip()]


def _bool(name: str, default: bool = False) -> bool:
    return os.getenv(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


def _validate_secret(name: str, value: str, *, minimum_length: int = 24) -> None:
    normalized = value.strip().lower()
    placeholder = (
        not normalized
        or normalized in {"changeme", "replace_me", "replace-before-public-deploy"}
        or normalized.startswith(("replace", "change-me", "local-", "example"))
    )
    if len(value.strip()) < minimum_length or placeholder:
        raise RuntimeError(
            f"{name} must be a non-placeholder random value of at least "
            f"{minimum_length} characters"
        )


@dataclass(frozen=True)
class Settings:
    strict_config: bool = _bool("MEETING_STRICT_CONFIG")
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
    # Local development still uses a key; deployments must override it via ENV.
    service_key: str = os.getenv("MEETING_SERVICE_KEY", "local-meeting-service-key")
    ai_base_url: str = os.getenv("MEETING_AI_BASE_URL", "http://meeting-ai-api:8001")
    ai_enabled: bool = _bool("MEETING_AI_ENABLED")
    ai_timeout_seconds: float = max(
        1.0, float(os.getenv("MEETING_AI_TIMEOUT_SECONDS", "60"))
    )
    minutes_auto_update_debounce_seconds: float = max(
        0.2, float(os.getenv("MEETING_MINUTES_AUTO_UPDATE_DEBOUNCE_SECONDS", "5"))
    )
    ai_callback_url: str = os.getenv(
        "MEETING_AI_CALLBACK_URL",
        "http://meeting-service:8002/internal/v1/ai-events",
    )
    redis_url: str = os.getenv("MEETING_REDIS_URL", "")
    runtime_token_secret: str = os.getenv("MEETING_RUNTIME_TOKEN_SECRET", "change-me-runtime-token-secret-32bytes")
    runtime_token_algorithm: str = os.getenv("MEETING_RUNTIME_TOKEN_ALGORITHM", "HS256")
    runtime_token_issuer: str = os.getenv("MEETING_RUNTIME_TOKEN_ISSUER", "ecabinet")
    runtime_token_audience: str = os.getenv("MEETING_RUNTIME_TOKEN_AUDIENCE", "meeting-service")
    livekit_url: str = os.getenv("MEETING_LIVEKIT_URL", "")
    # Browser clients need the LAN-reachable WSS endpoint, while the Agent
    # should remain on the private Docker network. Keeping those endpoints
    # distinct prevents a container from hairpinning through the LAN router.
    livekit_agent_url: str = os.getenv("MEETING_LIVEKIT_AGENT_URL", "")
    livekit_api_key: str = os.getenv("MEETING_LIVEKIT_API_KEY", "")
    livekit_api_secret: str = os.getenv("MEETING_LIVEKIT_API_SECRET", "")
    livekit_token_ttl_seconds: int = int(os.getenv("MEETING_LIVEKIT_TOKEN_TTL_SECONDS", "900"))
    minio_endpoint: str = os.getenv("MEETING_MINIO_ENDPOINT", "")
    minio_access_key: str = os.getenv("MEETING_MINIO_ACCESS_KEY", "")
    minio_secret_key: str = os.getenv("MEETING_MINIO_SECRET_KEY", "")
    minio_bucket: str = os.getenv("MEETING_MINIO_BUCKET", "meeting-minutes")
    minio_secure: bool = os.getenv("MEETING_MINIO_SECURE", "false").lower() == "true"
    export_root: str = os.getenv("MEETING_EXPORT_ROOT", "/tmp/meeting-exports")

    def validate_startup(self) -> None:
        """Fail closed only for a deployment explicitly marked strict."""
        if not self.strict_config:
            return
        _validate_secret("MEETING_SERVICE_KEY", self.service_key)
        _validate_secret("MEETING_RUNTIME_TOKEN_SECRET", self.runtime_token_secret, minimum_length=32)
        if self.persistence_enabled and not self.database_url:
            raise RuntimeError("MEETING_DATABASE_URL is required when persistence is enabled")
        if self.persistence_enabled and not self.redis_url:
            raise RuntimeError("MEETING_REDIS_URL is required when persistence is enabled")
        if not (self.livekit_url and self.livekit_api_key and self.livekit_api_secret):
            raise RuntimeError("LiveKit URL, API key and API secret are required")
        if self.livekit_url.strip().lower().startswith(("replace", "change-me", "example")):
            raise RuntimeError("MEETING_LIVEKIT_URL must not be a placeholder")
        _validate_secret("MEETING_LIVEKIT_API_KEY", self.livekit_api_key, minimum_length=3)
        _validate_secret("MEETING_LIVEKIT_API_SECRET", self.livekit_api_secret)


settings = Settings()
