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
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, UploadFile, File, Form, Body
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
from backend.config import settings

print("Bắt đầu khởi tạo các mô hình AI...")

# ============================================================
# 1. ASR – Zipformer
# ============================================================
print("1. Đang nạp mô hình ASR (Zipformer)...")
asr_dir = 'Zipformer-30M-RNNT-Streaming-6000h'
recognizer = sherpa_onnx.OnlineRecognizer.from_transducer(
    tokens=f'{asr_dir}/config.json',
    encoder=f'{asr_dir}/encoder-epoch-31-avg-11-chunk-16-left-128.fp16.onnx',
    decoder=f'{asr_dir}/decoder-epoch-31-avg-11-chunk-16-left-128.fp16.onnx',
    joiner=f'{asr_dir}/joiner-epoch-31-avg-11-chunk-16-left-128.fp16.onnx',
    num_threads=1, sample_rate=16000, feature_dim=80,
    decoding_method='modified_beam_search', max_active_paths=4, provider='cpu'
)

# ============================================================
# 2. WavLM – Speaker Embedding
# ============================================================
print("2. Đang nạp mô hình WavLM...")
DEVICE = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
if DEVICE.type == "cpu":
    # Leave CPU capacity for Zipformer and the Ollama process.
    torch.set_num_threads(1)
wavlm_extractor = Wav2Vec2FeatureExtractor.from_pretrained('microsoft/wavlm-base-sv')
wavlm_model = WavLMForXVector.from_pretrained('microsoft/wavlm-base-sv').to(DEVICE)
wavlm_model.eval()

# ============================================================
# 3. Silero VAD
# ============================================================
print("3. Đang nạp VAD (Silero VAD)...")
vad_model = silero.VAD.load(
    min_speech_duration=0.3,        # Bắt từ ngắn/ấp úng (0.3s)
    min_silence_duration=4.0,       # Giữ khoảng nghỉ nội bộ; soft boundary 15s vẫn chốt lượt dài
    prefix_padding_duration=0.5,    
    activation_threshold=0.55,      # Ngưỡng kích hoạt 0.55 chống nhận diện nhầm tiếng thở/nhiễu mic
    deactivation_threshold=0.30,    # Ngưỡng ngắt mượt cho cuộc họp
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

# ============================================================
# DYNAMIC ADAPTIVE CONFIGURATION ENGINE
# ============================================================

def calculate_dynamic_speaker_threshold() -> float:
    """Tự động tính ngưỡng Cosine Similarity dựa trên khoảng cách giữa các vector diễn giả trong Qdrant."""
    points, _ = qdrant.scroll(
        collection_name='speakers',
        limit=10,
        with_vectors=True
    )
    if len(points) < 2:
        return 0.78

    vecs = {p.payload['speaker_label']: np.array(p.vector) for p in points if p.vector is not None}
    labels = list(vecs.keys())
    if len(labels) < 2:
        return 0.78

    max_sim = 0.0
    for i in range(len(labels)):
        for j in range(i + 1, len(labels)):
            sim = float(np.dot(vecs[labels[i]], vecs[labels[j]]))
            if sim > max_sim:
                max_sim = sim

    # Ngưỡng thích ứng an toàn: Bounded [0.75, 0.82]
    return max(0.75, min(0.82, max_sim + 0.01))

class AdaptiveNoiseFloor:
    """Tự động thích ứng với độ ồn phông môi trường theo thời gian thực (Dynamic RMS Noise Gate)."""
    def __init__(self):
        self.buffer = collections.deque(maxlen=40)

    def get_dynamic_gate(self, audio_np: np.ndarray, is_speaking: bool) -> float:
        rms = float(np.sqrt(np.mean(audio_np**2)))
        if not is_speaking:
            self.buffer.append(rms)
        avg_noise = float(np.mean(self.buffer)) if self.buffer else 0.005
        return max(0.008, min(0.035, avg_noise * 2.5))

class CrossMicVoiceFocus:
    """
    DYNAMIC VOICE FOCUS: So sánh độ lớn giọng nói (RMS) giữa tất cả các Micro trong thời gian thực.
    - Mic có RMS vượt trội -> Giữ lại (Focus).
    - Mic bị lọt âm từ Mic khác (RMS_this < RMS_max_other * 0.6) -> Chặn hoàn toàn không cho vào VAD.
    - Cả 2 Mic đều to xấp xỉ nhau (Nói chồng) -> Cho phép cả 2 cùng xử lý.
    """
    def __init__(self):
        self.mic_rms_state = {}

    def update_and_check_focus(self, identity: str, audio_np: np.ndarray) -> bool:
        now = time.time()
        rms = float(np.sqrt(np.mean(audio_np**2)))
        self.mic_rms_state[identity] = (rms, now)

        max_other_rms = 0.0
        for mic_id, (other_rms, ts) in list(self.mic_rms_state.items()):
            if mic_id != identity and (now - ts) < 0.4:
                if other_rms > max_other_rms:
                    max_other_rms = other_rms

        # Nếu Mic khác đang phát biểu to vượt trội (max_other > 0.020 và rms < max_other * 0.40): Chặn lọt âm!
        if max_other_rms > 0.020 and rms < (max_other_rms * 0.40):
            return False
        return True

import base64

voice_focus_engine = CrossMicVoiceFocus()


class CrossMicFinalArbiter:
    """Select the strongest mic before expensive WavLM/Qwen finalization."""

    def __init__(self):
        self.lock = asyncio.Lock()
        self.candidates = {}

    @staticmethod
    def _similarity(left: str, right: str) -> float:
        left_words = set(re.findall(r"\w+", left.lower(), flags=re.UNICODE))
        right_words = set(re.findall(r"\w+", right.lower(), flags=re.UNICODE))
        if not left_words or not right_words:
            return 0.0
        return len(left_words & right_words) / min(
            len(left_words), len(right_words)
        )

    @staticmethod
    def _overlaps(left: dict, right: dict) -> bool:
        return (
            min(left["end_time"], right["end_time"])
            - max(left["start_time"], right["start_time"])
            >= 1.0
        )

    async def should_process(self, candidate: dict) -> bool:
        async with self.lock:
            self.candidates[candidate["id"]] = candidate

        # Parallel VAD streams normally close within a few hundred ms.
        await asyncio.sleep(0.75)

        async with self.lock:
            current = self.candidates.get(candidate["id"])
            if current is None:
                return False
            if current.get("winner_id") is not None:
                return current["winner_id"] == candidate["id"]

            group = [
                item
                for item in self.candidates.values()
                if item.get("winner_id") is None
                and self._overlaps(candidate, item)
                and self._similarity(
                    candidate["raw_text"], item["raw_text"]
                )
                >= 0.62
            ]
            if not group:
                # Very short utterances may not satisfy the normal overlap
                # window even when compared with themselves.
                group = [candidate]
            winner = max(group, key=lambda item: item["signal_rms"])
            for item in group:
                item["winner_id"] = winner["id"]

            cutoff = time.time() - 10
            self.candidates = {
                key: item
                for key, item in self.candidates.items()
                if item["created_at"] >= cutoff
            }
            return winner["id"] == candidate["id"]


final_arbiter = CrossMicFinalArbiter()


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
        points, _ = qdrant.scroll(
            collection_name='speakers',
            limit=10,
            with_vectors=True
        )
        speakers = [p.payload['speaker_label'] for p in points]
        sim_str = None
        if len(points) >= 2:
            vecs = {p.payload['speaker_label']: np.array(p.vector) for p in points if p.vector is not None}
            labels = list(vecs.keys())
            if len(labels) >= 2:
                sim_str = f"{float(np.dot(vecs[labels[0]], vecs[labels[1]])):.4f}"

        await self.broadcast({
            "type": "enrolled_speakers",
            "speakers": list(set(speakers)),
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
LLM_MIN_WORDS = 5  # Câu quá ngắn thì bỏ qua LLM, tránh hallucination

async def refine_text(raw_text: str) -> str:
    """Hiệu đính văn bản ASR bằng LLM với few-shot instruction và Hallucination Guard."""
    if not settings.enable_llm_refinement:
        return raw_text.capitalize()

    if len(raw_text.split()) < LLM_MIN_WORDS:
        return raw_text.capitalize()

    try:
        response = await llm_client.chat.completions.create(
            model=settings.ollama_model,
            messages=[
                {"role": "system", "content": "Sửa lỗi chính tả tiếng Việt cuộc họp. Chuẩn hóa từ ASR nghe nhầm: 'lồng quét' -> 'làm web', 'aptoris' -> 'Architecture', 'hpase' -> 'HBase', 'hd' -> 'HD'. KHÔNG tóm tắt. CHỈ trả về đúng văn bản đã sửa."},
                {"role": "user", "content": "Văn bản gốc: lồng quét này nọ làm hệ thống web lồng quét aptoris gồm hpase"},
                {"role": "assistant", "content": "Làm Web này nọ làm hệ thống web, làm web Architecture gồm HBase."},
                {"role": "user", "content": f"Văn bản gốc: {raw_text}"},
            ],
            max_tokens=256,
            temperature=0.0,
            stop=["\n", "\n\n", "Dưới đây", "Nếu bạn"],
            extra_body={
                "keep_alive": "30m",
                "options": {
                    "num_thread": 2,
                    "num_predict": min(
                        160, max(48, len(raw_text.split()) * 3)
                    ),
                },
            },
        )
        res_text = response.choices[0].message.content.strip()

        # --- HALLUCINATION GUARD ---
        raw_words = len(raw_text.split())
        res_words = len(res_text.split())
        if res_words > raw_words * 2 + 3 or res_words < max(1, raw_words // 2):
            print(f"   [!] LLM Hallucination Guard bị kích hoạt ({res_words} từ vs {raw_words} từ gốc). Fallback về văn bản gốc.")
            return raw_text.capitalize()

        raw_tokens = set(
            re.findall(r"\w+", raw_text.lower(), flags=re.UNICODE)
        )
        refined_tokens = set(
            re.findall(r"\w+", res_text.lower(), flags=re.UNICODE)
        )
        preserved = (
            len(raw_tokens & refined_tokens) / len(raw_tokens)
            if raw_tokens
            else 1.0
        )
        if preserved < 0.58:
            print(
                "   [!] LLM content-preservation guard bị kích hoạt "
                f"({preserved:.0%}). Fallback về văn bản gốc."
            )
            return raw_text.capitalize()

        return res_text
    except Exception:
        return raw_text

# ============================================================
# 6. FastAPI Web & WebSocket Server
# ============================================================
app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

@app.get("/")
async def get_dashboard():
    return FileResponse("static/index.html")

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

    CHUNK = 16000 * 4
    STEP  = int(16000 * 2.0)
    clips = []
    if len(clean_audio) < CHUNK:
        if len(clean_audio) >= 16000:
            clips.append(clean_audio)
    else:
        for i in range(0, len(clean_audio) - CHUNK + 1, STEP):
            clips.append(clean_audio[i:i+CHUNK])

    def extract_all_embeddings():
        with gpu_lock:
            return [extract_embedding(clip) for clip in clips]

    # WavLM is CPU/GPU-heavy. Never block FastAPI's event loop during
    # enrollment because transcript events must continue to flow.
    embeddings = (
        await asyncio.to_thread(extract_all_embeddings) if clips else []
    )

    if not embeddings:
        return {"status": "error", "message": f"Audio file for {speaker_name} is too short"}

    mean_emb = np.mean(embeddings, axis=0)
    mean_emb /= np.linalg.norm(mean_emb)
    
    # Dùng uuid5 để tạo ID cố định dựa trên tên diễn giả (Ghi đè nếu enroll lại)
    point_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, speaker_name))
    point = PointStruct(id=point_id, vector=mean_emb.tolist(), payload={'speaker_label': speaker_name})
    qdrant.upsert('speakers', points=[point])
    print(f"\n   [API /enroll] + Đã đăng ký vân tay giọng nói: {speaker_name} ({len(embeddings)} chunk mẫu từ {len(clean_audio)/16000:.1f}s audio sạch)")
    
    await dashboard_manager.broadcast_speakers()
    return {"status": "success", "speaker_name": speaker_name, "chunks_enrolled": len(embeddings)}

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
    return {"status": "success", "message": f"Đã đăng ký thành công cho {speaker_name}!"}

@app.post("/api/simulate")
async def simulate_api():
    print("\n[Dashboard] Khởi chạy kịch bản test_client.py từ Web UI...")
    subprocess.Popen(["python", "test_client.py"])
    return {"status": "success", "message": "Đã bắt đầu mô phỏng 2 Micro!"}

@app.websocket("/ws/{identity}")
async def websocket_endpoint(websocket: WebSocket, identity: str):
    await websocket.accept()
    print(f"\n[+] Đã cấp phát luồng AI cho Client: {identity}")

    asr_stream = recognizer.create_stream()
    vad_stream  = vad_model.stream()
    asr_lock = asyncio.Lock()

    # rtc.AudioStream currently yields ~100 ms frames. Keep about 500 ms
    # pre-roll; the old value 40 duplicated roughly four seconds of audio.
    PRE_BUFFER_CHUNKS = 5
    pre_speech_buf = collections.deque(maxlen=PRE_BUFFER_CHUNKS)

    speech_audio_chunks: list[np.ndarray] = []
    speech_sample_count = 0
    is_speaking = False
    speech_start_time = 0.0
    
    current_speaker = identity
    last_speaker_check = 0.0
    speaker_checked_this_turn = False
    audio_queue = asyncio.Queue()
    adaptive_noise = AdaptiveNoiseFloor()

    last_sent_text = ""
    last_partial_sent_at = 0.0
    last_speech_end_time = 0.0
    bg_tasks = set()

    async def asr_worker():
        nonlocal current_speaker, last_speaker_check
        nonlocal speaker_checked_this_turn
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
                            result = recognizer.get_result(asr_stream)
                            final_text = (
                                result.text.strip()
                                if hasattr(result, "text")
                                else str(result).strip()
                            )
                            recognizer.reset(asr_stream)
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
                    asr_stream.accept_waveform(16000, audio_np)
                    while recognizer.is_ready(asr_stream):
                        recognizer.decode_stream(asr_stream)
                    res = recognizer.get_result(asr_stream)
                    return res.text.strip() if hasattr(res, 'text') else str(res).strip()

                try:
                    async with asr_lock:
                        text = await asyncio.to_thread(decode_step)
                finally:
                    for _ in chunks:
                        audio_queue.task_done()

                # Thỉnh thoảng kiểm tra WavLM nhận diện tên người nói ngay khi đang stream nháp
                now = time.time()
                if (
                    is_speaking
                    and not speaker_checked_this_turn
                    and (now - speech_start_time >= 4.0)
                    and (now - last_speaker_check >= 0.5)
                    and speech_audio_chunks
                    and qdrant.count(
                        collection_name="speakers", exact=True
                    ).count > 0
                ):
                    audio_clip = np.concatenate(list(speech_audio_chunks))
                    if len(audio_clip) >= 24000:
                        last_speaker_check = now
                        def quick_id(clip=audio_clip[:16000*5]):
                            with gpu_lock:
                                emb = extract_embedding(clip)
                            res = qdrant.query_points(
                                collection_name='speakers',
                                query=emb.tolist(),
                                limit=2,
                            )
                            if not res.points:
                                return identity
                            best = res.points[0]
                            second_score = (
                                res.points[1].score
                                if len(res.points) > 1
                                else 0.0
                            )
                            threshold = min(
                                calculate_dynamic_speaker_threshold(),
                                0.80,
                            )
                            if (
                                best.score >= threshold
                                and best.score - second_score >= 0.008
                            ):
                                return best.payload['speaker_label']
                            return identity
                        ran, detected_speaker = await heavy_work.run_quick(
                            quick_id
                        )
                        if ran:
                            speaker_checked_this_turn = True
                            current_speaker = detected_speaker

                if (
                    text
                    and text != last_sent_text
                    and now - last_partial_sent_at >= 0.25
                ):
                    last_sent_text = text
                    last_partial_sent_at = now
                    partial_msg = {
                        "partial": text,
                        "identity": identity,
                        "speaker": current_speaker,
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

    async def process_final_minute_bg(audio_snapshot: list[np.ndarray], raw_text: str, start_ts: float, end_ts: float):
        if len(raw_text) < 4:
            return

        final_pipeline_started = time.perf_counter()

        speaker_name = identity
        signal_rms = 0.0
        if audio_snapshot:
            audio_for_id = np.concatenate(audio_snapshot)
            duration_s = len(audio_for_id) / 16000
            signal_rms = float(np.sqrt(np.mean(audio_for_id**2)))
            # Audio sample count is stable even when CPU load delays asyncio
            # scheduling. Wall-clock VAD callbacks otherwise under-report
            # utterance duration during a multi-mic stress test.
            start_ts = end_ts - duration_s

            candidate_id = uuid.uuid4().hex
            should_process = await final_arbiter.should_process({
                "id": candidate_id,
                "identity": identity,
                "raw_text": raw_text,
                "start_time": start_ts,
                "end_time": end_ts,
                "signal_rms": signal_rms,
                "created_at": time.time(),
            })
            if not should_process:
                print(
                    f"   [VoiceFocus Final] Bỏ bản sao từ {identity}; "
                    "một mic khác có tín hiệu rõ hơn."
                )
                return
            # Realtime speaker checks now yield until this final has passed
            # both WavLM identification and Qwen refinement.
            heavy_work.mark_final()
            print(f"   [WavLM] Đoạn audio nhận diện: {duration_s:.2f}s")

            enrolled_speakers = qdrant.count(
                collection_name="speakers", exact=True
            ).count

            if enrolled_speakers == 0:
                # Before enrollment the LiveKit bridge will map this channel
                # identity back to the participant display name. Avoid an
                # expensive WavLM pass that cannot possibly find a profile.
                speaker_name = identity
            elif duration_s >= 1.2:
                def run_wavlm(audio=audio_for_id):
                    target_len = 16000 * 4
                    if len(audio) > target_len:
                        step = 16000 // 2
                        best_start = 0
                        max_rms = 0.0
                        for start in range(0, len(audio) - target_len, step):
                            win = audio[start:start+target_len]
                            rms = float(np.sqrt(np.mean(win**2)))
                            if rms > max_rms:
                                max_rms = rms
                                best_start = start
                        clip = audio[best_start:best_start+target_len]
                    else:
                        clip = audio

                    with gpu_lock:
                        emb = extract_embedding(clip)
                    res = qdrant.query_points(
                        collection_name='speakers',
                        query=emb.tolist(),
                        limit=10
                    )
                    # Enrollment is optional for the first demo pass. The
                    # bridge maps this channel identity back to display_name.
                    if not res.points:
                        return identity

                    best_match = res.points[0]
                    second_score = res.points[1].score if len(res.points) > 1 else 0.0
                    print(f"   [WavLM] Top 1: {best_match.payload['speaker_label']} ({best_match.score:.3f}) | Top 2: ({second_score:.3f})")

                    top1_label = best_match.payload['speaker_label']

                    dyn_thresh = calculate_dynamic_speaker_threshold()
                    if (
                        best_match.score >= min(dyn_thresh, 0.80)
                        and best_match.score - second_score >= 0.008
                    ):
                        return top1_label

                    print(f"   [WavLM] Không đạt ngưỡng tin cậy thích ứng >= {dyn_thresh:.3f} -> Bỏ qua đoạn rác/nhiễu")
                    return None
                speaker_name = await heavy_work.run_final_thread(run_wavlm)
            else:
                print(f"   [WavLM] Bỏ qua nhận diện (audio quá ngắn < 1.2s)")

        if not speaker_name:
            # An uncertain label must never make meeting content disappear.
            # It can be corrected by the chair or a later refinement pass.
            speaker_name = "Chưa xác định"
            print(f"   [WavLM] Giữ nội dung với nhãn chưa xác định ({identity})")

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
        refinement_started = time.perf_counter()
        refinement_task = asyncio.create_task(
            heavy_work.run_final_async(lambda: refine_text(raw_text))
        )
        done, _ = await asyncio.wait(
            {refinement_task}, timeout=4.0
        )
        refinement_pending = not done
        if refinement_pending:
            refined = raw_text.capitalize()
            refinement_ms = None
        else:
            refined = refinement_task.result()
            refinement_ms = round(
                (time.perf_counter() - refinement_started) * 1000
            )

        payload = {
            "utterance_id": utterance_id,
            "identity": identity,
            "speaker":  speaker_name,
            "text":     refined,
            "raw_text": raw_text,
            "start_time": round(start_ts, 2),
            "end_time":   round(end_ts, 2),
            "refinement_ms": refinement_ms,
            "pipeline_ms": round(
                (time.perf_counter() - final_pipeline_started) * 1000
            ),
            "signal_rms": round(signal_rms, 6),
            "refinement_pending": refinement_pending,
            "revision": 1,
        }
        await emit_payload(payload)

        print(f"[✅ Đã xuất Biên Bản cho {identity}]")

        if refinement_pending:
            async def publish_late_refinement() -> None:
                try:
                    final_text = await refinement_task
                    update = dict(payload)
                    update.update({
                        "text": final_text,
                        "refinement_ms": round(
                            (
                                time.perf_counter()
                                - refinement_started
                            )
                            * 1000
                        ),
                        "refinement_pending": False,
                        "is_refinement_update": True,
                        "revision": 2,
                    })
                    await emit_payload(update)
                except Exception as exc:
                    print(f"[!] Lỗi cập nhật LLM muộn: {exc}")

            late_task = asyncio.create_task(publish_late_refinement())
            bg_tasks.add(late_task)
            late_task.add_done_callback(bg_tasks.discard)

    async def process_vad_events():
        nonlocal is_speaking, speech_start_time, speech_audio_chunks
        nonlocal speech_sample_count
        nonlocal last_sent_text, last_speech_end_time, current_speaker
        nonlocal speaker_checked_this_turn
        try:
            async for evt in vad_stream:
                if evt.type == VADEventType.START_OF_SPEECH:
                    is_speaking = True
                    speech_start_time = time.time()
                    last_sent_text = ""
                    current_speaker = identity
                    speaker_checked_this_turn = False
                    print(f"\n[🎙️ VAD] [{identity}] BẮT ĐẦU NÓI...")

                    async with asr_lock:
                        recognizer.reset(asr_stream)
                        # Preserve the same prefix that is fed to ASR so
                        # duration, speaker embedding and transcript all
                        # describe the same complete utterance.
                        speech_audio_chunks = list(pre_speech_buf)
                        # Pre-roll belongs to the snapshot/ASR, but not to
                        # the 15-second live-turn counter.
                        speech_sample_count = 0
                        for chunk in list(pre_speech_buf):
                            asr_stream.accept_waveform(16000, chunk)

                elif (
                    evt.type == VADEventType.INFERENCE_DONE
                    and is_speaking
                    and speech_sample_count >= 15 * 16000
                ):
                    # Bound a continuous turn to roughly 15 seconds. The
                    # queue marker finalizes everything before it, then
                    # resets ASR before later frames are decoded.
                    speech_end_time = time.time()
                    audio_snapshot = list(speech_audio_chunks)
                    speech_audio_chunks = []
                    speech_sample_count = 0

                    future = asyncio.get_running_loop().create_future()
                    audio_queue.put_nowait(("finalize", future))
                    raw_text = await future

                    t = asyncio.create_task(
                        process_final_minute_bg(
                            audio_snapshot,
                            raw_text,
                            speech_start_time,
                            speech_end_time,
                        )
                    )
                    bg_tasks.add(t)
                    t.add_done_callback(bg_tasks.discard)

                    speech_start_time = speech_end_time
                    last_sent_text = ""
                    current_speaker = identity
                    speaker_checked_this_turn = False

                elif evt.type == VADEventType.END_OF_SPEECH:
                    is_speaking = False
                    speech_end_time = time.time()
                    last_speech_end_time = speech_end_time
                    last_sent_text = ""
                    pre_speech_buf.clear()
                    print(f"\n[🛑 VAD] [{identity}] ĐÃ NGỪNG NÓI.")

                    audio_snapshot = list(speech_audio_chunks)
                    speech_audio_chunks = []
                    speech_sample_count = 0

                    # Do not read the final recognizer state until every
                    # accepted speech chunk has been decoded.
                    await audio_queue.join()
                    async with asr_lock:
                        asr_res  = recognizer.get_result(asr_stream)
                        raw_text = asr_res.text.strip() if hasattr(asr_res, 'text') else str(asr_res).strip()

                    # Bật tác vụ ngầm chạy WavLM + LLM để luồng VAD không bao giờ bị nghẽn hay khựng!
                    t = asyncio.create_task(process_final_minute_bg(audio_snapshot, raw_text, speech_start_time, speech_end_time))
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
            data = await websocket.receive_bytes()
            now = time.time()
            
            # Gom đệm 50ms âm thanh (1600 bytes) rồi mới broadcast về Web UI để stream mượt mượt không giật
            audio_batch_buf.extend(data)
            if len(audio_batch_buf) >= 1600:
                chunk_to_send = bytes(audio_batch_buf)
                audio_batch_buf.clear()
                asyncio.create_task(dashboard_manager.broadcast_audio(identity, chunk_to_send))

            audio_np = np.frombuffer(data, dtype=np.int16).astype(np.float32) / 32768.0

            # Always refresh RMS, including while this channel is speaking.
            # Otherwise its state expires after 0.4s and another mic can be
            # falsely activated by leakage.
            # Keep every channel through VAD. Per-frame arrival order is not
            # synchronized across WebSockets, so dropping a channel here can
            # discard the actual near-field mic. CrossMicFinalArbiter makes
            # the reliable decision from complete, time-aligned utterances.
            voice_focus_engine.update_and_check_focus(identity, audio_np)

            # Feed continuous audio into Silero. A frame-level RMS gate and
            # four-second cooldown used to cut quiet syllables and the start
            # of the next speaker. Silero already performs the proper
            # temporal speech/noise decision.
            if not is_speaking:
                pre_speech_buf.append(audio_np)

            frame = rtc.AudioFrame(
                data=data, sample_rate=16000, num_channels=1,
                samples_per_channel=len(data) // 2
            )
            vad_stream.push_frame(frame)

            if is_speaking:
                speech_audio_chunks.append(audio_np)
                speech_sample_count += len(audio_np)
                audio_queue.put_nowait(audio_np)

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
