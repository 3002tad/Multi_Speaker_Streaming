import asyncio
import collections
import time
import json
import threading
import torch
import sherpa_onnx
import numpy as np
import io
import re
import soundfile as sf
import uuid
import subprocess
import hmac
from fastapi import (
    FastAPI,
    WebSocket,
    WebSocketDisconnect,
    UploadFile,
    File,
    Form,
    Body,
    Header,
    HTTPException,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from transformers import Wav2Vec2FeatureExtractor, WavLMForXVector
from qdrant_client import QdrantClient
from qdrant_client.models import VectorParams, Distance, PointStruct
from openai import AsyncOpenAI
from livekit import rtc
from livekit.plugins import silero
from livekit.agents.vad import VADEventType
from backend.audio_pipeline import (
    AudioQualityTracker,
    CoordinatedVadTimeline,
    DynamicEnhancementController,
    FinalCandidate,
    StreamingDpdfNetEnhancer,
    StreamingAsrPreprocessor,
    select_speaker_windows,
    speech_envelope,
    summarize_quality,
    unpack_audio_packet,
)
from backend.config import settings
from backend.adaptive_dictionary import (
    AdaptiveDictionary,
    HotwordArtifacts,
    build_hotword_artifacts,
)
from backend.speaker_identity import (
    adaptive_absolute_threshold,
    build_enrollment_profile,
    can_early_accept_speaker,
    decide_open_set_speaker,
)
from backend.text_refinement import (
    EpitranVietnamesePhonemizer,
    G2POnnxPhonemizer,
    PanphonFeatureScorer,
    PhoneticRecovery,
    PhoneticLexicon,
    SeaG2PVietnamesePhonemizer,
    TriplePhoneticScorer,
    format_transcript_sentence,
    normalize_meeting_terms,
)

print("Bắt đầu khởi tạo các mô hình AI...")

# ============================================================
# 1. ASR – Zipformer
# ============================================================
print("1. Đang nạp mô hình ASR (Zipformer)...")
asr_dir = str(settings.zipformer_model_dir)
if settings.adaptive_dictionary_enabled:
    adaptive_dictionary = AdaptiveDictionary.from_paths(
        seed_path=settings.phonetic_dictionary_path,
        state_path=settings.adaptive_dictionary_state_path,
    )
else:
    adaptive_dictionary = AdaptiveDictionary(
        state_path=settings.adaptive_dictionary_state_path,
    )

hotword_artifacts: HotwordArtifacts | None = None
if settings.zipformer_hotwords_enabled:
    try:
        hotword_artifacts = build_hotword_artifacts(
            adaptive_dictionary,
            model_dir=settings.zipformer_model_dir,
            hotwords_path=settings.zipformer_hotwords_path,
            bpe_vocab_path=settings.zipformer_bpe_vocab_path,
            minimum_confidence=settings.zipformer_hotwords_min_confidence,
        )
        print(
            "[Dictionary] Đã tạo "
            f"{hotword_artifacts.phrase_count} hotword Zipformer."
        )
    except Exception as exc:
        # The decoder must retain its known baseline rather than start with a
        # malformed contextual vocabulary. The phonetic branch remains usable.
        print(f"[Dictionary] Tắt hotword Zipformer: {exc}")


def create_asr_recognizer(
    artifacts: HotwordArtifacts | None = None,
) -> sherpa_onnx.OnlineRecognizer:
    """Create the baseline or the safe contextual Zipformer recognizer."""
    kwargs = {}
    if artifacts and artifacts.phrase_count:
        kwargs = {
            "modeling_unit": "bpe",
            "bpe_vocab": str(artifacts.bpe_vocab_path),
            "hotwords_file": str(artifacts.hotwords_path),
            "hotwords_score": settings.zipformer_hotwords_score,
        }
    chunk = settings.zipformer_chunk_size
    return sherpa_onnx.OnlineRecognizer.from_transducer(
        tokens=f'{asr_dir}/config.json',
        encoder=f'{asr_dir}/encoder-epoch-31-avg-11-chunk-{chunk}-left-128.fp16.onnx',
        decoder=f'{asr_dir}/decoder-epoch-31-avg-11-chunk-{chunk}-left-128.fp16.onnx',
        joiner=f'{asr_dir}/joiner-epoch-31-avg-11-chunk-{chunk}-left-128.fp16.onnx',
        num_threads=1,
        sample_rate=16000,
        feature_dim=80,
        decoding_method='modified_beam_search',
        max_active_paths=settings.zipformer_max_active_paths,
        blank_penalty=settings.zipformer_blank_penalty,
        provider='cpu',
        **kwargs,
    )


recognizer = create_asr_recognizer(hotword_artifacts)
# sherpa streams are cheap, but the ONNX recognizer/model object is shared by
# all microphone connections. Serialize calls into it so decoding every mic
# continuously cannot race or corrupt another stream's state.
zipformer_inference_lock = threading.Lock()

# ============================================================
# 2. WavLM – Speaker Embedding
# ============================================================
print("2. Đang nạp mô hình WavLM...")
DEVICE = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
if DEVICE.type == "cpu":
    # Leave CPU capacity for Zipformer and the Ollama process.
    torch.set_num_threads(max(1, settings.wavlm_num_threads))
wavlm_extractor = Wav2Vec2FeatureExtractor.from_pretrained('microsoft/wavlm-base-sv')
wavlm_model = WavLMForXVector.from_pretrained('microsoft/wavlm-base-sv').to(DEVICE)
wavlm_model.eval()

# ============================================================
# 3. Silero VAD
# ============================================================
print("3. Đang nạp VAD (Silero VAD)...")
vad_model = silero.VAD.load(
    min_speech_duration=settings.vad_min_speech_seconds,
    min_silence_duration=settings.vad_min_silence_seconds,
    prefix_padding_duration=settings.vad_prefix_padding_seconds,
    activation_threshold=settings.vad_activation_threshold,
    deactivation_threshold=settings.vad_deactivation_threshold,
)

# ============================================================
# 4. Qdrant Vector Database
# ============================================================
print("4. Khởi tạo Qdrant Vector Database...")
settings.speaker_database_path.mkdir(parents=True, exist_ok=True)
qdrant = QdrantClient(path=str(settings.speaker_database_path))
if not qdrant.collection_exists(collection_name="speakers"):
    qdrant.create_collection(
        collection_name='speakers',
        vectors_config=VectorParams(size=512, distance=Distance.COSINE)
    )
gpu_lock = threading.Lock()

def extract_embedding(audio_array: np.ndarray) -> np.ndarray:
    """Trích xuất vector 512-D từ WavLM, chuẩn hóa L2."""
    inputs = wavlm_extractor(audio_array, sampling_rate=16000, return_tensors='pt')
    inputs = {k: v.to(DEVICE) for k, v in inputs.items()}
    with torch.no_grad():
        emb = wavlm_model(**inputs).embeddings
    emb = torch.nn.functional.normalize(emb, dim=-1)
    return emb.squeeze(0).cpu().numpy()


def _all_speaker_points(*, with_vectors: bool = False):
    points = []
    offset = None
    while True:
        page, offset = qdrant.scroll(
            collection_name="speakers",
            limit=256,
            offset=offset,
            with_payload=True,
            with_vectors=with_vectors,
        )
        points.extend(page)
        if offset is None:
            return points


def _speaker_label(point) -> str:
    return str((point.payload or {}).get("speaker_label", "")).strip()


def _delete_speaker_profile(speaker_name: str) -> None:
    normalized = speaker_name.strip().casefold()
    point_ids = [
        point.id
        for point in _all_speaker_points()
        if _speaker_label(point).casefold() == normalized
    ]
    if point_ids:
        qdrant.delete(
            collection_name="speakers",
            points_selector=point_ids,
            wait=True,
        )


def _query_speaker_scores(embedding: np.ndarray) -> dict[str, float]:
    result = qdrant.query_points(
        collection_name="speakers",
        query=embedding.tolist(),
        limit=64,
    )
    scores: dict[str, float] = {}
    for point in result.points:
        label = _speaker_label(point)
        if label:
            scores[label] = max(scores.get(label, -1.0), float(point.score))
    return scores


def _speaker_identity_clips(audio: np.ndarray) -> list[np.ndarray]:
    return select_speaker_windows(
        audio,
        minimum_seconds=settings.speaker_min_id_seconds,
        window_seconds=max(
            settings.speaker_min_id_seconds,
            settings.speaker_id_window_seconds,
        ),
        max_windows=max(1, settings.speaker_id_max_windows),
    )


def recognize_speaker_open_set(audio: np.ndarray):
    clips = _speaker_identity_clips(audio)
    if not clips:
        return decide_open_set_speaker(
            [],
            absolute_threshold=max(
                settings.speaker_match_threshold,
                settings.speaker_open_set_floor,
            ),
            margin_threshold=settings.speaker_match_margin,
            consensus_threshold=settings.speaker_consensus_ratio,
            single_profile_threshold=(
                settings.speaker_single_profile_threshold
            ),
        )

    absolute_threshold = max(
        calculate_dynamic_speaker_threshold(),
        settings.speaker_open_set_floor,
    )
    observations = []
    decision = None
    for index, clip in enumerate(clips):
        with gpu_lock:
            embedding = extract_embedding(clip)
        observations.append(_query_speaker_scores(embedding))
        decision = decide_open_set_speaker(
            observations,
            absolute_threshold=absolute_threshold,
            margin_threshold=settings.speaker_match_margin,
            consensus_threshold=settings.speaker_consensus_ratio,
            single_profile_threshold=(
                settings.speaker_single_profile_threshold
            ),
        )
        if (
            index == 0
            and len(clips) > 1
            and can_early_accept_speaker(
                decision,
                score_buffer=(
                    settings.speaker_early_exit_score_buffer
                ),
                margin_threshold=settings.speaker_match_margin,
                margin_buffer=(
                    settings.speaker_early_exit_margin_buffer
                ),
            )
        ):
            margin_text = (
                f"{decision.margin:.3f}"
                if decision.margin is not None
                else "n/a"
            )
            print(
                "   [WavLM] Early accept sau một cửa sổ "
                f"(score={decision.score:.3f}, "
                f"margin={margin_text})"
            )
            return decision
    return decision

# ============================================================
# DYNAMIC ADAPTIVE CONFIGURATION ENGINE
# ============================================================

def calculate_dynamic_speaker_threshold() -> float:
    """Raise the acceptance threshold when enrolled voices are close."""
    grouped: dict[str, list[np.ndarray]] = {}
    for point in _all_speaker_points(with_vectors=True):
        label = _speaker_label(point)
        if label and point.vector is not None:
            grouped.setdefault(label, []).append(
                np.asarray(point.vector, dtype=np.float32)
            )

    vecs = {}
    for label, vectors in grouped.items():
        centroid = np.mean(vectors, axis=0)
        norm = float(np.linalg.norm(centroid))
        if norm > 1e-8:
            vecs[label] = centroid / norm
    labels = list(vecs)
    max_sim = None
    for i in range(len(labels)):
        for j in range(i + 1, len(labels)):
            sim = float(np.dot(vecs[labels[i]], vecs[labels[j]]))
            if max_sim is None or sim > max_sim:
                max_sim = sim

    threshold = adaptive_absolute_threshold(
        base_floor=max(
            settings.speaker_match_threshold,
            settings.speaker_open_set_floor,
        ),
        single_profile_threshold=settings.speaker_single_profile_threshold,
        profile_count=len(labels),
        max_profile_similarity=max_sim,
        margin_threshold=settings.speaker_match_margin,
    )
    if max_sim is not None:
        print(
            f"   [WavLM AdaptiveGate] profiles={len(labels)} "
            f"closest_similarity={max_sim:.3f} "
            f"required_score={threshold:.3f}"
        )
    return threshold

import base64

room_timeline = CoordinatedVadTimeline(
    asr_quality_margin=settings.timeline_asr_quality_margin,
    asr_rms_ratio=settings.timeline_asr_rms_ratio,
    final_settle_seconds=settings.timeline_final_settle_seconds,
)


class HeavyWorkCoordinator:
    """Serialize CPU-heavy inference and give final transcripts priority."""

    def __init__(self):
        self.lock = asyncio.Lock()
        self.final_tasks: set[asyncio.Task] = set()

    def mark_final(self) -> None:
        task = asyncio.current_task()
        if task is not None:
            self.final_tasks.add(task)
            task.add_done_callback(self.final_tasks.discard)

    def has_pending_final(self) -> bool:
        self.final_tasks = {
            task for task in self.final_tasks if not task.done()
        }
        return bool(self.final_tasks)

    async def run_quick(self, func):
        if self.has_pending_final():
            return False, None
        async with self.lock:
            if self.has_pending_final():
                return False, None
            return True, await asyncio.to_thread(func)

    async def run_final_thread(self, func):
        async with self.lock:
            return await asyncio.to_thread(func)

    async def run_final_async(self, awaitable_factory):
        async with self.lock:
            return await awaitable_factory()


heavy_work = HeavyWorkCoordinator()

# Quản lý WebSocket clients kết nối tới Web Dashboard
class WebDashboardManager:
    def __init__(self):
        self.active_connections: list[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)
        await self.broadcast_speakers()

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)

    async def broadcast(self, message: dict):
        for connection in list(self.active_connections):
            try:
                await connection.send_text(json.dumps(message, ensure_ascii=False))
            except Exception:
                self.disconnect(connection)

    async def broadcast_audio(self, identity: str, data: bytes):
        if not self.active_connections:
            return
        b64_audio = base64.b64encode(data).decode('ascii')
        msg = {
            "type": "live_audio",
            "identity": identity,
            "audio": b64_audio,
            "ts": time.time()
        }
        await self.broadcast(msg)

    async def broadcast_speakers(self):
        points = _all_speaker_points(with_vectors=True)
        speakers = [_speaker_label(point) for point in points]
        sim_str = None
        grouped: dict[str, list[np.ndarray]] = {}
        for point in points:
            label = _speaker_label(point)
            if label and point.vector is not None:
                grouped.setdefault(label, []).append(np.asarray(point.vector))
        labels = list(grouped)
        if len(labels) >= 2:
            left = np.mean(grouped[labels[0]], axis=0)
            right = np.mean(grouped[labels[1]], axis=0)
            left /= np.linalg.norm(left)
            right /= np.linalg.norm(right)
            sim_str = f"{float(np.dot(left, right)):.4f}"

        await self.broadcast({
            "type": "enrolled_speakers",
            "speakers": sorted(set(filter(None, speakers))),
            "similarity": sim_str
        })

dashboard_manager = WebDashboardManager()

# ============================================================
# 5. LLM – Ollama Qwen2.5
# ============================================================
llm_client = AsyncOpenAI(
    base_url="http://localhost:11434/v1",
    api_key="ollama",
    timeout=settings.llm_timeout_seconds,
    max_retries=0,
)


def audio_frame_to_float(frame: rtc.AudioFrame) -> np.ndarray:
    """Convert a LiveKit frame to mono float32 without assuming its backing type."""
    raw = frame.data
    if isinstance(raw, np.ndarray):
        values = np.asarray(raw, dtype=np.int16).reshape(-1)
    else:
        values = np.frombuffer(raw, dtype=np.int16)
    return values.astype(np.float32) / 32768.0


def audio_chunk_sample_count(chunks: list[np.ndarray]) -> int:
    return sum(int(chunk.size) for chunk in chunks)


LLM_MIN_WORDS = 5  # Câu quá ngắn thì bỏ qua LLM, tránh hallucination
g2p_phonemizer = None
if settings.phonetic_recovery_enabled:
    if settings.phonetic_backend == "g2p_onnx":
        try:
            g2p_phonemizer = G2POnnxPhonemizer(
                settings.phonetic_g2p_model_path,
                language_code=settings.phonetic_g2p_language,
                threads=settings.phonetic_g2p_threads,
            )
            print(
                "[G2P] Đã nạp ByT5 ONNX "
                f"({settings.phonetic_g2p_language}, "
                f"{settings.phonetic_g2p_threads} threads)."
            )
        except Exception as exc:
            print(
                "[G2P] Không thể nạp ONNX, fallback grapheme: "
                f"{exc}"
            )
    elif settings.phonetic_backend not in {"grapheme", ""}:
        print(
            "[G2P] PHONETIC_BACKEND không hỗ trợ: "
            f"{settings.phonetic_backend}; dùng grapheme."
        )
triple_phonetic_scorer = None
if g2p_phonemizer is not None:
    try:
        triple_phonetic_scorer = TriplePhoneticScorer(
            EpitranVietnamesePhonemizer(),
            g2p_phonemizer,
            SeaG2PVietnamesePhonemizer(),
            PanphonFeatureScorer(),
            consensus_tolerance=settings.phonetic_triple_consensus_tolerance,
        )
        print("[Phonetic] Triple gate enabled: Epitran + ByT5 + SEA-G2P.")
    except Exception as exc:
        print(f"[Phonetic] Triple gate unavailable; using G2P only: {exc}")


def create_phonetic_lexicon(
    dictionary: AdaptiveDictionary,
) -> PhoneticLexicon:
    return PhoneticLexicon.from_file(
        settings.phonetic_dictionary_path,
        extra_entries=(
            dictionary.dynamic_phonetic_entries(
                minimum_confidence=settings.adaptive_dictionary_phonetic_min_confidence
            )
            if settings.adaptive_dictionary_enabled
            else ()
        ),
        threshold=settings.phonetic_recovery_threshold,
        margin=settings.phonetic_recovery_margin,
        max_words=settings.phonetic_recovery_max_words,
        phonemizer=g2p_phonemizer,
        g2p_weight=settings.phonetic_g2p_weight,
        g2p_prefilter=settings.phonetic_g2p_prefilter,
        g2p_max_calls=settings.phonetic_g2p_max_calls,
        g2p_force=settings.phonetic_g2p_force,
        triple_scorer=triple_phonetic_scorer,
        triple_weight=settings.phonetic_triple_weight,
        triple_min_consensus=settings.phonetic_triple_min_consensus,
        # In Sailor candidate mode this object produces proposals only.  Sailor
        # decides which individual spans are actually written into the transcript.
        auto_apply=settings.refinement_backend != "sailor_candidate",
    )


phonetic_lexicon = create_phonetic_lexicon(adaptive_dictionary)
dictionary_runtime_lock = threading.RLock()


def refresh_adaptive_dictionary_for_next_streams() -> dict[str, object]:
    """Reload persisted glossary without invalidating active ASR streams.

    Each WebSocket captures its recognizer at connect time. Swapping the global
    recognizer here therefore affects only microphones that connect after this
    function returns; an in-progress global turn continues with its original
    decoder and timestamp alignment.
    """
    global adaptive_dictionary, hotword_artifacts, phonetic_lexicon, recognizer
    with dictionary_runtime_lock:
        if settings.adaptive_dictionary_enabled:
            updated_dictionary = AdaptiveDictionary.from_paths(
                seed_path=settings.phonetic_dictionary_path,
                state_path=settings.adaptive_dictionary_state_path,
            )
        else:
            updated_dictionary = AdaptiveDictionary(
                state_path=settings.adaptive_dictionary_state_path,
            )
        updated_artifacts = None
        updated_recognizer = recognizer
        if settings.zipformer_hotwords_enabled:
            updated_artifacts = build_hotword_artifacts(
                updated_dictionary,
                model_dir=settings.zipformer_model_dir,
                hotwords_path=settings.zipformer_hotwords_path,
                bpe_vocab_path=settings.zipformer_bpe_vocab_path,
                minimum_confidence=settings.zipformer_hotwords_min_confidence,
            )
            updated_recognizer = create_asr_recognizer(updated_artifacts)

        # All work above succeeds before the references are swapped, so a
        # malformed dictionary cannot leave the live pipeline half-updated.
        adaptive_dictionary = updated_dictionary
        phonetic_lexicon = create_phonetic_lexicon(updated_dictionary)
        hotword_artifacts = updated_artifacts
        recognizer = updated_recognizer
        return {
            "active_entries": len(updated_dictionary.active_entries()),
            "dynamic_entries": len(updated_dictionary.active_dynamic_entries()),
            "hotword_enabled": bool(
                updated_artifacts and updated_artifacts.phrase_count
            ),
            "hotword_count": (
                updated_artifacts.phrase_count if updated_artifacts else 0
            ),
        }


def prepare_adaptive_dictionary_for_meeting(title: str) -> dict[str, object]:
    """Replace dynamic terms using explicit technical terms in a meeting title."""
    if not settings.adaptive_dictionary_enabled:
        return {"enabled": False, "entries": [], "hotword_count": 0}
    title = " ".join(title.split()).strip()
    if not title:
        raise ValueError("meeting_title must not be empty")
    entries = AdaptiveDictionary.entries_from_meeting_title(
        title,
        ttl_hours=settings.adaptive_dictionary_title_ttl_hours,
    )
    with dictionary_runtime_lock:
        adaptive_dictionary.save_dynamic_entries(entries)
        state = refresh_adaptive_dictionary_for_next_streams()
    return {
        "enabled": True,
        "meeting_title": title,
        "entries": [entry.to_json() for entry in entries],
        **state,
    }
# Only finalized turns enter this buffer.  It is deliberately small: context
# helps decide an ambiguous domain term, but must not turn realtime correction
# into meeting summarization.
recent_refinement_context: collections.deque[str] = collections.deque(
    maxlen=max(0, settings.sailor_context_turns)
)


def recover_phonetics(raw_text: str) -> PhoneticRecovery:
    if not settings.phonetic_recovery_enabled:
        return PhoneticRecovery(text=raw_text, replacements=())
    return phonetic_lexicon.recover(raw_text)


def format_final_transcript(text: str) -> str:
    """Format evidence for people while retaining raw ASR separately."""
    with dictionary_runtime_lock:
        protected_terms = tuple(
            entry.canonical for entry in adaptive_dictionary.active_entries()
        )
    return format_transcript_sentence(text, protected_terms=protected_terms)


def format_realtime_draft(text: str) -> str:
    """Apply presentation-only casing to an unfinished ASR hypothesis."""
    with dictionary_runtime_lock:
        protected_terms = tuple(
            entry.canonical for entry in adaptive_dictionary.active_entries()
        )
    return format_transcript_sentence(
        text,
        protected_terms=protected_terms,
        add_terminal_punctuation=False,
    )

async def _refine_text_direct(
    raw_text: str,
    *,
    recovered_text: str | None = None,
) -> str:
    """Existing free-form cleanup path, retained for baseline A/B tests."""
    phonetic_text = recovered_text or recover_phonetics(raw_text).text
    normalized_text = normalize_meeting_terms(phonetic_text)
    if not settings.enable_llm_refinement:
        return format_final_transcript(normalized_text)

    if len(normalized_text.split()) < LLM_MIN_WORDS:
        return format_final_transcript(normalized_text)

    try:
        response = await llm_client.chat.completions.create(
            model=settings.ollama_model,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Sửa chính tả và dấu câu tiếng Việt cho transcript "
                        "cuộc họp. Không tóm tắt, không thêm ý. Chỉ trả về "
                        "văn bản đã sửa."
                    ),
                },
                {
                    "role": "user",
                    "content": f"Văn bản gốc: {normalized_text}",
                },
            ],
            max_tokens=160,
            temperature=0.0,
            stop=["\n", "\n\n", "Dưới đây", "Nếu bạn"],
            extra_body={
                "keep_alive": "30m",
                "options": {
                    "num_thread": 2,
                    "num_predict": min(
                        128,
                        max(24, len(normalized_text.split()) * 2),
                    ),
                },
            },
        )
        res_text = response.choices[0].message.content.strip()

        # --- HALLUCINATION GUARD ---
        raw_words = len(normalized_text.split())
        res_words = len(res_text.split())
        if res_words > raw_words * 2 + 3 or res_words < max(1, raw_words // 2):
            print(f"   [!] LLM Hallucination Guard bị kích hoạt ({res_words} từ vs {raw_words} từ gốc). Fallback về văn bản gốc.")
            return format_final_transcript(normalized_text)

        raw_tokens = set(
            re.findall(r"\w+", normalized_text.lower(), flags=re.UNICODE)
        )
        refined_tokens = set(
            re.findall(r"\w+", res_text.lower(), flags=re.UNICODE)
        )
        preserved = (
            len(raw_tokens & refined_tokens) / len(raw_tokens)
            if raw_tokens
            else 1.0
        )
        novel_tokens = refined_tokens - raw_tokens
        novel_ratio = len(novel_tokens) / max(1, len(raw_tokens))
        if preserved < 0.72 or novel_ratio > 0.22:
            print(
                "   [!] LLM content-preservation guard bị kích hoạt "
                f"(preserved={preserved:.0%}, "
                f"novel={novel_ratio:.0%}). "
                "Fallback về văn bản đã chuẩn hóa thuật ngữ."
            )
            return format_final_transcript(normalized_text)

        return res_text
    except Exception:
        return format_final_transcript(normalized_text)


def _parse_sailor_decisions(
    value: str, candidate_ids: set[int]
) -> dict[int, str]:
    """Parse closed per-candidate decisions; any invalid value is rejected."""
    parsed = {candidate_id: "REJECT" for candidate_id in candidate_ids}
    try:
        match = re.search(r"\{.*\}", value, flags=re.DOTALL)
        payload = json.loads(match.group(0) if match else value)
        seen_ids: set[int] = set()
        for item in payload.get("decisions", []):
            candidate_id = item.get("id")
            decision = str(item.get("decision", "REJECT")).upper()
            if candidate_id in seen_ids:
                # A duplicate id is ambiguous; retain the fail-closed rule.
                if candidate_id in candidate_ids:
                    parsed[candidate_id] = "REJECT"
                continue
            if candidate_id in candidate_ids:
                seen_ids.add(candidate_id)
            if candidate_id in candidate_ids and decision in {"ACCEPT", "REJECT"}:
                parsed[candidate_id] = decision
    except (json.JSONDecodeError, TypeError, ValueError, AttributeError):
        pass
    return parsed


async def _refine_text_sailor_candidate(
    raw_text: str,
    *,
    recovered_text: str | None,
    replacements: tuple[dict[str, object], ...],
    context: tuple[str, ...],
) -> tuple[str, dict[str, object]]:
    """Let Sailor accept/reject a closed G2P/dictionary correction only.

    The model never returns transcript text.  A malformed response, timeout,
    or rejection always retains the raw ASR result.  This is intentionally a
    safety gate, not another unconstrained ASR decoder.
    """
    candidate_text = normalize_meeting_terms(recovered_text or raw_text)
    raw_display = format_final_transcript(raw_text)
    metadata: dict[str, object] = {
        "backend": "sailor_candidate",
        "candidate_text": candidate_text,
        "candidate_terms": [item.get("to") for item in replacements],
        "context_turn_count": len(context),
    }
    if candidate_text.casefold() == raw_text.casefold():
        metadata["decision"] = "SKIPPED_NO_CANDIDATE"
        return raw_display, metadata

    context_text = "\n".join(f"- {item}" for item in context) or "(không có)"
    try:
        response = await llm_client.chat.completions.create(
            model=settings.sailor_model,
            messages=[
                {
                    "role": "system",
                    "content": (
                        f"Ngôn ngữ bắt buộc: {settings.sailor_language} (tiếng Việt). "
                        "Bạn là bộ kiểm tra candidate cho transcript họp tiếng Việt. "
                        "Bạn KHÔNG được viết lại transcript và KHÔNG được thêm từ. "
                        "Không dùng tiếng Anh hay bất kỳ ngôn ngữ nào khác. "
                        "Chỉ trả JSON hợp lệ: {\"decision\":\"ACCEPT\"|\"REJECT\","
                        "\"confidence\": số từ 0 đến 1}. ACCEPT chỉ khi candidate "
                        "phù hợp âm thanh ASR và ngữ cảnh; nếu không chắc chắn, REJECT."
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"<context_truoc>\n{context_text}\n</context_truoc>\n"
                        f"<raw_asr>{raw_text}</raw_asr>\n"
                        f"<candidate>{candidate_text}</candidate>\n"
                        "Chỉ chọn ACCEPT hoặc REJECT cho đúng candidate trên."
                    ),
                },
            ],
            max_tokens=32,
            temperature=0.0,
            extra_body={
                "keep_alive": settings.sailor_keep_alive,
                "options": {
                    "num_thread": max(1, settings.sailor_num_threads),
                    "num_predict": 32,
                    "temperature": 0,
                },
            },
        )
        decision, confidence = _parse_sailor_decision(
            response.choices[0].message.content or ""
        )
        metadata.update({"decision": decision, "confidence": confidence})
        if decision == "ACCEPT":
            return format_final_transcript(candidate_text), metadata
        return raw_display, metadata
    except Exception as exc:
        metadata.update({"decision": "ERROR_REJECT", "error": type(exc).__name__})
        return raw_display, metadata


async def _refine_text_sailor_candidate_batch(
    raw_text: str,
    *,
    replacements: tuple[dict[str, object], ...],
    context: tuple[str, ...],
) -> tuple[str, dict[str, object]]:
    """Ask Sailor for a decision per G2P-verified replacement.

    All candidates are sent in one closed JSON request to avoid serial LLM
    calls.  They are nevertheless applied independently and only after an
    explicit ACCEPT for the candidate's id.
    """
    raw_display = format_final_transcript(raw_text)
    proposals: list[dict[str, object]] = []
    for index, item in enumerate(replacements):
        start, end = item.get("start"), item.get("end")
        observed, replacement = item.get("from"), item.get("to")
        if not isinstance(start, int) or not isinstance(end, int):
            continue
        if not isinstance(observed, str) or not isinstance(replacement, str):
            continue
        if start < 0 or end <= start or raw_text[start:end] != observed:
            continue
        # Casing is presentation, not a semantic ASR correction. Do not burn
        # an LLM call merely to turn `LÀM WEB` into `làm web`.
        if observed.casefold() == replacement.casefold():
            continue
        proposals.append({
            "id": index,
            "start": start,
            "end": end,
            "from": observed,
            "to": replacement,
            "g2p_score": item.get("g2p_score"),
            "score": item.get("score"),
        })

    metadata: dict[str, object] = {
        "backend": "sailor_candidate_batch",
        "context_turn_count": len(context),
        "candidates": proposals,
    }
    if not proposals:
        metadata["decision"] = "SKIPPED_NO_CANDIDATE"
        return raw_display, metadata

    context_text = "\n".join(
        f"- {item[-240:]}" for item in context
    ) or "(no prior context)"
    request_candidates = [
        {"id": item["id"], "from": item["from"], "to": item["to"]}
        for item in proposals
    ]
    max_output_tokens = min(96, 16 + (8 * len(proposals)))
    try:
        response = await llm_client.chat.completions.create(
            model=settings.sailor_model,
            messages=[
                {
                    "role": "system",
                    "content": (
                        f"Required language: {settings.sailor_language} (Vietnamese). "
                        "You verify Vietnamese meeting-ASR correction candidates. "
                        "Never rewrite text, never add terms, and never use another language. "
                        "Return JSON only in this exact shape: "
                        "{\"decisions\":[{\"id\":0,\"decision\":\"ACCEPT\"|\"REJECT\"}]}. "
                        "Return one independent decision for every candidate id. "
                        "Accept only if the individual replacement is justified by raw ASR and context; otherwise reject. "
                        "Example ACCEPT: raw 'H PASE đang lỗi', candidate {\"id\":0,\"from\":\"H PASE\",\"to\":\"HBase\"} "
                        "returns {\"decisions\":[{\"id\":0,\"decision\":\"ACCEPT\"}]}. "
                        "Example REJECT: raw 'họp lúc chín giờ', candidate {\"id\":0,\"from\":\"chín\",\"to\":\"HBase\"} "
                        "returns {\"decisions\":[{\"id\":0,\"decision\":\"REJECT\"}]}."
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"<previous_context>\n{context_text}\n</previous_context>\n"
                        f"<raw_asr>{raw_text}</raw_asr>\n"
                        f"<candidates>{json.dumps(request_candidates, ensure_ascii=False)}</candidates>"
                    ),
                },
            ],
            max_tokens=max_output_tokens,
            temperature=0.0,
            # Ollama's OpenAI-compatible endpoint maps this schema to a
            # grammar.  Generic JSON mode alone allowed Sailor 1B to invent
            # a different object shape, which the fail-closed parser rejects.
            response_format={
                "type": "json_schema",
                "json_schema": {
                    "name": "candidate_decisions",
                    "strict": True,
                    "schema": {
                        "type": "object",
                        "properties": {
                            "decisions": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "id": {
                                            "type": "integer",
                                            "enum": [
                                                int(item["id"])
                                                for item in proposals
                                            ],
                                        },
                                        "decision": {
                                            "type": "string",
                                            "enum": ["ACCEPT", "REJECT"],
                                        },
                                    },
                                    "required": ["id", "decision"],
                                    "additionalProperties": False,
                                },
                            },
                        },
                        "required": ["decisions"],
                        "additionalProperties": False,
                    },
                },
            },
            extra_body={
                "keep_alive": settings.sailor_keep_alive,
                "options": {
                    "num_thread": max(1, settings.sailor_num_threads),
                    "num_predict": max_output_tokens,
                    "temperature": 0,
                },
            },
        )
        model_response = response.choices[0].message.content or ""
        # Persist a bounded response for A/B diagnostics only.  It explains
        # whether a reject came from Sailor's judgement or an invalid schema.
        metadata["model_response"] = model_response[:600]
        decisions = _parse_sailor_decisions(
            model_response,
            {int(item["id"]) for item in proposals},
        )
        accepted: list[dict[str, object]] = []
        for proposal in proposals:
            proposal["decision"] = decisions[int(proposal["id"])]
            if proposal["decision"] == "ACCEPT":
                accepted.append(proposal)
        final_text = raw_text
        for proposal in reversed(accepted):
            final_text = (
                final_text[:int(proposal["start"])]
                + str(proposal["to"])
                + final_text[int(proposal["end"]):]
            )
        metadata.update({
            "decision": "BATCHED",
            "accepted_count": len(accepted),
            "candidate_text": final_text,
        })
        return format_final_transcript(final_text), metadata
    except Exception as exc:
        metadata.update({"decision": "ERROR_REJECT", "error": type(exc).__name__})
        return raw_display, metadata


async def refine_text(
    raw_text: str,
    *,
    recovered_text: str | None = None,
    replacements: tuple[dict[str, object], ...] = (),
    context: tuple[str, ...] = (),
) -> tuple[str, dict[str, object]]:
    """Dispatch either the baseline refiner or the closed Sailor gate."""
    if not settings.enable_llm_refinement:
        return format_final_transcript(recovered_text or raw_text), {
            "backend": "disabled",
            "decision": "SKIPPED_DISABLED",
        }
    if settings.refinement_backend == "sailor_candidate":
        return await _refine_text_sailor_candidate_batch(
            raw_text,
            replacements=replacements,
            context=context,
        )
    return await _refine_text_direct(raw_text, recovered_text=recovered_text), {
        "backend": "direct",
        "decision": "FREEFORM_GUARDED",
    }

# ============================================================
# 6. FastAPI Web & WebSocket Server
# ============================================================
app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

@app.get("/")
async def get_dashboard():
    return FileResponse("static/index.html")


def _require_internal_key(x_internal_key: str | None) -> None:
    if not x_internal_key or not hmac.compare_digest(
        x_internal_key, settings.internal_api_key
    ):
        raise HTTPException(status_code=401, detail="Invalid internal API key")


@app.get("/api/adaptive-dictionary")
async def adaptive_dictionary_status(
    x_internal_key: str | None = Header(default=None),
):
    _require_internal_key(x_internal_key)
    with dictionary_runtime_lock:
        entries = adaptive_dictionary.active_entries()
        return {
            "enabled": settings.adaptive_dictionary_enabled,
            "hotword_enabled": bool(
                hotword_artifacts and hotword_artifacts.phrase_count
            ),
            "hotword_count": (
                hotword_artifacts.phrase_count if hotword_artifacts else 0
            ),
            "entries": [entry.to_json() for entry in entries],
        }


@app.post("/api/adaptive-dictionary/prepare")
async def prepare_adaptive_dictionary_api(
    payload: dict = Body(...),
    x_internal_key: str | None = Header(default=None),
):
    """Prepare the next room's glossary from an explicit meeting title."""
    _require_internal_key(x_internal_key)
    title = str(payload.get("meeting_title", ""))
    try:
        return prepare_adaptive_dictionary_for_meeting(title)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Unable to prepare adaptive dictionary: {type(exc).__name__}",
        ) from exc


@app.websocket("/ws/web_dashboard")
async def web_dashboard_websocket(websocket: WebSocket):
    await dashboard_manager.connect(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        dashboard_manager.disconnect(websocket)

async def _process_enrollment_audio(speaker_name: str, audio: np.ndarray, sr: int):
    if audio.ndim > 1: audio = audio.mean(axis=1)
    if sr != 16000:
        import torchaudio
        wv = torch.from_numpy(audio).unsqueeze(0).float()
        wv = torchaudio.transforms.Resample(sr, 16000)(wv)
        audio = wv.squeeze(0).numpy()

    enroll_vad_stream = vad_model.stream()
    pcm16 = (audio * 32768.0).astype(np.int16)
    speech_segments = []

    async def collect_speech():
        async for evt in enroll_vad_stream:
            if evt.type == VADEventType.END_OF_SPEECH and evt.frames:
                raw = b"".join(f.data.tobytes() for f in evt.frames)
                seg = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
                if len(seg) >= 8000:
                    speech_segments.append(seg)

    collector = asyncio.create_task(collect_speech())
    chunk_size = 320
    for i in range(0, len(pcm16) - chunk_size + 1, chunk_size):
        frame = rtc.AudioFrame(
            data=pcm16[i:i+chunk_size].tobytes(),
            sample_rate=16000, num_channels=1, samples_per_channel=chunk_size
        )
        enroll_vad_stream.push_frame(frame)
    enroll_vad_stream.end_input()
    try:
        await asyncio.wait_for(collector, timeout=10.0)
    except Exception:
        pass
    finally:
        try:
            await enroll_vad_stream.aclose()
        except Exception:
            pass

    clean_audio = np.concatenate(speech_segments) if speech_segments else audio

    clean_duration = len(clean_audio) / 16000
    if clean_duration < 6.0:
        return {
            "status": "error",
            "message": (
                f"Giọng nói sạch chỉ có {clean_duration:.1f}s; "
                "cần ít nhất 6s và nên đọc liên tục 20-30s"
            ),
        }

    CHUNK = 16000 * 4
    STEP = 16000 * 2
    raw_clips = []
    if len(clean_audio) < CHUNK:
        if len(clean_audio) >= 16000:
            raw_clips.append(clean_audio)
    else:
        for i in range(0, len(clean_audio) - CHUNK + 1, STEP):
            raw_clips.append(clean_audio[i : i + CHUNK])

    clip_quality = [
        (
            float(np.sqrt(np.mean(clip**2))),
            float(np.mean(np.abs(clip) >= 0.98)),
            clip,
        )
        for clip in raw_clips
    ]
    median_rms = (
        float(np.median([item[0] for item in clip_quality]))
        if clip_quality
        else 0.0
    )
    rms_floor = max(0.006, median_rms * 0.35)
    clips = [
        clip
        for rms, clipping_ratio, clip in clip_quality
        if rms >= rms_floor and clipping_ratio <= 0.03
    ]

    def extract_all_embeddings():
        with gpu_lock:
            return [extract_embedding(clip) for clip in clips]

    # WavLM is CPU/GPU-heavy. Never block FastAPI's event loop during
    # enrollment because transcript events must continue to flow.
    embeddings = (
        await asyncio.to_thread(extract_all_embeddings) if clips else []
    )

    if len(embeddings) < 3:
        return {
            "status": "error",
            "message": (
                "Không đủ cửa sổ giọng nói sạch để ghi danh; "
                "hãy kiểm tra âm lượng, tránh clipping và đọc lại 20-30s"
            ),
        }

    try:
        profile = build_enrollment_profile(embeddings)
    except ValueError as exc:
        return {"status": "error", "message": str(exc)}

    normalized_name = speaker_name.strip()
    _delete_speaker_profile(normalized_name)
    points = []
    for index, prototype in enumerate(profile.prototypes):
        point_id = str(
            uuid.uuid5(
                uuid.NAMESPACE_DNS,
                f"speaker-profile-v2:{normalized_name.casefold()}:{index}",
            )
        )
        points.append(
            PointStruct(
                id=point_id,
                vector=prototype.tolist(),
                payload={
                    "speaker_label": normalized_name,
                    "profile_version": 2,
                    "prototype_index": index,
                    "prototype_kind": (
                        "centroid" if index == 0 else "sample"
                    ),
                },
            )
        )
    qdrant.upsert("speakers", points=points, wait=True)
    print(
        f"\n   [API /enroll] + Đã đăng ký vân tay giọng nói: "
        f"{normalized_name} ({profile.retained_embeddings}/"
        f"{profile.total_embeddings} cửa sổ đạt chất lượng, "
        f"{clean_duration:.1f}s audio sạch)"
    )
    
    await dashboard_manager.broadcast_speakers()
    return {
        "status": "success",
        "speaker_name": normalized_name,
        "chunks_enrolled": profile.retained_embeddings,
        "chunks_total": profile.total_embeddings,
        "prototypes": len(profile.prototypes),
        "profile_consistency": round(
            profile.median_centroid_similarity, 4
        ),
    }

@app.post("/enroll")
async def enroll_speaker_api(speaker_name: str = Form(...), file: UploadFile = File(...)):
    contents = await file.read()
    audio, sr = sf.read(io.BytesIO(contents))
    return await _process_enrollment_audio(speaker_name, audio, sr)

@app.post("/api/quick_enroll")
async def quick_enroll_api(data: dict = Body(...)):
    speaker_name = data.get("speaker_name")
    wav_path = data.get("wav_path")
    audio, sr = sf.read(wav_path)
    res = await _process_enrollment_audio(speaker_name, audio, sr)
    if res.get("status") != "success":
        return res
    return {
        **res,
        "message": f"Đã đăng ký thành công cho {speaker_name}!",
    }

@app.post("/api/simulate")
async def simulate_api():
    print("\n[Dashboard] Khởi chạy kịch bản test_client.py từ Web UI...")
    subprocess.Popen(["python", "test_client.py"])
    return {"status": "success", "message": "Đã bắt đầu mô phỏng 2 Micro!"}

@app.websocket("/ws/{identity}")
async def websocket_endpoint(
    websocket: WebSocket,
    identity: str,
    display_name: str | None = None,
):
    await websocket.accept()
    fallback_speaker = (display_name or identity).strip() or identity
    print(f"\n[+] Đã cấp phát luồng AI cho Client: {identity}")

    # Keep a stable decoder for this microphone. Dictionary refresh may swap
    # the global recognizer for later connections, never for this stream.
    asr_recognizer = recognizer
    asr_stream = asr_recognizer.create_stream()
    vad_stream  = vad_model.stream()
    asr_lock = asyncio.Lock()

    # AudioStream is pinned to 20 ms in agent.py. Keep 800 ms by sample-time,
    # not by an assumed LiveKit packet count. Silero's START event is the
    # authoritative fallback and carries its own prefix-padded speech buffer.
    PRE_BUFFER_CHUNKS = max(
        1,
        int(round(0.8 * 1000 / settings.audio_frame_size_ms)),
    )
    pre_speech_buf = collections.deque(maxlen=PRE_BUFFER_CHUNKS)

    speech_audio_chunks: list[np.ndarray] = []
    speech_quality_observations = []
    speech_sample_count = 0
    is_speaking = False
    speech_start_time = 0.0
    current_turn_id = ""
    last_audio_timestamp = time.monotonic()
    audio_wall_time_offset: float | None = None

    def audio_timestamp_to_wall(timestamp: float) -> float:
        if audio_wall_time_offset is None:
            return time.time()
        return min(time.time(), timestamp + audio_wall_time_offset)
    
    current_speaker = fallback_speaker
    audio_queue = asyncio.Queue()
    quality_tracker = AudioQualityTracker()
    asr_preprocessor = StreamingAsrPreprocessor(
        high_pass_hz=settings.asr_high_pass_hz,
        target_rms=settings.asr_target_rms,
    )

    # The neural enhancer is per microphone because DPDFNet is stateful.
    # It is deliberately not shared with WavLM: speaker embeddings always
    # receive the original VAD-selected waveform.
    asr_enhancer = None
    if settings.asr_enhancer not in {"", "none", "off"}:
        if settings.asr_enhancer != "dpdfnet_baseline":
            raise RuntimeError(
                "ASR_ENHANCER không hỗ trợ: "
                f"{settings.asr_enhancer}"
            )
        asr_enhancer = StreamingDpdfNetEnhancer(
            model_path=str(settings.asr_enhancer_model),
            num_threads=settings.asr_enhancer_threads,
            controller=DynamicEnhancementController(
                bypass_snr_db=settings.asr_enhancer_bypass_snr_db,
                full_snr_db=settings.asr_enhancer_full_snr_db,
                maximum_mix=settings.asr_enhancer_max_mix,
                attack=settings.asr_enhancer_attack,
                release=settings.asr_enhancer_release,
            ),
        )
        print(
            f"[DPDFNet] [{identity}] enabled "
            f"(full <= {settings.asr_enhancer_full_snr_db:g} dB, "
            f"bypass >= {settings.asr_enhancer_bypass_snr_db:g} dB, "
            f"max mix {settings.asr_enhancer_max_mix:.2f})."
        )

    def prepare_asr_audio(audio, quality):
        prepared = audio
        if settings.enable_asr_preprocessing:
            prepared = asr_preprocessor.process(audio, quality=quality)
        if asr_enhancer is not None:
            prepared = asr_enhancer.process(prepared, quality=quality)
        return prepared

    def flush_asr_enhancer() -> None:
        """Deliver DPDFNet's one-frame look-ahead before final Zipformer."""
        if asr_enhancer is None:
            return
        tail = asr_enhancer.flush()
        if tail.size:
            with zipformer_inference_lock:
                asr_stream.accept_waveform(16000, tail)
                while asr_recognizer.is_ready(asr_stream):
                    asr_recognizer.decode_stream(asr_stream)

    def finalize_asr_stream_text() -> str:
        """Flush pending transducer tokens before reading a final result."""
        padding = np.zeros(
            int(settings.asr_final_padding_seconds * 16000),
            dtype=np.float32,
        )
        if padding.size:
            with zipformer_inference_lock:
                asr_stream.accept_waveform(16000, padding)
        with zipformer_inference_lock:
            while asr_recognizer.is_ready(asr_stream):
                asr_recognizer.decode_stream(asr_stream)
            result = asr_recognizer.get_result(asr_stream)
        return (
            result.text.strip()
            if hasattr(result, "text")
            else str(result).strip()
        )

    last_sent_text = ""
    last_partial_sent_at = 0.0
    last_speech_end_time = 0.0
    bg_tasks = set()

    async def asr_worker():
        nonlocal current_speaker
        nonlocal last_sent_text, last_partial_sent_at
        try:
            while True:
                first_chunk = await audio_queue.get()
                if first_chunk is None:
                    audio_queue.task_done()
                    break

                async def finish_segment(command):
                    _, future = command
                    try:
                        async with asr_lock:
                            final_text = finalize_asr_stream_text()
                            with zipformer_inference_lock:
                                asr_recognizer.reset(asr_stream)
                        if not future.done():
                            future.set_result(final_text)
                    except Exception as exc:
                        if not future.done():
                            future.set_exception(exc)
                    finally:
                        audio_queue.task_done()

                if isinstance(first_chunk, tuple):
                    await finish_segment(first_chunk)
                    continue

                chunks = [first_chunk]
                stop_after_batch = False
                finalize_command = None
                while len(chunks) < 10:
                    try:
                        next_chunk = audio_queue.get_nowait()
                    except asyncio.QueueEmpty:
                        break
                    if next_chunk is None:
                        audio_queue.task_done()
                        stop_after_batch = True
                        break
                    if isinstance(next_chunk, tuple):
                        finalize_command = next_chunk
                        break
                    chunks.append(next_chunk)

                audio_np = np.concatenate(chunks)

                def decode_step():
                    with zipformer_inference_lock:
                        asr_stream.accept_waveform(16000, audio_np)
                        while asr_recognizer.is_ready(asr_stream):
                            asr_recognizer.decode_stream(asr_stream)
                        res = asr_recognizer.get_result(asr_stream)
                    return res.text.strip() if hasattr(res, 'text') else str(res).strip()

                try:
                    async with asr_lock:
                        text = await asyncio.to_thread(decode_step)
                finally:
                    for _ in chunks:
                        audio_queue.task_done()

                now = time.time()

                if (
                    text
                    and text != last_sent_text
                    and now - last_partial_sent_at >= 0.25
                ):
                    last_sent_text = text
                    last_partial_sent_at = now
                    partial_msg = {
                        "partial": format_realtime_draft(text),
                        "identity": identity,
                        "speaker": current_speaker,
                        "identity_method": "mic_fallback",
                        "speaker_confidence": None,
                        "ts": time.time()
                    }
                    try:
                        await websocket.send_text(json.dumps(partial_msg, ensure_ascii=False))
                        await dashboard_manager.broadcast(partial_msg)
                    except Exception:
                        pass
                if finalize_command is not None:
                    await finish_segment(finalize_command)
                if stop_after_batch:
                    break
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            print(f"[!] Lỗi ASR worker [{identity}]: {exc}")

    async def process_final_minute_bg(
        audio_snapshot: list[np.ndarray],
        quality_snapshot: list,
        raw_text: str,
        start_ts: float,
        end_ts: float,
        turn_id: str,
    ):
        if len(raw_text) < 4:
            return

        final_pipeline_started = time.perf_counter()
        phonetic_result = recover_phonetics(raw_text)
        # Final transcript remains evidence.  Qwen is reserved for the
        # backend Minutes Composer, never for per-segment ASR rewriting.
        transcript_text = format_final_transcript(
            phonetic_result.text or raw_text
        )
        refinement_metadata: dict[str, object] = {
            "backend": "disabled_for_minutes_composer",
            "decision": "SOURCE_TRANSCRIPT_ONLY",
        }

        speaker_name = fallback_speaker
        identity_method = "mic_fallback"
        speaker_confidence = None
        speaker_margin = None
        speaker_consensus = None
        speaker_id_ms = None
        signal_rms = 0.0
        quality_summary = summarize_quality(quality_snapshot)
        if audio_snapshot:
            # Speaker ID deliberately receives the original VAD-selected
            # waveform. ASR enhancement must never alter WavLM embeddings.
            audio_for_id = np.concatenate(audio_snapshot)
            duration_s = len(audio_for_id) / 16000
            signal_rms = float(np.sqrt(np.mean(audio_for_id**2)))
            # Audio sample count is stable even when CPU load delays asyncio
            # scheduling. Wall-clock VAD callbacks otherwise under-report
            # utterance duration during a multi-mic stress test.
            start_ts = end_ts - duration_s

            candidate_id = uuid.uuid4().hex
            should_process = await room_timeline.select_final(
                FinalCandidate(
                    candidate_id=candidate_id,
                    turn_id=turn_id,
                    source_id=identity,
                    raw_text=raw_text,
                    start_time=start_ts,
                    end_time=end_ts,
                    quality=quality_summary,
                    created_at=time.monotonic(),
                    fingerprint=speech_envelope(audio_for_id),
                )
            )
            if not should_process:
                print(
                    f"   [Timeline {turn_id}] Bỏ bản sao từ {identity}; "
                    "mic khác có SNR/chất lượng tốt hơn."
                )
                return
            # Realtime speaker checks yield only for final WavLM identity.
            # Qwen is intentionally deferred to the backend minutes queue.
            heavy_work.mark_final()
            print(f"   [WavLM] Đoạn audio nhận diện: {duration_s:.2f}s")

            enrolled_points = qdrant.count(
                collection_name="speakers", exact=True
            ).count
            if enrolled_points == 0:
                print(
                    f"   [WavLM] Chưa có profile; dùng tên mic "
                    f"{fallback_speaker}"
                )
            elif duration_s >= settings.speaker_min_id_seconds:
                def run_wavlm(audio=audio_for_id):
                    return recognize_speaker_open_set(audio)

                speaker_id_started = time.perf_counter()
                decision = await heavy_work.run_final_thread(run_wavlm)
                speaker_id_ms = round(
                    (time.perf_counter() - speaker_id_started) * 1000
                )
                score_text = (
                    f"{decision.score:.3f}"
                    if decision.score is not None
                    else "n/a"
                )
                margin_text = (
                    f"{decision.margin:.3f}"
                    if decision.margin is not None
                    else "n/a"
                )
                print(
                    f"   [WavLM OpenSet] result={decision.reason} "
                    f"score={score_text} required="
                    f"{decision.required_score:.3f} margin={margin_text} "
                    f"consensus={decision.consensus:.2f}"
                )
                if decision.accepted and decision.label:
                    speaker_name = decision.label
                    identity_method = "voice_profile"
                    speaker_confidence = round(decision.score, 4)
                    speaker_margin = (
                        round(decision.margin, 4)
                        if decision.margin is not None
                        else None
                    )
                    speaker_consensus = round(decision.consensus, 4)
                else:
                    print(
                        f"   [WavLM OpenSet] Không đủ chắc chắn; "
                        f"fallback về {fallback_speaker}"
                    )
            else:
                print(
                    f"   [WavLM] Audio < "
                    f"{settings.speaker_min_id_seconds:.1f}s; "
                    f"fallback về {fallback_speaker}"
                )
        async def emit_payload(message: dict) -> None:
            try:
                await dashboard_manager.broadcast(message)
            except Exception as exc:
                print(f"Lỗi broadcast Dashboard: {exc}")
            try:
                await websocket.send_text(
                    json.dumps(message, ensure_ascii=False)
                )
            except Exception:
                pass

        utterance_id = uuid.uuid4().hex
        refinement_pending = False
        refinement_ms = 0

        payload = {
            "utterance_id": utterance_id,
            "identity": identity,
            "speaker":  speaker_name,
            "identity_method": identity_method,
            "speaker_confidence": speaker_confidence,
            "speaker_margin": speaker_margin,
            "speaker_consensus": speaker_consensus,
            "speaker_id_ms": speaker_id_ms,
            "text":     transcript_text,
            "raw_text": raw_text,
            "phonetic_recovered_text": phonetic_result.text,
            "phonetic_recovery_applied": bool(phonetic_result.replacements),
            "phonetic_replacements": list(phonetic_result.replacements),
            "refinement": refinement_metadata,
            "start_time": round(start_ts, 2),
            "end_time":   round(end_ts, 2),
            "refinement_ms": refinement_ms,
            "pipeline_ms": round(
                (time.perf_counter() - final_pipeline_started) * 1000
            ),
            "signal_rms": round(signal_rms, 6),
            "signal_snr_db": round(quality_summary.snr_db, 2),
            "clipping_ratio": round(
                quality_summary.clipping_ratio, 5
            ),
            "global_turn_id": turn_id,
            "refinement_pending": refinement_pending,
            "revision": 1,
        }
        await emit_payload(payload)

        print(f"[✅ Đã xuất Biên Bản cho {identity}]")

    async def process_vad_events():
        nonlocal is_speaking, speech_start_time, speech_audio_chunks
        nonlocal speech_quality_observations, speech_sample_count
        nonlocal last_sent_text, last_speech_end_time, current_speaker
        nonlocal current_turn_id
        try:
            async for evt in vad_stream:
                if evt.type == VADEventType.START_OF_SPEECH:
                    is_speaking = True
                    speech_start_time = audio_timestamp_to_wall(
                        last_audio_timestamp
                    )
                    current_turn_id = room_timeline.speech_started(
                        identity, timestamp=last_audio_timestamp
                    )
                    last_sent_text = ""
                    current_speaker = fallback_speaker
                    print(
                        f"\n[🎙️ VAD] [{identity}] BẮT ĐẦU NÓI "
                        f"({current_turn_id})..."
                    )

                    # Silero already retains the exact prefix-padded speech
                    # buffer that triggered START. Prefer it over a packet
                    # count assumption; the latter can lose the onset of fast
                    # utterances when LiveKit changes frame duration.
                    event_chunks = [
                        audio_frame_to_float(frame)
                        for frame in (evt.frames or [])
                    ]
                    event_chunks = [
                        chunk for chunk in event_chunks if chunk.size
                    ]
                    async with asr_lock:
                        with zipformer_inference_lock:
                            asr_recognizer.reset(asr_stream)
                        asr_preprocessor.reset()
                        if asr_enhancer is not None:
                            asr_enhancer.reset()
                        buffered = (
                            [
                                (
                                    chunk,
                                    quality_tracker.measure(
                                        chunk, speech_active=True
                                    ),
                                    last_audio_timestamp,
                                )
                                for chunk in event_chunks
                            ]
                            if event_chunks
                            else list(pre_speech_buf)
                        )
                        # Preserve the same prefix that is fed to ASR so
                        # duration, speaker embedding and transcript all
                        # describe the same complete utterance.
                        speech_audio_chunks = [
                            item[0] for item in buffered
                        ]
                        speech_quality_observations = [
                            item[1] for item in buffered
                        ]
                        # Pre-roll belongs to the snapshot/ASR, but not to
                        # the soft-split live-turn counter.
                        speech_sample_count = 0
                        for chunk, quality, timestamp in buffered:
                            prepared = prepare_asr_audio(chunk, quality)
                            if prepared.size:
                                with zipformer_inference_lock:
                                    asr_stream.accept_waveform(
                                        16000, prepared
                                    )

                elif (
                    evt.type == VADEventType.INFERENCE_DONE
                    and is_speaking
                    and speech_sample_count
                    >= settings.asr_soft_split_seconds * 16000
                    and (
                        evt.raw_accumulated_silence
                        >= settings.asr_split_min_silence_seconds
                        or speech_sample_count
                        >= settings.asr_hard_split_seconds * 16000
                    )
                ):
                    # Prefer a short observed pause as the split point. A
                    # longer hard limit prevents an unbroken stream from
                    # growing forever, without resetting at 15 seconds in the
                    # middle of a word.
                    split_reason = (
                        "pause"
                        if evt.raw_accumulated_silence
                        >= settings.asr_split_min_silence_seconds
                        else "hard_limit"
                    )
                    print(
                        f"   [ASR split] [{identity}] reason={split_reason} "
                        f"speech={speech_sample_count / 16000:.2f}s"
                    )
                    speech_end_time = audio_timestamp_to_wall(
                        last_audio_timestamp
                    )
                    # END_OF_SPEECH carries Silero's complete buffered speech
                    # (including prefix padding). It is more reliable for
                    # speaker ID than the wall-clock packet list, which may
                    # contain several seconds of endpointing silence.
                    end_event_chunks = [
                        audio_frame_to_float(frame)
                        for frame in (evt.frames or [])
                    ]
                    end_event_chunks = [
                        chunk for chunk in end_event_chunks if chunk.size
                    ]
                    captured_chunks = list(speech_audio_chunks)
                    audio_snapshot = (
                        end_event_chunks
                        if audio_chunk_sample_count(end_event_chunks)
                        > audio_chunk_sample_count(captured_chunks)
                        else captured_chunks
                    )
                    quality_snapshot = list(
                        speech_quality_observations
                    )
                    speech_audio_chunks = []
                    speech_quality_observations = []
                    speech_sample_count = 0

                    future = asyncio.get_running_loop().create_future()
                    audio_queue.put_nowait(("finalize", future))
                    raw_text = await future

                    t = asyncio.create_task(
                        process_final_minute_bg(
                            audio_snapshot,
                            quality_snapshot,
                            raw_text,
                            speech_start_time,
                            speech_end_time,
                            current_turn_id,
                        )
                    )
                    bg_tasks.add(t)
                    t.add_done_callback(bg_tasks.discard)

                    current_turn_id = room_timeline.split_turn(
                        identity, timestamp=last_audio_timestamp
                    )
                    speech_start_time = speech_end_time
                    last_sent_text = ""
                    current_speaker = fallback_speaker

                elif evt.type == VADEventType.END_OF_SPEECH:
                    is_speaking = False
                    speech_end_time = audio_timestamp_to_wall(
                        last_audio_timestamp
                    )
                    ended_turn_id = room_timeline.speech_ended(
                        identity, timestamp=last_audio_timestamp
                    )
                    last_speech_end_time = speech_end_time
                    last_sent_text = ""
                    pre_speech_buf.clear()
                    print(
                        f"\n[🛑 VAD] [{identity}] ĐÃ NGỪNG NÓI "
                        f"({ended_turn_id})."
                    )

                    end_event_chunks = [
                        audio_frame_to_float(frame)
                        for frame in (evt.frames or [])
                    ]
                    end_event_chunks = [
                        chunk for chunk in end_event_chunks if chunk.size
                    ]
                    captured_chunks = list(speech_audio_chunks)
                    audio_snapshot = (
                        end_event_chunks
                        if audio_chunk_sample_count(end_event_chunks)
                        > audio_chunk_sample_count(captured_chunks)
                        else captured_chunks
                    )
                    quality_snapshot = list(
                        speech_quality_observations
                    )
                    speech_audio_chunks = []
                    speech_quality_observations = []
                    speech_sample_count = 0

                    # Do not read the final recognizer state until every
                    # accepted speech chunk has been decoded.
                    await audio_queue.join()
                    async with asr_lock:
                        flush_asr_enhancer()
                        raw_text = finalize_asr_stream_text()
                        if asr_enhancer is not None:
                            telemetry = asr_enhancer.telemetry()
                            print(
                                f"[DPDFNet] [{identity}] "
                                f"{telemetry.processed_seconds:.1f}s, "
                                f"mix avg={telemetry.average_mix:.2f}, "
                                f"peak={telemetry.peak_mix:.2f}."
                            )

                    # Bật tác vụ ngầm chạy WavLM + LLM để luồng VAD không bao giờ bị nghẽn hay khựng!
                    t = asyncio.create_task(
                        process_final_minute_bg(
                            audio_snapshot,
                            quality_snapshot,
                            raw_text,
                            speech_start_time,
                            speech_end_time,
                            ended_turn_id,
                        )
                    )
                    bg_tasks.add(t)
                    t.add_done_callback(bg_tasks.discard)

        except asyncio.CancelledError:
            pass
        except Exception as e:
            print(f"[!] Lỗi VAD event loop [{identity}]: {e}")

    vad_task = asyncio.create_task(process_vad_events())
    asr_task = asyncio.create_task(asr_worker())

    audio_batch_buf = bytearray()
    last_voice_time = time.time()

    try:
        while True:
            packet = await websocket.receive_bytes()
            data, sequence, captured_at = unpack_audio_packet(packet)
            last_audio_timestamp = captured_at
            if audio_wall_time_offset is None:
                audio_wall_time_offset = time.time() - captured_at
            if not data or len(data) % 2:
                print(
                    f"[audio] Bỏ frame PCM không hợp lệ từ {identity}: "
                    f"{len(data)} bytes"
                )
                continue
            
            # Gom đệm 50ms âm thanh (1600 bytes) rồi mới broadcast về Web UI để stream mượt mượt không giật
            audio_batch_buf.extend(data)
            if len(audio_batch_buf) >= 1600:
                chunk_to_send = bytes(audio_batch_buf)
                audio_batch_buf.clear()
                asyncio.create_task(dashboard_manager.broadcast_audio(identity, chunk_to_send))

            audio_np = np.frombuffer(data, dtype=np.int16).astype(np.float32) / 32768.0
            frame_quality = quality_tracker.measure(
                audio_np, speech_active=is_speaking
            )
            room_timeline.note_frame(
                identity,
                timestamp=captured_at,
                quality=frame_quality,
                sequence=sequence,
            )

            # Feed continuous audio into Silero. A frame-level RMS gate and
            # four-second cooldown used to cut quiet syllables and the start
            # of the next speaker. Silero already performs the proper
            # temporal speech/noise decision.
            if not is_speaking:
                pre_speech_buf.append(
                    (audio_np, frame_quality, captured_at)
                )

            frame = rtc.AudioFrame(
                data=data, sample_rate=16000, num_channels=1,
                samples_per_channel=len(data) // 2
            )
            vad_stream.push_frame(frame)

            if is_speaking:
                # Branch 1 (Speaker ID): retain the original VAD-selected
                # waveform. Only reject windows later for silence/clipping.
                speech_audio_chunks.append(audio_np)
                speech_quality_observations.append(frame_quality)
                speech_sample_count += len(audio_np)

                # Branch 2 (ASR): decode every mic continuously by default.
                # Selecting a winner independently per frame creates holes in
                # a fast utterance when two microphones trade loudness. The
                # room timeline still chooses/retracts the strongest final
                # candidate; this switch only controls CPU-saving routing.
                if (
                    settings.asr_decode_all_mics
                    or room_timeline.should_route_asr(
                        identity, timestamp=captured_at
                    )
                ):
                    prepared = prepare_asr_audio(
                        audio_np, frame_quality
                    )
                    if prepared.size:
                        audio_queue.put_nowait(prepared)

    except WebSocketDisconnect:
        print(f"\n[-] Client {identity} đã ngắt kết nối.")
    except Exception as e:
        print(f"\n[!] Lỗi [{identity}]: {e}")
    finally:
        try:
            if is_speaking:
                vad_stream.end_input()
                await asyncio.wait_for(vad_task, timeout=2.0)
        except Exception:
            pass

        # Chờ các tác vụ ngầm xuất Biên bản (bg_tasks) hoàn tất xuất 100% trước khi đóng luồng
        if bg_tasks:
            try:
                await asyncio.wait_for(asyncio.gather(*list(bg_tasks), return_exceptions=True), timeout=7.0)
            except Exception:
                pass

        audio_queue.put_nowait(None)
        try:
            await asyncio.wait_for(asr_task, timeout=1.0)
        except Exception:
            asr_task.cancel()

        vad_task.cancel()
        await asyncio.gather(vad_task, asr_task, return_exceptions=True)
        try:
            await vad_stream.aclose()
        except Exception:
            pass

if __name__ == "__main__":
    import os
    import uvicorn
    port = int(os.getenv("AI_SERVER_PORT", "8001"))
    print(f"\n🚀 Khởi chạy AI pipeline tại http://localhost:{port} ...")
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")
