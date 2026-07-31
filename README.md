# Paperless Meeting Demo

Prototype “phòng họp không giấy” cho một phòng họp duy nhất. Hệ thống nhận
audio từ nhiều microphone/laptop, tạo transcript theo timeline và tổng hợp
biên bản cuộc họp. Video không nằm trong phạm vi prototype hiện tại.

## Mục tiêu và trạng thái hiện tại

- Chủ trì tạo phòng và chia sẻ mã phòng cho thành viên.
- Thành viên tham gia bằng trình duyệt qua HTTPS/WSS.
- Audio realtime đi qua LiveKit; backend AI chạy trên Ubuntu WSL.
- Speaker ID dùng WavLM/Qdrant; enrollment là tùy chọn.
- ASR chính dùng Zipformer Streaming 30M.
- Phonetic recovery và adaptive dictionary hỗ trợ các thuật ngữ chuyên môn.
- Transcript realtime được giữ làm bằng chứng. Minutes Composer chạy hậu kỳ
  theo timeline; có thể dùng Qwen2.5:3B ở mode `llm`, còn mode `timeline` là
  đường an toàn không để model nhỏ tự suy diễn nội dung.
- DPDFNet được tích hợp như nhánh A/B tùy chọn. Benchmark hiện tại chưa cho
  thấy WER tốt hơn khi bật DPDFNet, vì vậy mặc định đang tắt.

Đây là prototype phục vụ demo và nghiên cứu. Chất lượng ASR phụ thuộc mạnh vào
microphone, khoảng cách, tiếng vọng, tiếng nói chồng lấn và thuật ngữ cuộc họp.

## Kiến trúc tổng quát

```text
Browser/laptop microphone
        │  WebSocket audio + LiveKit
        ▼
Nginx/LiveKit trên home server
        │  Tailscale
        ▼
Ubuntu WSL
  ├─ backend API :8000
  │    ├─ tạo/tham gia phòng
  │    ├─ lưu transcript và biên bản SQLite
  │    └─ queue Minutes Composer
  ├─ ai_server :8001
  │    ├─ Silero VAD và global-turn timeline
  │    ├─ Zipformer ASR trên nhiều mic
  │    ├─ WavLM + Qdrant speaker identification
  │    ├─ phonetic recovery / adaptive dictionary
  │    └─ DPDFNet/GTCRN guarded tùy chọn cho nhánh ASR
  └─ agent.py
       └─ LiveKit worker chuyển audio về ai_server
```

Hai nhánh audio được tách riêng:

```text
Audio gốc → VAD → WavLM → Speaker ID
Audio gốc → ASR preprocessing → (guarded enhancer tùy chọn) → Zipformer
          → transcript
```

Audio dùng cho Speaker ID không đi qua DPDFNet để tránh làm thay đổi đặc trưng
giọng nói.

## Cấu trúc thư mục

```text
agent.py                         LiveKit worker
ai_server.py                     VAD, ASR, speaker ID, timeline
backend/api/                     REST API, SQLite, minutes queue
backend/audio_pipeline.py        preprocessing và guarded enhancer adapter
backend/text_refinement.py       dictionary và phonetic recovery
frontend/                        giao diện web demo
scripts/run_demo.sh              khởi chạy toàn bộ backend trên WSL
scripts/evaluate_asr.py          benchmark ASR offline
scripts/streaming_regression.py  kiểm thử LiveKit end-to-end
tests/                           unit test và probe dual-mic
audio/                           audio fixture và truth.csv
```

## Runtime WSL

Source code có thể nằm trên ổ Windows, nhưng runtime và model nên nằm trên
filesystem Linux để giảm độ trễ I/O:

```text
/mnt/d/VNPT/Code/Multi_Speaker_Streaming  source code
/home/ntd/meeting_runtime/venv_linux      Python environment
/home/ntd/meeting_runtime/Zipformer-...    Zipformer model
/home/ntd/meeting_runtime/models           DPDFNet và model phụ trợ
/home/ntd/meeting_runtime/data             SQLite/Qdrant/dictionary state
```

Mọi lệnh Python của project nên dùng:

```bash
/home/ntd/meeting_runtime/venv_linux/bin/python
```

## Cấu hình nhanh

Trong WSL:

```bash
cd /mnt/d/VNPT/Code/Multi_Speaker_Streaming
cp .env.example .env
```

Điền tối thiểu các biến:

```dotenv
LIVEKIT_URL=wss://livekit.example.com
LIVEKIT_API_KEY=<api-key>
LIVEKIT_API_SECRET=<api-secret>
INTERNAL_API_KEY=<random-secret>
MEETING_ROOM=paperless-demo
MEETING_CODE=DEMO-001

MINUTES_COMPOSER_ENABLED=true
# timeline = biên bản theo transcript; llm = tổng hợp bằng Qwen sau final turn
MINUTES_COMPOSER_MODE=timeline
MINUTES_COMPOSER_MODEL=qwen2.5:3b
MINUTES_COMPOSER_NUM_THREADS=12

ASR_FRONTEND=legacy
ASR_ENHANCER=none
ZIPFORMER_CHUNK_SIZE=32
ZIPFORMER_MAX_ACTIVE_PATHS=4
ZIPFORMER_BLANK_PENALTY=0.4

ADAPTIVE_DICTIONARY_ENABLED=true
ADAPTIVE_DICTIONARY_MANUAL_PATH=/home/ntd/meeting_runtime/data/meeting_lexicon.txt
TOPIC_DISCOVERY_ENABLED=true
TOPIC_DISCOVERY_MODEL=qwen2.5:3b
TOPIC_DISCOVERY_BOOTSTRAP_SECONDS=90
TOPIC_DISCOVERY_REFRESH_SECONDS=60
ZIPFORMER_HOTWORDS_ENABLED=true
```

Không lưu mật khẩu sudo hoặc secret thật vào README, source code hay file được
commit. `.env` đã được loại khỏi Git.

## Chạy demo

Đảm bảo Ollama đã có model nếu bật `MINUTES_COMPOSER_MODE=llm`:

```bash
ollama pull qwen2.5:3b
```

Khởi chạy:

```bash
cd /mnt/d/VNPT/Code/Multi_Speaker_Streaming
bash scripts/run_demo.sh
```

Các service local:

```text
Web/API : http://127.0.0.1:8000
AI      : http://127.0.0.1:8001
```

Frontend chính được phục vụ từ `frontend/` qua backend API. AI server port 8001
chỉ cung cấp pipeline và readiness API; không còn phụ thuộc vào thư mục
dashboard tĩnh riêng.

Kiểm tra nhanh:

```bash
curl http://127.0.0.1:8000/api/health
curl http://127.0.0.1:8001/
```

Dừng an toàn bằng `Ctrl+C` trong terminal chạy `run_demo.sh`. Script sẽ gửi
SIGTERM cho API, AI pipeline và LiveKit worker, chờ flush dữ liệu rồi mới buộc
dừng tiến trình còn sót.

## Speaker enrollment

Enrollment không bắt buộc. Người dùng có thể đọc đoạn văn mẫu khoảng 20–30
giây; audio được xử lý qua VAD và WavLM rồi lưu embedding vào Qdrant. Khi chưa
enroll, hệ thống dùng tên đăng nhập/mic làm fallback và không được tự ý gán
người nói vào một voice profile gần giống nếu độ tin cậy không đủ.

## Dictionary thích nghi theo nội dung

Người tạo phòng không cần nhập tên/chủ đề cuộc họp. Khi tạo phiên mới, AI xóa
term tự sinh của phiên trước và đăng ký tên hiển thị của các thành viên làm
proper-name candidate. Tên một từ như `An`, `Long`, `Đạt` không được đưa thẳng
vào decoder hotword để tránh bias từ phổ thông; chúng vẫn có thể được dùng bởi
phonetic recovery.

Trong 90 giây đầu, hệ thống chỉ lưu raw Zipformer transcript của mic thắng mỗi
global turn. Sau tối thiểu 6 turn, Qwen suy ra nhãn chủ đề và đề xuất term dưới
dạng JSON. Một term chỉ được nhận khi alias quan sát xuất hiện nguyên văn trong
ít nhất 2 global turn; snapshot được áp dụng cho Zipformer và phonetic recovery
ở đầu turn tiếp theo. Cứ 60 giây hệ thống đánh giá lại để theo kịp việc đổi chủ
đề từ cửa sổ rolling 180 giây, còn term tự sinh hết hạn sau TTL.

File do người vận hành chỉnh tay có ưu tiên cao nhất và không bị pipeline ghi
đè:

```bash
cp config/meeting_lexicon.example.txt \
  /home/ntd/meeting_runtime/data/meeting_lexicon.txt
```

Sau khi sửa file trong lúc hệ thống đang chạy, reload tại ranh giới turn an
toàn:

```bash
curl -X POST http://127.0.0.1:8001/api/adaptive-dictionary/reload \
  -H "X-Internal-Key: $INTERNAL_API_KEY"
```

## Guarded speech enhancement

Enhancer chỉ tác động lên nhánh ASR và chạy stateful theo từng microphone.
WavLM luôn nhận audio gốc. Frontend thử nghiệm hỗ trợ DPDFNet hoặc GTCRN:

```dotenv
ASR_FRONTEND=dpdfnet
ASR_ENHANCER_MODEL_TYPE=dpdfnet
ASR_ENHANCER_MODEL=/home/ntd/meeting_runtime/models/dpdfnet_baseline.onnx
ASR_ENHANCER_THREADS=1
ASR_ENHANCER_ALIGNMENT_DELAY_MS=40
ASR_PRESERVATION_MAX_SPEECH_MIX=0.10
```

Luồng tương ứng:

```text
raw 16-kHz audio ───────────────┐
        └→ DPDFNet/GTCRN ───────┤
                                └→ delay alignment
                                   + voice-preservation gate
                 → DC-block 20 Hz + gain chậm có giới hạn + peak limiter
                 → Zipformer
```

Gate kiểm tra correlation, tỷ lệ năng lượng, dải thoại 1–4 kHz và clipping trên
hai waveform đã căn thời gian. Frame làm mất giọng sẽ dùng raw; frame đạt gate
chỉ được trộn tối đa 10%. DPDFNet baseline hiện được bù 40 ms, GTCRN dùng 0 ms.

Benchmark hiện tại:

| Frontend | WER clean | WER noisy | RTF noisy |
|---|---:|---:|---:|
| legacy | 8,26% | 21,82% | 0,061 |
| guarded DPDFNet baseline | 7,54% | 22,93% | 0,158 |
| guarded GTCRN | 8,26% | 21,82% | 0,133 |

DPDFNet4 không tăng độ chính xác nhưng RTF lên khoảng 0,33. Streaming regression
cũng chưa cho thấy hai enhancer vượt `legacy`, nên `ASR_FRONTEND=legacy` vẫn là
mặc định. Nhánh guarded được giữ để A/B trên dữ liệu phòng thật; nó không giải
quyết overlap speech hoặc tách người nói giữa các mic.

## Kiểm thử

Unit test:

```bash
/home/ntd/meeting_runtime/venv_linux/bin/python -m unittest discover -s tests
```

Benchmark Zipformer:

```bash
/home/ntd/meeting_runtime/venv_linux/bin/python -B scripts/evaluate_asr.py \
  --frontend legacy --mode light --enhancer none --postprocess phonetic \
  --chunk-size 32 --blank-penalty 0.4 --final-padding-seconds 0.66
```

So sánh frontend DPDFNet guarded:

```bash
/home/ntd/meeting_runtime/venv_linux/bin/python -B scripts/evaluate_asr.py \
  --frontend dpdfnet --mode raw --enhancer none --postprocess phonetic \
  --chunk-size 32 --blank-penalty 0.4 --final-padding-seconds 0.66
```

So sánh GTCRN mà không sửa `.env`:

```bash
/home/ntd/meeting_runtime/venv_linux/bin/python -B scripts/evaluate_asr.py \
  --frontend dpdfnet --denoiser-model-type gtcrn \
  --denoiser-model /home/ntd/meeting_runtime/models/gtcrn_simple.onnx \
  --alignment-delay-ms 0 --mode raw --enhancer none \
  --postprocess phonetic
```

Streaming regression, yêu cầu backend đang chạy:

```bash
/home/ntd/meeting_runtime/venv_linux/bin/python -B \
  scripts/streaming_regression.py \
  --output tmp/streaming_regression_latest.json
```

Regression kiểm tra transcript final, timestamp, global turn, speaker identity,
cross-mic duplicate và WER/CER theo `audio/truth.csv`.

Hướng dẫn triển khai và benchmark chi tiết nằm trong
[Huong_Dan_Chay_Test.md](Huong_Dan_Chay_Test.md).

## Triển khai public

Mô hình demo được hỗ trợ:

```text
Laptop người dùng
  → HTTPS/WSS Nginx trên home server
  → LiveKit public
  → Tailscale
  → backend và AI pipeline trên WSL laptop
```

Nginx cần proxy `/api/` và `/ws/` đến backend WSL qua địa chỉ Tailscale, đồng
thời phục vụ frontend tĩnh. LiveKit dùng domain/WSS riêng và cần mở các port
được cấu hình trên home server/router.

## Giới hạn và hướng tiếp theo

- Prototype chỉ có một phòng họp.
- Chưa có video conference.
- ASR chưa đạt độ chính xác production với nói nhanh hoặc overlap mạnh.
- Qwen chỉ nên tổng hợp biên bản từ transcript đã lưu, không dùng để rewrite
  từng câu realtime một cách tự do.
- Bước tiếp theo là benchmark topic-derived hotword trên nhiều chủ đề phòng
  thật và đánh giá riêng chất lượng biên bản.
