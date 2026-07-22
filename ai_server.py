import asyncio
import collections
import time
import json
import threading
import torch
import sherpa_onnx
import numpy as np
import io
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
    num_threads=2, sample_rate=16000, feature_dim=80,
    decoding_method='modified_beam_search', max_active_paths=4, provider='cpu'
)

# ============================================================
# 2. WavLM – Speaker Embedding
# ============================================================
print("2. Đang nạp mô hình WavLM...")
DEVICE = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
wavlm_extractor = Wav2Vec2FeatureExtractor.from_pretrained('microsoft/wavlm-base-sv')
wavlm_model = WavLMForXVector.from_pretrained('microsoft/wavlm-base-sv').to(DEVICE)
wavlm_model.eval()

# ============================================================
# 3. Silero VAD
# ============================================================
print("3. Đang nạp VAD (Silero VAD)...")
vad_model = silero.VAD.load(
    min_speech_duration=0.3,        # Bắt từ ngắn/ấp úng (0.3s)
    min_silence_duration=1.2,       # Đợi đủ 1.2s để phù hợp với người nói chậm/ngập ngừng
    prefix_padding_duration=0.5,    
    activation_threshold=0.45,      # Nhạy hơn với giọng nói nhỏ
    deactivation_threshold=0.25,    # Không ngắt câu khi đang nói ngập ngừng
)

# ============================================================
# 4. Qdrant Vector Database
# ============================================================
print("4. Khởi tạo Qdrant Vector Database...")
qdrant = QdrantClient(':memory:')
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
    results = qdrant.query_points(
        collection_name='speakers',
        query=[0.0] * 512,
        limit=10,
        with_vectors=True
    )
    if len(results.points) < 2:
        return 0.80

    vecs = {p.payload['speaker_label']: np.array(p.vector) for p in results.points if p.vector is not None}
    labels = list(vecs.keys())
    if len(labels) < 2:
        return 0.80

    max_sim = 0.0
    for i in range(len(labels)):
        for j in range(i + 1, len(labels)):
            sim = float(np.dot(vecs[labels[i]], vecs[labels[j]]))
            if sim > max_sim:
                max_sim = sim

    # Ngưỡng thích ứng: Nếu 2 giọng trong DB giống nhau -> đẩy ngưỡng lên cao để chống nhầm người
    return max(0.75, min(0.92, max_sim + 0.04))

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

        # Nếu Mic khác đang phát biểu to vượt trội (max_other > 0.02 và rms < max_other * 0.50): Chặn lọt âm!
        if max_other_rms > 0.02 and rms < (max_other_rms * 0.50):
            return False
        return True

voice_focus_engine = CrossMicVoiceFocus()

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

    async def broadcast_speakers(self):
        results = qdrant.query_points(
            collection_name='speakers',
            query=[0.0] * 512,
            limit=10,
            with_vectors=True
        )
        speakers = [p.payload['speaker_label'] for p in results.points]
        sim_str = None
        if len(results.points) >= 2:
            vecs = {p.payload['speaker_label']: np.array(p.vector) for p in results.points if p.vector is not None}
            labels = list(vecs.keys())
            if len(labels) >= 2:
                sim_str = f"{float(np.dot(vecs[labels[0]], vecs[labels[1]])):.4f}"

        await self.broadcast({
            "type": "enrolled_speakers",
            "speakers": list(set(speakers)),
            "similarity": sim_str
        })

dashboard_manager = WebDashboardManager()
global_minute_history = []

# ============================================================
# 5. LLM – Ollama Qwen2.5
# ============================================================
llm_client = AsyncOpenAI(base_url="http://localhost:11434/v1", api_key="ollama")
LLM_MIN_WORDS = 5  # Câu quá ngắn thì bỏ qua LLM, tránh hallucination

async def refine_text(raw_text: str) -> str:
    """Hiệu đính văn bản ASR bằng LLM với few-shot instruction và Hallucination Guard."""
    if len(raw_text.split()) < LLM_MIN_WORDS:
        return raw_text.capitalize()

    try:
        response = await llm_client.chat.completions.create(
            model="qwen2.5:1.5b",
            messages=[
                {"role": "system", "content":
                    "Bạn là công cụ sửa lỗi chính tả văn bản tiếng Việt. "
                    "Nhiệm vụ: Sửa lỗi chính tả, viết hoa đầu câu, thêm dấu chấm câu và chuẩn hóa thuật ngữ (như HBase, Architecture, HD, Microservice, Web, API). "
                    "QUY TẮC BẮT BUỘC:\n"
                    "1. TUYỆT ĐỐI KHÔNG giải thích, KHÔNG trả lời câu hỏi, KHÔNG bịa thêm ý mới.\n"
                    "2. CHỈ trả về đúng văn bản đã sửa lỗi."},
                {"role": "user",    "content": "Văn bản gốc: thì ở đây một năm chấm hai họ nói về cái lĩnh vực andropteris gồm có hpase"},
                {"role": "assistant","content": "Thì ở đây mục 1.2 họ nói về cái lĩnh vực Architecture gồm có HBase."},
                {"role": "user",    "content": "Văn bản gốc: rồi thì ở đây năm chấm hai họ nói về cái lớp đầu tiên"},
                {"role": "assistant","content": "Rồi thì ở đây mục 5.2 họ nói về cái lớp đầu tiên."},
                {"role": "user",    "content": f"Văn bản gốc: {raw_text}"},
            ],
            max_tokens=256,
            temperature=0.0,
            stop=["\n\n", "\n1.", "\n-", "Dưới đây", "Nếu bạn"]
        )
        res_text = response.choices[0].message.content.strip()

        # --- HALLUCINATION GUARD ---
        raw_words = len(raw_text.split())
        res_words = len(res_text.split())
        if res_words > raw_words * 2 + 3 or res_words < max(1, raw_words // 2):
            print(f"   [!] LLM Hallucination Guard bị kích hoạt ({res_words} từ vs {raw_words} từ gốc). Fallback về văn bản gốc.")
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

    clean_audio = np.concatenate(speech_segments) if speech_segments else audio

    CHUNK = 16000 * 3
    STEP  = int(16000 * 1.5)
    embeddings = []

    if len(clean_audio) < CHUNK:
        if len(clean_audio) >= 16000:
            with gpu_lock:
                embeddings.append(extract_embedding(clean_audio))
    else:
        for i in range(0, len(clean_audio) - CHUNK + 1, STEP):
            with gpu_lock:
                embeddings.append(extract_embedding(clean_audio[i:i+CHUNK]))

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

    PRE_BUFFER_CHUNKS = 25
    pre_speech_buf = collections.deque(maxlen=PRE_BUFFER_CHUNKS)

    speech_audio_chunks: list[np.ndarray] = []
    is_speaking = False
    speech_start_time = 0.0
    
    current_speaker = identity
    last_speaker_check = 0.0
    audio_queue = asyncio.Queue()
    adaptive_noise = AdaptiveNoiseFloor()

    last_sent_text = ""

    async def asr_worker():
        nonlocal current_speaker, last_speaker_check, last_sent_text
        try:
            while True:
                audio_np = await audio_queue.get()
                asr_stream.accept_waveform(16000, audio_np)

                def decode_step():
                    while recognizer.is_ready(asr_stream):
                        recognizer.decode_stream(asr_stream)
                    res = recognizer.get_result(asr_stream)
                    return res.text.strip() if hasattr(res, 'text') else str(res).strip()

                text = await asyncio.to_thread(decode_step)

                # Thỉnh thoảng kiểm tra WavLM nhận diện tên người nói ngay khi đang stream nháp
                now = time.time()
                if is_speaking and (now - last_speaker_check > 1.5) and speech_audio_chunks:
                    audio_clip = np.concatenate(list(speech_audio_chunks))
                    if len(audio_clip) >= 24000:
                        last_speaker_check = now
                        def quick_id(clip=audio_clip[:16000*5]):
                            with gpu_lock:
                                emb = extract_embedding(clip)
                            res = qdrant.query_points(collection_name='speakers', query=emb.tolist(), limit=1)
                            if res.points and res.points[0].score >= 0.82:
                                return res.points[0].payload['speaker_label']
                            return identity
                        current_speaker = await asyncio.to_thread(quick_id)

                if text and text != last_sent_text:
                    last_sent_text = text
                    partial_msg = {
                        "partial": text,
                        "identity": identity,
                        "speaker": current_speaker
                    }
                    try:
                        await websocket.send_text(json.dumps(partial_msg, ensure_ascii=False))
                        await dashboard_manager.broadcast(partial_msg)
                    except Exception:
                        pass
        except asyncio.CancelledError:
            pass
        except Exception:
            pass

    async def process_vad_events():
        nonlocal is_speaking, speech_start_time, speech_audio_chunks, last_sent_text
        try:
            async for evt in vad_stream:
                if evt.type == VADEventType.START_OF_SPEECH:
                    is_speaking = True
                    speech_start_time = time.time()
                    last_sent_text = ""
                    print(f"\n[🎙️ VAD] [{identity}] BẮT ĐẦU NÓI...")

                    recognizer.reset(asr_stream)
                    speech_audio_chunks = []

                    for chunk in list(pre_speech_buf):
                        asr_stream.accept_waveform(16000, chunk)

                elif evt.type == VADEventType.END_OF_SPEECH:
                    is_speaking = False
                    speech_end_time = time.time()
                    last_sent_text = ""
                    print(f"\n[🛑 VAD] [{identity}] ĐÃ NGỪNG NÓI.")

                    audio_snapshot = list(speech_audio_chunks)
                    speech_audio_chunks = []

                    asr_res  = recognizer.get_result(asr_stream)
                    raw_text = asr_res.text.strip() if hasattr(asr_res, 'text') else str(asr_res).strip()

                    if len(raw_text) < 4:
                        continue

                    speaker_name = identity
                    if audio_snapshot:
                        audio_for_id = np.concatenate(audio_snapshot)
                        duration_s = len(audio_for_id) / 16000
                        print(f"   [WavLM] Đoạn audio nhận diện: {duration_s:.2f}s")

                        if duration_s >= 1.5:
                            def run_wavlm(audio=audio_for_id):
                                # Slide a 4-second window to find peak RMS voice energy
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
                                if res.points:
                                    best_match = res.points[0]
                                    second_score = res.points[1].score if len(res.points) > 1 else 0.0
                                    print(f"   [WavLM] Top 1: {best_match.payload['speaker_label']} ({best_match.score:.3f}) | Top 2: ({second_score:.3f})")
                                    
                                    dyn_thresh = calculate_dynamic_speaker_threshold()
                                    if best_match.score >= dyn_thresh:
                                        return best_match.payload['speaker_label']
                                
                                print(f"   [WavLM] Không đạt ngưỡng tin cậy thích ứng >= {dyn_thresh:.3f} -> Bỏ qua đoạn rác/nhiễu")
                                return None
                            speaker_name = await asyncio.to_thread(run_wavlm)
                        else:
                            print(f"   [WavLM] Bỏ qua nhận diện (audio quá ngắn < 1.5s)")

                    if not speaker_name:
                        print(f"   [WavLM] Bỏ qua câu không rõ định danh ({identity})")
                        continue

                    refined = await refine_text(raw_text)

                    # --- DEDUPLICATION CHECK: Loại bỏ câu trùng từ Crosstalk giữa các Mic ---
                    now = time.time()
                    is_dup = False
                    for item in list(global_minute_history):
                        if item["speaker"] == speaker_name and (now - item["time"]) < 5.0:
                            # Kiểm tra độ trùng lặp văn bản
                            w1 = set(refined.lower().split())
                            w2 = set(item["text"].lower().split())
                            if len(w1) > 0 and len(w1 & w2) / len(w1) > 0.5:
                                is_dup = True
                                break
                    if is_dup:
                        print(f"   [Deduplication] Bỏ qua câu trùng lặp từ Crosstalk ({speaker_name}: {identity})")
                        continue

                    global_minute_history.append({"speaker": speaker_name, "text": refined, "time": now})
                    if len(global_minute_history) > 50:
                        global_minute_history.pop(0)

                    payload = {
                        "identity": identity,
                        "speaker":  speaker_name,
                        "text":     refined,
                        "raw_text": raw_text,
                        "start_time": round(speech_start_time, 2),
                        "end_time":   round(speech_end_time, 2),
                    }
                    
                    # Gửi tới Agent và đồng thời Broadcast lên Web Dashboard
                    try:
                        await websocket.send_text(json.dumps(payload, ensure_ascii=False))
                        await dashboard_manager.broadcast(payload)
                        print(f"[✅ Đã gửi Biên Bản cho {identity}]")
                    except Exception as e:
                        print(f"Lỗi gửi: {e}")
        except asyncio.CancelledError:
            pass
        except Exception as e:
            print(f"[!] Lỗi VAD event loop [{identity}]: {e}")

    vad_task = asyncio.create_task(process_vad_events())
    asr_task = asyncio.create_task(asr_worker())

    try:
        while True:
            data = await websocket.receive_bytes()
            audio_np = np.frombuffer(data, dtype=np.int16).astype(np.float32) / 32768.0

            # Adaptive Dynamic Noise Gate: Tự động thích ứng với tiếng ồn phông môi trường
            rms = float(np.sqrt(np.mean(audio_np**2)))
            dynamic_gate = adaptive_noise.get_dynamic_gate(audio_np, is_speaking)
            if rms < dynamic_gate and not is_speaking:
                continue

            # DYNAMIC VOICE FOCUS: So sánh độ lớn âm thanh thời gian thực giữa các Mic trong phòng
            is_focused = voice_focus_engine.update_and_check_focus(identity, audio_np)
            if not is_focused:
                if is_speaking:
                    # Mic khác đã cướp quyền phát biểu chính -> Ngắt VAD lập tức để đóng câu!
                    vad_stream.end_input()
                    vad_stream = vad_model.stream()
                    is_speaking = False
                continue

            if not is_speaking:
                pre_speech_buf.append(audio_np)

            frame = rtc.AudioFrame(
                data=data, sample_rate=16000, num_channels=1,
                samples_per_channel=len(data) // 2
            )
            vad_stream.push_frame(frame)

            if is_speaking:
                speech_audio_chunks.append(audio_np)
                audio_queue.put_nowait(audio_np)

    except WebSocketDisconnect:
        print(f"\n[-] Client {identity} đã ngắt kết nối.")
    except Exception as e:
        print(f"\n[!] Lỗi [{identity}]: {e}")
    finally:
        try:
            vad_stream.end_input()
        except Exception:
            pass
        vad_task.cancel()
        asr_task.cancel()
        await asyncio.gather(vad_task, asr_task, return_exceptions=True)

if __name__ == "__main__":
    import uvicorn
    print("\n🚀 Khởi chạy AI Server & Web Dashboard tại http://localhost:8000 ...")
    uvicorn.run(app, host="0.0.0.0", port=8000, log_level="warning")
