from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
_project_env_path = PROJECT_ROOT / ".env"
_default_runtime_root = (
    PROJECT_ROOT
    if _project_env_path.exists()
    else Path.home() / "meeting_runtime"
)
RUNTIME_ROOT = Path(
    os.getenv("MEETING_RUNTIME_DIR", str(_default_runtime_root))
).expanduser()


def _load_env_file() -> None:
    """Load local settings, falling back to the Linux runtime environment."""
    env_paths = [PROJECT_ROOT / ".env"]
    if RUNTIME_ROOT != PROJECT_ROOT:
        env_paths.append(RUNTIME_ROOT / ".env")

    for env_path in env_paths:
        if not env_path.exists():
            continue
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
        os.getenv("DATABASE_PATH", str(RUNTIME_ROOT / "data" / "meeting.db"))
    )
    speaker_database_path: Path = Path(
        os.getenv(
            "SPEAKER_DATABASE_PATH",
            str(RUNTIME_ROOT / "data" / "qdrant_speakers"),
        )
    )
    speaker_match_threshold: float = float(
        os.getenv("SPEAKER_MATCH_THRESHOLD", "0.86")
    )
    speaker_open_set_floor: float = float(
        os.getenv("SPEAKER_OPEN_SET_FLOOR", "0.86")
    )
    speaker_single_profile_threshold: float = float(
        os.getenv("SPEAKER_SINGLE_PROFILE_THRESHOLD", "0.90")
    )
    speaker_match_margin: float = float(
        os.getenv("SPEAKER_MATCH_MARGIN", "0.035")
    )
    speaker_consensus_ratio: float = float(
        os.getenv("SPEAKER_CONSENSUS_RATIO", "0.67")
    )
    speaker_min_id_seconds: float = float(
        os.getenv("SPEAKER_MIN_ID_SECONDS", "2.5")
    )
    speaker_id_window_seconds: float = float(
        os.getenv("SPEAKER_ID_WINDOW_SECONDS", "3.0")
    )
    speaker_id_max_windows: int = int(
        os.getenv("SPEAKER_ID_MAX_WINDOWS", "2")
    )
    enable_asr_preprocessing: bool = _env_bool(
        "ENABLE_ASR_PREPROCESSING", True
    )
    asr_high_pass_hz: float = float(
        os.getenv("ASR_HIGH_PASS_HZ", "70")
    )
    asr_target_rms: float = float(
        os.getenv("ASR_TARGET_RMS", "0.065")
    )
    asr_final_padding_seconds: float = float(
        os.getenv("ASR_FINAL_PADDING_SECONDS", "0.66")
    )
    asr_enhancer: str = os.getenv(
        "ASR_ENHANCER", "dpdfnet_baseline"
    ).strip().lower()
    asr_enhancer_model: Path = Path(
        os.getenv(
            "ASR_ENHANCER_MODEL",
            str(RUNTIME_ROOT / "models" / "dpdfnet_baseline.onnx"),
        )
    )
    zipformer_model_dir: Path = Path(
        os.getenv(
            "ZIPFORMER_MODEL_DIR",
            str(RUNTIME_ROOT / "Zipformer-30M-RNNT-Streaming-6000h"),
        )
    )
    asr_enhancer_threads: int = int(
        os.getenv("ASR_ENHANCER_THREADS", "1")
    )
    asr_enhancer_bypass_snr_db: float = float(
        os.getenv("ASR_ENHANCER_BYPASS_SNR_DB", "15")
    )
    asr_enhancer_full_snr_db: float = float(
        os.getenv("ASR_ENHANCER_FULL_SNR_DB", "3")
    )
    asr_enhancer_max_mix: float = float(
        os.getenv("ASR_ENHANCER_MAX_MIX", "0.65")
    )
    asr_enhancer_attack: float = float(
        os.getenv("ASR_ENHANCER_ATTACK", "0.20")
    )
    asr_enhancer_release: float = float(
        os.getenv("ASR_ENHANCER_RELEASE", "0.65")
    )
    timeline_asr_quality_margin: float = float(
        os.getenv("TIMELINE_ASR_QUALITY_MARGIN", "3.5")
    )
    timeline_asr_rms_ratio: float = float(
        os.getenv("TIMELINE_ASR_RMS_RATIO", "0.48")
    )
    timeline_final_settle_seconds: float = float(
        os.getenv("TIMELINE_FINAL_SETTLE_SECONDS", "0.75")
    )
    llm_inline_wait_seconds: float = float(
        os.getenv("LLM_INLINE_WAIT_SECONDS", "0.35")
    )
    wavlm_num_threads: int = int(
        os.getenv("WAVLM_NUM_THREADS", "2")
    )
    speaker_early_exit_score_buffer: float = float(
        os.getenv("SPEAKER_EARLY_EXIT_SCORE_BUFFER", "0.025")
    )
    speaker_early_exit_margin_buffer: float = float(
        os.getenv("SPEAKER_EARLY_EXIT_MARGIN_BUFFER", "0.015")
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
