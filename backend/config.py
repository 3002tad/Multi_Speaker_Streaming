from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _load_env_file() -> None:
    """Load the project's .env without overriding exported environment values."""
    env_path = PROJECT_ROOT / ".env"
    if not env_path.exists():
        return

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip("\"'")
        if key:
            os.environ.setdefault(key, value)


_load_env_file()


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    livekit_url: str = os.getenv(
        "LIVEKIT_URL", "wss://livekit.simplething.id.vn"
    )
    livekit_api_key: str = os.getenv("LIVEKIT_API_KEY", "")
    livekit_api_secret: str = os.getenv("LIVEKIT_API_SECRET", "")
    meeting_room: str = os.getenv("MEETING_ROOM", "paperless-demo")
    meeting_code: str = os.getenv("MEETING_CODE", "DEMO-001")
    internal_api_key: str = os.getenv("INTERNAL_API_KEY", "local-demo-key")
    backend_internal_url: str = os.getenv(
        "BACKEND_INTERNAL_URL", "http://127.0.0.1:8000/api/internal/events"
    )
    ai_server_ws_url: str = os.getenv(
        "AI_SERVER_WS_URL", "ws://127.0.0.1:8001"
    )
    ai_server_http_url: str = os.getenv(
        "AI_SERVER_HTTP_URL", "http://127.0.0.1:8001"
    )
    enable_llm_refinement: bool = _env_bool(
        "ENABLE_LLM_REFINEMENT", True
    )
    ollama_model: str = os.getenv("OLLAMA_MODEL", "qwen2.5:0.5b")
    llm_timeout_seconds: float = float(
        os.getenv("LLM_TIMEOUT_SECONDS", "5")
    )
    database_path: Path = Path(
        os.getenv("DATABASE_PATH", str(PROJECT_ROOT / "data" / "meeting.db"))
    )
    speaker_database_path: Path = Path(
        os.getenv(
            "SPEAKER_DATABASE_PATH",
            str(PROJECT_ROOT / "data" / "qdrant_speakers"),
        )
    )

    @property
    def livekit_configured(self) -> bool:
        invalid = {"", "replace_me", "changeme"}
        return (
            self.livekit_api_key.lower() not in invalid
            and self.livekit_api_secret.lower() not in invalid
        )

    def validate_livekit(self) -> None:
        if not self.livekit_configured:
            raise RuntimeError(
                "LIVEKIT_API_KEY and LIVEKIT_API_SECRET must be set in .env"
            )

    def validate_runtime(self) -> None:
        self.validate_livekit()
        if (
            len(self.internal_api_key) < 24
            or self.internal_api_key.startswith("replace_")
        ):
            raise RuntimeError(
                "INTERNAL_API_KEY must be a random value of at least 24 chars"
            )


settings = Settings()
