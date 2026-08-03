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


def _env_keep_alive(name: str, default: str) -> str | int:
    """Parse Ollama's numeric ``-1`` sentinel without breaking durations."""
    value = os.getenv(name, default).strip()
    return -1 if value == "-1" else value


def _env_choice_int(name: str, default: int, choices: tuple[int, ...]) -> int:
    """Read an integer setting while keeping an invalid runtime safe."""
    try:
        value = int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default
    return value if value in choices else default


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
    audio_frame_size_ms: int = _env_choice_int(
        "AUDIO_FRAME_SIZE_MS", 20, (10, 20, 40, 50, 100)
    )
    enable_llm_refinement: bool = _env_bool(
        "ENABLE_LLM_REFINEMENT", False
    )
    # Legacy per-segment transcript cleanup.  It remains disabled by default:
    # Qwen is reserved for the evidence-backed Minutes Composer below.
    ollama_url: str = os.getenv("OLLAMA_URL", "http://127.0.0.1:11434")
    ollama_model: str = os.getenv("OLLAMA_MODEL", "qwen2.5:0.5b")
    # `direct` keeps the existing free-form cleanup model.  In
    # `sailor_candidate` mode the LLM can only accept or reject the exact
    # dictionary/G2P candidate; it never generates replacement text.
    refinement_backend: str = os.getenv(
        "REFINEMENT_BACKEND", "direct"
    ).strip().lower()
    sailor_model: str = os.getenv("SAILOR_MODEL", "sailor2:1b")
    sailor_keep_alive: str = os.getenv("SAILOR_KEEP_ALIVE", "5m")
    sailor_num_threads: int = int(os.getenv("SAILOR_NUM_THREADS", "2"))
    sailor_language: str = os.getenv("SAILOR_LANGUAGE", "vi-VN")
    sailor_context_turns: int = int(
        os.getenv("SAILOR_CONTEXT_TURNS", "2")
    )
    llm_timeout_seconds: float = float(
        os.getenv("LLM_TIMEOUT_SECONDS", "5")
    )
    # Minutes Composer runs after a final global turn reaches the backend.
    # It must not be used in the realtime ASR path.
    # The demo baseline composes official minutes asynchronously with
    # Qwen2.5:3B.  This remains outside the realtime ASR path.
    minutes_composer_enabled: bool = _env_bool(
        "MINUTES_COMPOSER_ENABLED", True
    )
    minutes_composer_model: str = os.getenv(
        "MINUTES_COMPOSER_MODEL", "qwen2.5:3b"
    )
    # ``timeline`` is the safe demo default: show the final, formatted
    # transcript as a reviewable meeting timeline.  Use ``llm`` only after a
    # model has passed a meeting-specific quality test; small local models can
    # otherwise turn noisy ASR fragments into invented proposals/decisions.
    minutes_composer_mode: str = os.getenv(
        "MINUTES_COMPOSER_MODE", "timeline"
    ).strip().lower()
    minutes_composer_timeout_seconds: float = float(
        os.getenv("MINUTES_COMPOSER_TIMEOUT_SECONDS", "45")
    )
    minutes_composer_debounce_seconds: float = float(
        os.getenv("MINUTES_COMPOSER_DEBOUNCE_SECONDS", "0.4")
    )
    minutes_composer_num_threads: int = int(
        os.getenv("MINUTES_COMPOSER_NUM_THREADS", "12")
    )
    minutes_composer_temperature: float = float(
        os.getenv("MINUTES_COMPOSER_TEMPERATURE", "0")
    )
    minutes_composer_max_output_tokens: int = int(
        os.getenv("MINUTES_COMPOSER_MAX_OUTPUT_TOKENS", "200")
    )
    minutes_composer_max_context_chars: int = int(
        os.getenv("MINUTES_COMPOSER_MAX_CONTEXT_CHARS", "3200")
    )
    minutes_composer_context_window: int = int(
        os.getenv("MINUTES_COMPOSER_CONTEXT_WINDOW", "2048")
    )
    minutes_composer_keep_alive: str | int = _env_keep_alive(
        "MINUTES_COMPOSER_KEEP_ALIVE", "-1"
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
    # ``legacy`` keeps the light DSP frontend and optional dynamic enhancer.
    # ``dpdfnet`` is the backwards-compatible name for the guarded online
    # DPDFNet/GTCRN candidate plus bounded post-conditioning.
    asr_frontend: str = os.getenv(
        "ASR_FRONTEND", "legacy"
    ).strip().lower()
    asr_high_pass_hz: float = float(
        os.getenv("ASR_HIGH_PASS_HZ", "70")
    )
    asr_target_rms: float = float(
        os.getenv("ASR_TARGET_RMS", "0.065")
    )
    asr_loudness_window_seconds: float = float(
        os.getenv("ASR_LOUDNESS_WINDOW_SECONDS", "0.45")
    )
    asr_gain_boost_rate: float = float(
        os.getenv("ASR_GAIN_BOOST_RATE", "0.16")
    )
    asr_gain_attenuation_rate: float = float(
        os.getenv("ASR_GAIN_ATTENUATION_RATE", "0.35")
    )
    asr_final_padding_seconds: float = float(
        os.getenv("ASR_FINAL_PADDING_SECONDS", "0.66")
    )
    # The realtime stream produces the draft immediately.  After an endpoint,
    # one shared worker may replay the complete VAD turn before it is written
    # to the official timeline.  It is deliberately bounded: a busy room must
    # fall back to the realtime final instead of accumulating stale work.
    asr_final_turn_redecode_enabled: bool = _env_bool(
        "ASR_FINAL_TURN_REDECODE_ENABLED", False
    )
    asr_final_turn_redecode_queue_size: int = max(
        1, int(os.getenv("ASR_FINAL_TURN_REDECODE_QUEUE_SIZE", "4"))
    )
    asr_final_turn_redecode_timeout_seconds: float = float(
        os.getenv("ASR_FINAL_TURN_REDECODE_TIMEOUT_SECONDS", "8.0")
    )
    asr_final_turn_redecode_tail_padding_seconds: float = float(
        os.getenv("ASR_FINAL_TURN_REDECODE_TAIL_PADDING_SECONDS", "0.40")
    )
    asr_final_turn_redecode_min_overlap: float = float(
        os.getenv("ASR_FINAL_TURN_REDECODE_MIN_OVERLAP", "0.72")
    )
    asr_final_turn_redecode_max_word_ratio: float = float(
        os.getenv("ASR_FINAL_TURN_REDECODE_MAX_WORD_RATIO", "1.35")
    )
    # Silero controls transcript boundaries. A four-second endpoint delay
    # merged distinct speakers and made the timeline wait too long. Keep
    # these tunable per room so recordings can be calibrated without code.
    vad_min_speech_seconds: float = float(
        os.getenv("VAD_MIN_SPEECH_SECONDS", "0.20")
    )
    vad_min_silence_seconds: float = float(
        os.getenv("VAD_MIN_SILENCE_SECONDS", "0.90")
    )
    vad_prefix_padding_seconds: float = float(
        os.getenv("VAD_PREFIX_PADDING_SECONDS", "0.50")
    )
    vad_activation_threshold: float = float(
        os.getenv("VAD_ACTIVATION_THRESHOLD", "0.50")
    )
    vad_deactivation_threshold: float = float(
        os.getenv("VAD_DEACTIVATION_THRESHOLD", "0.30")
    )
    # Decode every microphone continuously and choose the strongest final
    # candidate. Frame-level switching can punch holes in fast speech.
    asr_decode_all_mics: bool = _env_bool(
        "ASR_DECODE_ALL_MICS", True
    )
    asr_soft_split_seconds: float = float(
        os.getenv("ASR_SOFT_SPLIT_SECONDS", "15")
    )
    asr_hard_split_seconds: float = float(
        os.getenv("ASR_HARD_SPLIT_SECONDS", "30")
    )
    asr_split_min_silence_seconds: float = float(
        os.getenv("ASR_SPLIT_MIN_SILENCE_SECONDS", "0.30")
    )
    # Keep neural enhancement opt-in.  Current labelled clean/noisy samples
    # show no WER gain from DPDFNet and a higher CPU cost; retain the adapter
    # for future recordings where a measured A/B result justifies it.
    asr_enhancer: str = os.getenv("ASR_ENHANCER", "none").strip().lower()
    asr_enhancer_model: Path = Path(
        os.getenv(
            "ASR_ENHANCER_MODEL",
            str(RUNTIME_ROOT / "models" / "dpdfnet_baseline.onnx"),
        )
    )
    asr_enhancer_model_type: str = os.getenv(
        "ASR_ENHANCER_MODEL_TYPE", "dpdfnet"
    ).strip().lower()
    zipformer_model_dir: Path = Path(
        os.getenv(
            "ZIPFORMER_MODEL_DIR",
            str(RUNTIME_ROOT / "Zipformer-30M-RNNT-Streaming-6000h"),
        )
    )
    # Beam width for Zipformer's modified beam search.  Keep this small enough
    # for one decoder per microphone; benchmark 4/8/12 with evaluate_asr.py
    # before changing the demo default.
    zipformer_max_active_paths: int = max(
        1, int(os.getenv("ZIPFORMER_MAX_ACTIVE_PATHS", "4"))
    )
    # The model ships three streaming receptive-field variants.  Chunk 16 is
    # the lowest-latency baseline; 32/64 retain more acoustic context and are
    # useful for fast speech when the demo can tolerate extra look-ahead.
    zipformer_chunk_size: int = _env_choice_int(
        "ZIPFORMER_CHUNK_SIZE", 32, (16, 32, 64)
    )
    zipformer_blank_penalty: float = float(
        os.getenv("ZIPFORMER_BLANK_PENALTY", "0.4")
    )
    # Adaptive glossary is shared by contextual Zipformer hotwords and the
    # final-turn phonetic gate. Dynamic entries are evidence-backed and expire.
    adaptive_dictionary_enabled: bool = _env_bool(
        "ADAPTIVE_DICTIONARY_ENABLED", True
    )
    adaptive_dictionary_state_path: Path = Path(
        os.getenv(
            "ADAPTIVE_DICTIONARY_STATE_PATH",
            str(RUNTIME_ROOT / "data" / "adaptive_dictionary.json"),
        )
    )
    adaptive_dictionary_manual_path: Path = Path(
        os.getenv(
            "ADAPTIVE_DICTIONARY_MANUAL_PATH",
            str(RUNTIME_ROOT / "data" / "meeting_lexicon.txt"),
        )
    )
    topic_discovery_enabled: bool = _env_bool(
        "TOPIC_DISCOVERY_ENABLED", True
    )
    topic_discovery_state_path: Path = Path(
        os.getenv(
            "TOPIC_DISCOVERY_STATE_PATH",
            str(RUNTIME_ROOT / "data" / "topic_discovery.json"),
        )
    )
    topic_discovery_model: str = os.getenv(
        "TOPIC_DISCOVERY_MODEL", "qwen2.5:3b"
    )
    topic_discovery_bootstrap_seconds: float = float(
        os.getenv("TOPIC_DISCOVERY_BOOTSTRAP_SECONDS", "90")
    )
    topic_discovery_refresh_seconds: float = float(
        os.getenv("TOPIC_DISCOVERY_REFRESH_SECONDS", "60")
    )
    topic_discovery_minimum_turns: int = max(
        1, int(os.getenv("TOPIC_DISCOVERY_MINIMUM_TURNS", "6"))
    )
    topic_discovery_minimum_evidence_turns: int = max(
        1, int(os.getenv("TOPIC_DISCOVERY_MINIMUM_EVIDENCE_TURNS", "2"))
    )
    topic_discovery_minimum_topic_confidence: float = float(
        os.getenv("TOPIC_DISCOVERY_MINIMUM_TOPIC_CONFIDENCE", "0.65")
    )
    topic_discovery_minimum_term_confidence: float = float(
        os.getenv("TOPIC_DISCOVERY_MINIMUM_TERM_CONFIDENCE", "0.88")
    )
    topic_discovery_term_ttl_hours: float = float(
        os.getenv("TOPIC_DISCOVERY_TERM_TTL_HOURS", "0.25")
    )
    topic_discovery_maximum_terms: int = max(
        1, int(os.getenv("TOPIC_DISCOVERY_MAXIMUM_TERMS", "24"))
    )
    topic_discovery_maximum_context_chars: int = max(
        500, int(os.getenv("TOPIC_DISCOVERY_MAXIMUM_CONTEXT_CHARS", "6000"))
    )
    topic_discovery_maximum_window_seconds: float = float(
        os.getenv("TOPIC_DISCOVERY_MAXIMUM_WINDOW_SECONDS", "180")
    )
    topic_discovery_timeout_seconds: float = float(
        os.getenv("TOPIC_DISCOVERY_TIMEOUT_SECONDS", "30")
    )
    zipformer_hotwords_enabled: bool = _env_bool(
        "ZIPFORMER_HOTWORDS_ENABLED", True
    )
    zipformer_hotwords_score: float = float(
        os.getenv("ZIPFORMER_HOTWORDS_SCORE", "1.5")
    )
    zipformer_hotwords_min_confidence: float = float(
        os.getenv("ZIPFORMER_HOTWORDS_MIN_CONFIDENCE", "0.9")
    )
    adaptive_dictionary_phonetic_min_confidence: float = float(
        os.getenv("ADAPTIVE_DICTIONARY_PHONETIC_MIN_CONFIDENCE", "0.75")
    )
    zipformer_hotwords_path: Path = Path(
        os.getenv(
            "ZIPFORMER_HOTWORDS_PATH",
            str(RUNTIME_ROOT / "data" / "zipformer_hotwords.txt"),
        )
    )
    zipformer_bpe_vocab_path: Path = Path(
        os.getenv(
            "ZIPFORMER_BPE_VOCAB_PATH",
            str(RUNTIME_ROOT / "data" / "zipformer.bpe.vocab"),
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
    # DPDFNet baseline shifts waveform content by about 40 ms on the labelled
    # fixtures. The raw fallback must be delayed by the same amount before
    # waveform comparison or blending.
    asr_enhancer_alignment_delay_ms: float = float(
        os.getenv(
            "ASR_ENHANCER_ALIGNMENT_DELAY_MS",
            "0" if asr_enhancer_model_type == "gtcrn" else "40",
        )
    )
    asr_preservation_min_correlation: float = float(
        os.getenv("ASR_PRESERVATION_MIN_CORRELATION", "0.93")
    )
    asr_preservation_min_energy_ratio: float = float(
        os.getenv("ASR_PRESERVATION_MIN_ENERGY_RATIO", "0.65")
    )
    asr_preservation_max_energy_ratio: float = float(
        os.getenv("ASR_PRESERVATION_MAX_ENERGY_RATIO", "1.35")
    )
    asr_preservation_min_speech_band_ratio: float = float(
        os.getenv("ASR_PRESERVATION_MIN_SPEECH_BAND_RATIO", "0.80")
    )
    asr_preservation_max_speech_mix: float = float(
        os.getenv("ASR_PRESERVATION_MAX_SPEECH_MIX", "0.10")
    )
    asr_preservation_max_noise_mix: float = float(
        os.getenv("ASR_PRESERVATION_MAX_NOISE_MIX", "0.65")
    )
    asr_preservation_crossfade_ms: float = float(
        os.getenv("ASR_PRESERVATION_CROSSFADE_MS", "15")
    )
    asr_dpdfnet_post_dc_hz: float = float(
        os.getenv("ASR_DPDFNET_POST_DC_HZ", "20")
    )
    asr_dpdfnet_post_target_rms: float = float(
        os.getenv("ASR_DPDFNET_POST_TARGET_RMS", "0.055")
    )
    asr_dpdfnet_post_min_gain: float = float(
        os.getenv("ASR_DPDFNET_POST_MIN_GAIN", "0.75")
    )
    asr_dpdfnet_post_max_gain: float = float(
        os.getenv("ASR_DPDFNET_POST_MAX_GAIN", "1.50")
    )
    asr_dpdfnet_post_attenuation_rate: float = float(
        os.getenv("ASR_DPDFNET_POST_ATTENUATION_RATE", "0.08")
    )
    asr_dpdfnet_post_boost_rate: float = float(
        os.getenv("ASR_DPDFNET_POST_BOOST_RATE", "0.02")
    )
    asr_dpdfnet_post_activity_floor: float = float(
        os.getenv("ASR_DPDFNET_POST_ACTIVITY_FLOOR", "0.003")
    )
    asr_dpdfnet_post_peak_limit: float = float(
        os.getenv("ASR_DPDFNET_POST_PEAK_LIMIT", "0.97")
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
    phonetic_recovery_enabled: bool = _env_bool(
        "PHONETIC_RECOVERY_ENABLED", True
    )
    phonetic_dictionary_path: Path = Path(
        os.getenv(
            "PHONETIC_DICTIONARY_PATH",
            str(RUNTIME_ROOT / "data" / "phonetic_dictionary.txt"),
        )
    )
    phonetic_recovery_threshold: float = float(
        os.getenv("PHONETIC_RECOVERY_THRESHOLD", "0.86")
    )
    phonetic_recovery_margin: float = float(
        os.getenv("PHONETIC_RECOVERY_MARGIN", "0.06")
    )
    phonetic_recovery_max_words: int = int(
        os.getenv("PHONETIC_RECOVERY_MAX_WORDS", "4")
    )
    phonetic_backend: str = os.getenv(
        "PHONETIC_BACKEND", "grapheme"
    ).strip().lower()
    phonetic_g2p_model_path: Path = Path(
        os.getenv(
            "PHONETIC_G2P_MODEL_PATH",
            str(RUNTIME_ROOT / "models" / "g2p_multilingual_byT5_tiny_onnx"),
        )
    )
    phonetic_g2p_language: str = os.getenv(
        "PHONETIC_G2P_LANGUAGE", "vie-c"
    )
    phonetic_g2p_threads: int = int(
        os.getenv("PHONETIC_G2P_THREADS", "4")
    )
    phonetic_g2p_weight: float = float(
        os.getenv("PHONETIC_G2P_WEIGHT", "0.65")
    )
    phonetic_g2p_prefilter: float = float(
        os.getenv("PHONETIC_G2P_PREFILTER", "0.80")
    )
    phonetic_g2p_max_calls: int = int(
        os.getenv("PHONETIC_G2P_MAX_CALLS", "8")
    )
    # When enabled, even an exact dictionary alias must receive an IPA score
    # from G2P. This is the safe mode for Sailor candidate A/B testing.
    phonetic_g2p_force: bool = _env_bool("PHONETIC_G2P_FORCE", False)
    # The triple gate is always prepared when the G2P backend is available.
    # It executes only for final-turn dictionary candidates after the cheap
    # grapheme prefilter, never for realtime partial transcript.
    phonetic_triple_weight: float = float(
        os.getenv("PHONETIC_TRIPLE_WEIGHT", "0.75")
    )
    phonetic_triple_min_consensus: int = int(
        os.getenv("PHONETIC_TRIPLE_MIN_CONSENSUS", "2")
    )
    phonetic_triple_consensus_tolerance: float = float(
        os.getenv("PHONETIC_TRIPLE_CONSENSUS_TOLERANCE", "0.18")
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
        if self.asr_frontend not in {"legacy", "dpdfnet"}:
            raise RuntimeError(
                "ASR_FRONTEND must be either 'legacy' or 'dpdfnet'"
            )
        if self.asr_enhancer_model_type not in {"dpdfnet", "gtcrn"}:
            raise RuntimeError(
                "ASR_ENHANCER_MODEL_TYPE must be 'dpdfnet' or 'gtcrn'"
            )
        if (
            self.asr_frontend == "dpdfnet"
            and not self.asr_enhancer_model.is_file()
        ):
            raise RuntimeError(
                "Speech-enhancement model not found: "
                f"{self.asr_enhancer_model}"
            )
        if (
            len(self.internal_api_key) < 24
            or self.internal_api_key.startswith("replace_")
        ):
            raise RuntimeError(
                "INTERNAL_API_KEY must be a random value of at least 24 chars"
            )


settings = Settings()
