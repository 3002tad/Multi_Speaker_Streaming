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
- Transcript realtime được giữ làm bằng chứng; Qwen2.5:3B xử lý biên bản ở
  bước hậu kỳ theo timeline, không chặn đường ASR.
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
  │    └─ DPDFNet tùy chọn cho nhánh ASR
  └─ agent.py
       └─ LiveKit worker chuyển audio về ai_server
```

Hai nhánh audio được tách riêng:

```text
Audio gốc → VAD → WavLM → Speaker ID
Audio gốc → ASR preprocessing → (DPDFNet tùy chọn) → Zipformer → transcript
```

Audio dùng cho Speaker ID không đi qua DPDFNet để tránh làm thay đổi đặc trưng
giọng nói.

## Cấu trúc thư mục

```text
agent.py                         LiveKit worker
ai_server.py                     VAD, ASR, speaker ID, timeline
backend/api/                     REST API, SQLite, minutes queue
backend/audio_pipeline.py        preprocessing và DPDFNet adapter
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
MINUTES_COMPOSER_MODE=timeline
MINUTES_COMPOSER_MODEL=qwen2.5:3b
MINUTES_COMPOSER_NUM_THREADS=12

ASR_ENHANCER=none
ZIPFORMER_CHUNK_SIZE=32
ZIPFORMER_MAX_ACTIVE_PATHS=4
ZIPFORMER_BLANK_PENALTY=0.4
```

Không lưu mật khẩu sudo hoặc secret thật vào README, source code hay file được
commit. `.env` đã được loại khỏi Git.

## Chạy demo

Đảm bảo Ollama đã có model biên bản nếu bật `MINUTES_COMPOSER_MODE=llm`:

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

## DPDFNet

DPDFNet chỉ tác động lên nhánh ASR và chạy stateful theo từng microphone:

```dotenv
ASR_ENHANCER=dpdfnet_baseline
ASR_ENHANCER_MODEL=/home/ntd/meeting_runtime/models/dpdfnet_baseline.onnx
ASR_ENHANCER_THREADS=1
```

Chỉ bật sau khi A/B trên audio của chính phòng họp cho thấy WER/CER giảm. Trên
fixture hiện tại, DPDFNet làm tăng chi phí CPU và chưa cải thiện ASR. DPDFNet
cũng không giải quyết được overlap speech hoặc tách người nói từ hai microphone.

## Kiểm thử

Unit test:

```bash
/home/ntd/meeting_runtime/venv_linux/bin/python -m unittest discover -s tests
```

Benchmark Zipformer:

```bash
/home/ntd/meeting_runtime/venv_linux/bin/python -B scripts/evaluate_asr.py \
  --mode light --enhancer none --postprocess phonetic \
  --chunk-size 32 --blank-penalty 0.4 --final-padding-seconds 0.66
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
- Bước tiếp theo là benchmark DPDFNet theo điều kiện phòng thật, cải thiện
  dictionary/hotword theo tên cuộc họp và đánh giá riêng chất lượng biên bản.
