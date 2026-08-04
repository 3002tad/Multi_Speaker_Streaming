# Hướng dẫn chạy demo và kiểm thử phòng họp không giấy

Tài liệu này áp dụng cho kiến trúc hiện tại:

```text
Laptop người dùng
    → HTTPS/WSS Nginx trên home server
    → LiveKit public
    → Tailscale
    → Web/API và AI pipeline trong Ubuntu WSL
```

Source code vẫn nằm trên ổ Windows, còn virtualenv, model và dữ liệu runtime
được đặt trong filesystem WSL để giảm độ trễ I/O:

```text
/mnt/d/VNPT/Code/Multi_Speaker_Streaming     # source code
/home/ntd/meeting_runtime/venv_linux         # Python environment
/home/ntd/meeting_runtime/models             # DPDFNet và model phụ trợ
/home/ntd/meeting_runtime/data               # SQLite và Qdrant
/home/ntd/meeting_runtime/Zipformer-...       # model ASR
```

Tất cả lệnh Python của project phải sử dụng
`/home/ntd/meeting_runtime/venv_linux/bin/python`.

## 1. Chuẩn bị `.env`

Trong Ubuntu WSL:

```bash
cd /mnt/d/VNPT/Code/Multi_Speaker_Streaming
cp .env.example .env
```

Điền các giá trị tương ứng với LiveKit trên home server:

```dotenv
LIVEKIT_URL=wss://livekit.simplething.id.vn
LIVEKIT_API_KEY=<API key>
LIVEKIT_API_SECRET=<API secret>
MEETING_ROOM=paperless-demo
MEETING_CODE=DEMO-001
INTERNAL_API_KEY=<chuỗi ngẫu nhiên dài tối thiểu 24 ký tự>
# Không dùng Qwen để sửa từng câu transcript realtime.
ENABLE_LLM_REFINEMENT=false
# Biên bản chính thức chạy nền sau transcript.final; không sửa transcript realtime.
MINUTES_COMPOSER_ENABLED=true
MINUTES_COMPOSER_MODEL=qwen2.5:3b
MINUTES_COMPOSER_NUM_THREADS=12
MINUTES_COMPOSER_TIMEOUT_SECONDS=45
MINUTES_COMPOSER_KEEP_ALIVE=-1
```

Không lưu mật khẩu `sudo` trong `.env`. File `.env` đã được Git ignore.

Kiểm tra model Ollama:

```bash
ollama list
```

Nếu chưa có model:

```bash
ollama pull qwen2.5:3b
```

## 1.1 Runtime trong filesystem WSL

`scripts/run_demo.sh` mặc định dùng `/home/ntd/meeting_runtime`. Có thể đổi
runtime root cho một phiên chạy bằng biến môi trường:

```bash
export MEETING_RUNTIME_DIR=/home/ntd/meeting_runtime
```

Script chấp nhận `.env` ở thư mục project hoặc
`/home/ntd/meeting_runtime/.env`. Không tạo thêm virtualenv hay sao chép model
trở lại ổ `/mnt/d`.

## 1.2 Guarded enhancer cho nhánh ASR

DPDFNet/GTCRN chỉ xử lý nhánh ASR trước Zipformer. Audio gốc của mic vẫn đi
thẳng vào WavLM để speaker ID không bị thay đổi đặc trưng giọng. Chỉ tải model
khi máy chưa có file:

```bash
mkdir -p /home/ntd/meeting_runtime/models
curl -fL \
  -o /home/ntd/meeting_runtime/models/dpdfnet_baseline.onnx \
  https://github.com/k2-fsa/sherpa-onnx/releases/download/speech-enhancement-models/dpdfnet_baseline.onnx
curl -fL \
  -o /home/ntd/meeting_runtime/models/gtcrn_simple.onnx \
  https://github.com/k2-fsa/sherpa-onnx/releases/download/speech-enhancement-models/gtcrn_simple.onnx
```

Frontend guarded có thể bật để A/B:

```dotenv
ASR_FRONTEND=dpdfnet
ASR_ENHANCER_MODEL_TYPE=dpdfnet
ASR_ENHANCER_MODEL=/home/ntd/meeting_runtime/models/dpdfnet_baseline.onnx
ASR_ENHANCER_ALIGNMENT_DELAY_MS=40
ASR_PRESERVATION_MAX_SPEECH_MIX=0.10
```

Luồng này là `raw + candidate enhancer → căn delay → preservation gate →
DC-block/gain chậm/peak-limit → Zipformer`; không chạy high-pass 70 Hz, noise
attenuation hoặc AGC cũ trước enhancer. Gate kiểm tra correlation, năng lượng,
dải thoại 1–4 kHz và clipping. Frame không giữ được giọng sẽ fallback raw;
frame đạt gate chỉ trộn tối đa 10%.

DPDFNet baseline cần bù 40 ms. Khi A/B GTCRN, đổi model type/path và đặt
`ASR_ENHANCER_ALIGNMENT_DELAY_MS=0`. Benchmark hiện tại chưa vượt frontend
`legacy` trong streaming, nên mặc định vẫn giữ `ASR_FRONTEND=legacy`.

## 1.3 Adaptive dictionary và Zipformer hotword

`AdaptiveDictionary` là nguồn term chung cho hai nhánh: canonical term được
đưa vào Zipformer hotword; canonical + alias được dùng bởi phonetic recovery
sau khi global turn kết thúc. Người dùng không nhập tên cuộc họp. Raw
transcript chỉ được lưu làm evidence; nó không tự động trở thành hotword.

`phonetic_dictionary.txt` là seed rộng và chỉ dùng cho phonetic recovery;
không tự trở thành hotword cho mọi cuộc họp. Hotword chỉ lấy term động của
phiên hiện tại để tránh bias các chủ đề không liên quan.

Ba nguồn term theo thứ tự ưu tiên là file manual, tên thành viên và topic
discovery. File manual có thể sửa khi chạy:

```dotenv
ADAPTIVE_DICTIONARY_ENABLED=true
ADAPTIVE_DICTIONARY_STATE_PATH=/home/ntd/meeting_runtime/data/adaptive_dictionary.json
ADAPTIVE_DICTIONARY_MANUAL_PATH=/home/ntd/meeting_runtime/data/meeting_lexicon.txt
TOPIC_DISCOVERY_ENABLED=true
TOPIC_DISCOVERY_STATE_PATH=/home/ntd/meeting_runtime/data/topic_discovery.json
TOPIC_DISCOVERY_MODEL=qwen2.5:3b
TOPIC_DISCOVERY_BOOTSTRAP_SECONDS=90
TOPIC_DISCOVERY_REFRESH_SECONDS=60
TOPIC_DISCOVERY_MINIMUM_TURNS=6
TOPIC_DISCOVERY_MINIMUM_EVIDENCE_TURNS=2
TOPIC_DISCOVERY_MINIMUM_TOPIC_CONFIDENCE=0.65
TOPIC_DISCOVERY_MINIMUM_TERM_CONFIDENCE=0.88
TOPIC_DISCOVERY_TERM_TTL_HOURS=0.25
TOPIC_DISCOVERY_MAXIMUM_WINDOW_SECONDS=180
ZIPFORMER_HOTWORDS_ENABLED=true
ZIPFORMER_HOTWORDS_SCORE=1.5
ZIPFORMER_HOTWORDS_MIN_CONFIDENCE=0.9
ADAPTIVE_DICTIONARY_PHONETIC_MIN_CONFIDENCE=0.75
```

Sao chép file manual mẫu:

```bash
cp config/meeting_lexicon.example.txt \
  /home/ntd/meeting_runtime/data/meeting_lexicon.txt
```

Khi bật lần đầu, cài `sentencepiece` trong `venv_linux`; AI server tự sinh
`zipformer_hotwords.txt` và BPE vocabulary trong runtime. Snapshot mới không
đổi recognizer của microphone đang nói; mic đang kết nối nhận generation mới
ở VAD/global turn kế tiếp.

```bash
/home/ntd/meeting_runtime/venv_linux/bin/python -m pip install sentencepiece
curl -X POST http://127.0.0.1:8001/api/adaptive-dictionary/reload \
  -H "X-Internal-Key: $INTERNAL_API_KEY"
```

## 2. Cấu hình home server

Hai domain public:

- Web: `https://meet.simplething.id.vn`
- LiveKit: `wss://livekit.simplething.id.vn`

Nginx của domain Web phải:

- Phục vụ frontend từ `/var/www/meeting`.
- Proxy `/api/` và `/ws/` về backend WSL qua Tailscale:
  `http://100.64.0.4:8000`.
- Cho phép WAV enrollment 20–30 giây bằng:

```nginx
client_max_body_size 10m;
```

Cấu hình mẫu nằm tại:

```text
deploy/nginx/meet.simplething.id.vn
```

Sau khi stage file lên server:

```bash
sudo cp /home/ntd/meeting-deploy/meet.simplething.id.vn \
  /etc/nginx/sites-available/meet.simplething.id.vn

sudo cp /home/ntd/meeting-deploy/app.js \
  /var/www/meeting/app.js

sudo nginx -t
sudo systemctl reload nginx
```

## 3. Khởi động pipeline trong WSL

```bash
cd /mnt/d/VNPT/Code/Multi_Speaker_Streaming
chmod +x scripts/run_demo.sh
bash scripts/run_demo.sh
```

Script thực hiện:

1. Kiểm tra `.env`.
2. Khởi động API; Qwen2.5:3B được warm-up trước, rồi chỉ cập nhật biên bản sau global turn cuối.
3. Chạy Web/API tại `0.0.0.0:8000`.
4. Chạy AI pipeline tại `127.0.0.1:8001`.
5. Chạy LiveKit worker và tự reconnect khi kết nối bị gián đoạn.

Chờ log:

```text
[worker] Sẵn sàng nhận các luồng microphone.
```

Kiểm tra từ WSL:

```bash
curl http://127.0.0.1:8000/api/health
```

Kiểm tra từ home server:

```bash
curl http://100.64.0.4:8000/api/health
```

## 4. Enrollment người nói

### Enrollment từ Web

1. Mở `https://meet.simplething.id.vn`.
2. Nhập đúng tên hiển thị.
3. Mở chức năng enrollment.
4. Đọc liên tục đoạn văn mẫu trong 20–30 giây.
5. Dừng ghi và chờ AI tạo vân tay giọng nói.

Đọc bằng một giọng duy nhất, giữ khoảng cách tới mic ổn định, tránh tiếng
người khác và không để âm lượng bị vỡ. Pipeline yêu cầu tối thiểu 6 giây
giọng sạch, loại cửa sổ quá nhỏ/quá lớn hoặc không nhất quán, sau đó lưu
nhiều prototype thay vì chỉ một vector trung bình.

Sau khi nâng cấp thuật toán speaker profile lên phiên bản 2, cần enrollment
lại từng người để profile cũ được thay thế bằng bộ prototype mới.

Speaker profile được lưu persistent tại:

```text
data/qdrant_speakers/
```

Profile không mất khi restart AI process.

### Xóa toàn bộ Qdrant speaker database

Thao tác này xóa vĩnh viễn tất cả profile đã enrollment. Trước tiên dừng
pipeline bằng `Ctrl+C`, sau đó xác nhận AI server không còn chạy:

```bash
pgrep -af '[a]i_server.py'
ss -ltnp | grep -E ':8001\b'
```

Hai lệnh trên không được trả về process/cổng AI. Kiểm tra đúng thư mục sẽ xóa:

```bash
realpath /mnt/d/VNPT/Code/Multi_Speaker_Streaming/data/qdrant_speakers
```

Nếu đường dẫn trả về đúng như trên, xóa database:

```bash
rm -rf -- /mnt/d/VNPT/Code/Multi_Speaker_Streaming/data/qdrant_speakers
```

Lần khởi động `ai_server.py` tiếp theo sẽ tự tạo lại Qdrant collection rỗng.
Sau đó phải enrollment lại từng người. Không chạy lệnh xóa khi AI server đang
ghi hoặc đọc Qdrant.

### Enrollment bằng file mẫu

```bash
curl -F speaker_name=Thay_Dung \
  -F file=@audio/thayDung_goc.wav \
  http://127.0.0.1:8000/api/enrollment

curl -F speaker_name=Thay_Phuoc \
  -F file=@audio/thayPhuoc_goc.wav \
  http://127.0.0.1:8000/api/enrollment
```

Nếu trình duyệt báo máy chủ trả HTML thay vì JSON hoặc HTTP 413, kiểm tra
`client_max_body_size 10m` trong Nginx.

## 5. Kịch bản demo ba laptop

1. Mở `https://meet.simplething.id.vn` trên ba laptop.
2. Chủ trì nhập tên và bấm **Tạo phòng**.
3. Hai thành viên nhập mã `DEMO-001` và bấm **Tham gia**.
4. Playback mặc định tắt. Khi bật playback phải đeo tai nghe để tránh vọng.
5. Mỗi người nói khoảng 15 giây.
6. Theo dõi transcript nháp realtime và biên bản có cấu trúc.
7. Đổi vị trí laptop/micro để xác nhận hệ thống định danh theo giọng nói,
   không cố định người nói theo vị trí mic.

Transcript nháp luôn là ASR raw (có thể đã qua phonetic recovery cho thuật
ngữ) và không gọi LLM. Sau khi `transcript.final` đã qua lọc mic trùng,
backend xếp nó vào một hàng đợi Qwen2.5:3B đơn. Qwen cập nhật biên bản JSON theo
chủ đề, tóm tắt, đề xuất, quyết định và việc cần làm. Mỗi mục luôn giữ
`source_segment_ids` để mở lại transcript nguồn; request Ollama luôn gửi
`think:false`.

## 6. Test hai micro có lọt âm

Hai file `*_noi.wav` là hai bản ghi đồng bộ trong cùng không gian. Khi một
người nói gần mic A, mic B vẫn thu được tiếng lọt và ngược lại.

Đảm bảo pipeline đang chạy và hai người đã enrollment, sau đó:

```bash
cd /mnt/d/VNPT/Code/Multi_Speaker_Streaming
venv_linux/bin/python -B test_client.py
```

Test tạo hai participant LiveKit độc lập và publish đồng thời:

- `thayDung_noi.wav` qua participant `Mic A`.
- `thayPhuoc_noi.wav` qua participant `Mic B`.

Luồng kiểm thử:

```text
Mic → LiveKit → agent (PCM + sequence/timestamp chung)
    → AI server: VAD riêng từng mic
    → coordinator timeline toàn phòng
       ├─ Speaker ID: raw VAD segment → lọc cửa sổ/clipping → WavLM
       └─ ASR: chọn mic theo SNR → high-pass/DC → noise suppression nhẹ
                 → normalize → Zipformer
    → phonetic recovery theo dictionary → WebSocket/SQLite (transcript nguồn)
    → backend queue → Qwen2.5:3B Minutes Composer (`think:false`) → biên bản JSON
```

VAD vẫn chạy một stream riêng cho mỗi mic để giữ trạng thái cục bộ, nhưng
`CoordinatedVadTimeline` ghép các stream theo timestamp. Các mic đang thu cùng
một turn sẽ được so sánh chất lượng theo frame; mic bị lọt tiếng sẽ không đưa
frame vào Zipformer. Những mic có chất lượng tương đương vẫn được giữ lại để
không làm mất trường hợp hai người nói chồng. Ở bước final, các transcript
trùng trong cùng global turn tiếp tục được deduplicate theo nội dung và
envelope âm thanh.

Audio đưa vào WavLM không đi qua ASR preprocessing hoặc DPDFNet. Điều này giữ
đặc trưng giọng ổn định khi ghi danh hoặc nhận dạng. DPDFNet chạy stateful sau
khâu chọn mic và high-pass/AGC của nhánh ASR, có look-ahead 10 ms được flush
trước khi Zipformer chốt transcript.

Speaker ID chia đoạn nói thành cửa sổ 4 giây, stride 2 giây. Sau khi loại
silence/clipping/noise, ba cửa sổ dùng để nhận dạng được lấy trải đều từ đầu,
giữa và cuối lượt nói thay vì chỉ lấy ba cửa sổ lớn tiếng nhất. Cách này giữ
được biến thiên tone/cường độ mà không tăng số lần inference WavLM.

Phonetic recovery chỉ áp dụng cho `transcript.final` sau khi global turn đã
chọn được đoạn ASR tốt nhất. Module này so khớp transcript với dictionary
thuật ngữ nội bộ bằng khóa không dấu/phonetic gần đúng. Khi cấu hình
`PHONETIC_BACKEND=g2p_onnx`, nó dùng `g2p_multilingual_byT5_tiny_onnx` để so
sánh IPA, rồi kết hợp với similarity grapheme, ngưỡng và margin an toàn trước
khi thay thế. Nếu model G2P không nạp được, nó tự fallback về grapheme để
transcript không bị gián đoạn.
Transcript nháp realtime vẫn giữ nguyên raw text. Kết quả final lưu cả
`raw_text`, `phonetic_recovered_text` và danh sách `phonetic_replacements`
để kiểm tra trước/sau.

Dictionary mẫu nằm tại
`config/phonetic_dictionary.example.txt`. Khi chạy thật, sao chép thành:

```bash
mkdir -p /home/ntd/meeting_runtime/data
cp config/phonetic_dictionary.example.txt \
  /home/ntd/meeting_runtime/data/phonetic_dictionary.txt
```

G2P model được đặt ở runtime WSL, không đưa vào Git:

```bash
/home/ntd/meeting_runtime/venv_linux/bin/python -m pip install optimum-onnx
/home/ntd/meeting_runtime/venv_linux/bin/python -c "from huggingface_hub import snapshot_download; snapshot_download('klebster/g2p_multilingual_byT5_tiny_onnx', local_dir='/home/ntd/meeting_runtime/models/g2p_multilingual_byT5_tiny_onnx')"
```

### Token Hugging Face khi tải model lớn

Nếu tài khoản có quyền/tốc độ tải tốt hơn, đặt token vào session WSL trước khi
tải; không ghi token vào source code, `.env` project hoặc chat:

```bash
read -rsp 'Hugging Face token: ' HF_TOKEN; echo
export HF_TOKEN
```

Các lệnh `curl` dùng header an toàn sau (token không xuất hiện trong history
nếu biến đã được export từ session):

```bash
curl -fL --continue-at - \
  -H "Authorization: Bearer $HF_TOKEN" \
  -o /home/ntd/meeting_runtime/models/<model>.gguf \
  https://huggingface.co/<org>/<repo>/resolve/main/<file>.gguf
```

Có thể tắt recovery để A/B:

```dotenv
PHONETIC_RECOVERY_ENABLED=false
```

Các tham số có thể điều chỉnh trong `.env`:

```dotenv
ENABLE_ASR_PREPROCESSING=true
ASR_HIGH_PASS_HZ=70
ASR_TARGET_RMS=0.065
ASR_FINAL_PADDING_SECONDS=0.66
# legacy | dpdfnet
ASR_FRONTEND=legacy
# Chỉ dùng cho frontend legacy: none | dpdfnet_baseline
ASR_ENHANCER=none
ASR_ENHANCER_MODEL=/home/ntd/meeting_runtime/models/dpdfnet_baseline.onnx
ASR_ENHANCER_MODEL_TYPE=dpdfnet
ASR_ENHANCER_THREADS=1
# >= bypass: raw; <= full: DPDFNet mix tối đa; ở giữa: blend động.
ASR_ENHANCER_BYPASS_SNR_DB=15
ASR_ENHANCER_FULL_SNR_DB=3
ASR_ENHANCER_MAX_MIX=0.65
# attack vào DPDFNet chậm, release về raw nhanh khi audio sạch trở lại.
ASR_ENHANCER_ATTACK=0.20
ASR_ENHANCER_RELEASE=0.65
# Căn waveform trước khi gate: DPDFNet baseline=40 ms, GTCRN=0 ms.
ASR_ENHANCER_ALIGNMENT_DELAY_MS=40
ASR_PRESERVATION_MIN_CORRELATION=0.93
ASR_PRESERVATION_MIN_ENERGY_RATIO=0.65
ASR_PRESERVATION_MAX_ENERGY_RATIO=1.35
ASR_PRESERVATION_MIN_SPEECH_BAND_RATIO=0.80
ASR_PRESERVATION_MAX_SPEECH_MIX=0.10
ASR_PRESERVATION_MAX_NOISE_MIX=0.65
ASR_PRESERVATION_CROSSFADE_MS=15
# Chỉ dùng khi ASR_FRONTEND=dpdfnet.
ASR_DPDFNET_POST_DC_HZ=20
ASR_DPDFNET_POST_TARGET_RMS=0.055
ASR_DPDFNET_POST_MIN_GAIN=0.75
ASR_DPDFNET_POST_MAX_GAIN=1.50
ASR_DPDFNET_POST_ATTENUATION_RATE=0.08
ASR_DPDFNET_POST_BOOST_RATE=0.02
ASR_DPDFNET_POST_ACTIVITY_FLOOR=0.003
ASR_DPDFNET_POST_PEAK_LIMIT=0.97
TIMELINE_ASR_QUALITY_MARGIN=3.5
TIMELINE_ASR_RMS_RATIO=0.48
TIMELINE_FINAL_SETTLE_SECONDS=0.75
# modified-beam width; đã A/B 4/8/12 trước khi tăng cho mọi mic.
ZIPFORMER_MAX_ACTIVE_PATHS=4
ZIPFORMER_CHUNK_SIZE=32
ZIPFORMER_BLANK_PENALTY=0.4
AUDIO_FRAME_SIZE_MS=20
ASR_DECODE_ALL_MICS=true
ASR_SOFT_SPLIT_SECONDS=15
ASR_HARD_SPLIT_SECONDS=30
ASR_SPLIT_MIN_SILENCE_SECONDS=0.30
SPEAKER_MATCH_THRESHOLD=0.86
SPEAKER_OPEN_SET_FLOOR=0.86
SPEAKER_SINGLE_PROFILE_THRESHOLD=0.90
SPEAKER_ID_WINDOW_SECONDS=3.0
SPEAKER_ID_MAX_WINDOWS=2
SPEAKER_EARLY_EXIT_SCORE_BUFFER=0.025
SPEAKER_EARLY_EXIT_MARGIN_BUFFER=0.015
WAVLM_NUM_THREADS=2
LLM_INLINE_WAIT_SECONDS=0.35
PHONETIC_RECOVERY_ENABLED=true
PHONETIC_DICTIONARY_PATH=/home/ntd/meeting_runtime/data/phonetic_dictionary.txt
PHONETIC_RECOVERY_THRESHOLD=0.86
PHONETIC_RECOVERY_MARGIN=0.06
PHONETIC_RECOVERY_MAX_WORDS=4
PHONETIC_BACKEND=g2p_onnx
PHONETIC_G2P_MODEL_PATH=/home/ntd/meeting_runtime/models/g2p_multilingual_byT5_tiny_onnx
PHONETIC_G2P_LANGUAGE=vie-c
PHONETIC_G2P_THREADS=4
PHONETIC_G2P_WEIGHT=0.65
PHONETIC_G2P_PREFILTER=0.80
PHONETIC_G2P_MAX_CALLS=8
PHONETIC_G2P_FORCE=false
# Always-ready triple gate only evaluates dictionary candidates on a finalized
# global turn. It uses Epitran + ByT5 ONNX + SEA-G2P and requires 2/3 consensus.
PHONETIC_TRIPLE_WEIGHT=0.75
PHONETIC_TRIPLE_MIN_CONSENSUS=2
PHONETIC_TRIPLE_CONSENSUS_TOLERANCE=0.18
# A/B Sailor: only accepts/rejects the closed dictionary/G2P candidate.
# Qwen direct cleanup remains the default baseline.
REFINEMENT_BACKEND=sailor_candidate
SAILOR_MODEL=sailor2:1b
SAILOR_KEEP_ALIVE=5m
SAILOR_NUM_THREADS=2
SAILOR_LANGUAGE=vi-VN
SAILOR_CONTEXT_TURNS=2
```

Nếu runtime chưa có ba dependency của triple gate:

```bash
/home/ntd/meeting_runtime/venv_linux/bin/python -m pip install epitran panphon sea-g2p
```

Chạy benchmark final-turn riêng (không khởi động LiveKit):

```bash
venv_linux/bin/python -B scripts/evaluate_triple_phonetics.py \
  --output tmp/triple_phonetic_report.json
```

`SPEAKER_OPEN_SET_FLOOR` chỉ là ngưỡng sàn. Khi có từ hai profile, hệ thống
tính độ tương đồng lớn nhất giữa các centroid đã enroll và tự nâng absolute
gate lên `closest_profile_similarity + margin`. Đồng thời từng cửa sổ phải
đồng thuận về winner và vượt margin top-1/top-2. Vì vậy hai giọng càng gần
nhau thì càng khó bị gán nhầm; không dùng một threshold cố định cho mọi cohort.

Để benchmark Zipformer với `audio/truth.csv` mà không cần khởi động toàn bộ
backend:

```bash
venv_linux/bin/python -B scripts/evaluate_asr.py --mode both
```

Đo transcript final đúng như pipeline (Zipformer + phonetic gate, không LLM):

```bash
venv_linux/bin/python -B scripts/evaluate_asr.py \
  --frontend legacy --mode light --enhancer none --postprocess phonetic
```

Benchmark decoder theo tốc độ nói và nhiễu (có trailing padding giống runtime):

```bash
venv_linux/bin/python -B scripts/evaluate_asr.py \
  --frontend legacy --mode light --enhancer none --postprocess phonetic \
  --chunk-size 32 --blank-penalty 0.4 --final-padding-seconds 0.66
```

`chunk-size=16/32/64` tương ứng đánh đổi độ trễ và ngữ cảnh âm học. Với demo CPU
hiện tại, chunk 32 (~640 ms) và blank penalty 0.4 là cấu hình cân bằng đã đo trên
`truth.csv` và `truth_1.csv`. Chunk 64 (~1,28 s) có thể dùng cho final-pass khi
cần thêm độ chính xác. Evaluator cũng ghi riêng số lỗi xoá/chèn/thay (D/I/S),
giúp phân biệt model bỏ từ với hậu xử lý.

So decoder beam hoặc hotword chỉ bằng A/B trên truth. Ví dụ:

```bash
venv_linux/bin/python -B scripts/evaluate_asr.py \
  --mode raw --enhancer none --max-active-paths 8 \
  --hotwords "VNPT,HDFS,HBase"
```

Benchmark A/B DPDFNet guarded trên cùng tập chuẩn:

```bash
venv_linux/bin/python -B scripts/evaluate_asr.py \
  --frontend dpdfnet --mode raw --enhancer none \
  --postprocess phonetic \
  --output output/asr-dpdfnet-frontend.json
```

Benchmark GTCRN trên cùng gate mà không sửa `.env`:

```bash
venv_linux/bin/python -B scripts/evaluate_asr.py \
  --frontend dpdfnet --denoiser-model-type gtcrn \
  --denoiser-model /home/ntd/meeting_runtime/models/gtcrn_simple.onnx \
  --alignment-delay-ms 0 --mode raw --enhancer none \
  --postprocess phonetic \
  --output output/asr-gtcrn-frontend.json
```

Report ghi số frame thoại accepted/fallback, mix, correlation, energy ratio,
speech-band ratio, gain và peak-limit. Nếu WER xấu hơn frontend `legacy`, giữ
`ASR_FRONTEND=legacy`. Có thể benchmark blend động cũ riêng bằng `--frontend
legacy --mode light --enhancer dpdfnet_baseline`, nhưng không trộn kết quả của
hai kiến trúc vào cùng một baseline.

Nếu một file chứa nhiều đoạn/giọng khác nhau, nên thêm hai cột
`start_seconds,end_seconds` vào `truth.csv`; evaluator sẽ chỉ cắt đúng đoạn
đó trước khi tính WER/CER.

Test tự động toàn bộ streaming LiveKit:

```bash
venv_linux/bin/python -B scripts/streaming_regression.py
```

Test nhánh `Zipformer → G2P ONNX → Sailor candidate gate` (không thay đổi
Qwen baseline trong `.env`):

```bash
ollama pull sailor2:1b
PHONETIC_BACKEND=g2p_onnx \
PHONETIC_G2P_FORCE=true \
REFINEMENT_BACKEND=sailor_candidate \
SAILOR_MODEL=sailor2:1b \
venv_linux/bin/python -B scripts/streaming_regression.py \
  --start-demo \
  --output output/streaming-sailor-g2p.json
```

Report giữ `raw_text`, candidate/replacements từ G2P và trường `refinement`.
Sailor chỉ có hai quyết định `ACCEPT`/`REJECT`; timeout, JSON lỗi hoặc từ chối
đều trả lại raw ASR. Đánh giá cả WER/CER và số quyết định Sailor trước khi bật
nhánh này cho demo.

Runner sẽ xóa transcript cũ, chạy dual-mic probe qua LiveKit, chờ
`transcript.final`, ghép kết quả theo mốc thời gian trong `truth.csv`, tính
WER/CER và kiểm tra `global_turn_id`, SNR, clipping cùng dedup.

Để runner tự khởi động và tự dừng WSL demo:

```bash
venv_linux/bin/python -B scripts/streaming_regression.py --start-demo
```

Thêm `--keep-demo` nếu muốn giữ pipeline chạy sau test. Khi tự khởi động, log
nằm tại `output/streaming-regression-demo.log`. Exit code `0` chỉ được trả về
khi transport probe, transcript, timeline metadata và overlap checks đều đạt.
Có thể lưu báo cáo JSON:

```bash
venv_linux/bin/python -B scripts/streaming_regression.py \
  --start-demo \
  --output output/streaming-regression-report.json
```

Ngưỡng mặc định của streaming regression là `WER <= 0.65`, `CER <= 0.60`;
có thể điều chỉnh bằng `--max-wer` và `--max-cer`.

Test riêng tình huống nhiều người nói đồng thời từ các file trong
`audio/truth_1.csv`:

```bash
venv_linux/bin/python -B scripts/concurrent_streaming_regression.py \
  --start-demo \
  --mics 4 \
  --output tmp/concurrent-streaming-4mic.json
```

Có thể đổi `--mics 2`, `--mics 3` hoặc `--mics 4`. Runner theo dõi event
WebSocket theo từng `source_id`; mỗi mic bắt buộc phải có cả
`transcript.partial` và `transcript.final`. Runner tự dừng demo an toàn nếu
không truyền `--keep-demo`. Log nằm tại
`output/concurrent-streaming-demo.log`.

Kiểm tra transcript đã lưu:

```bash
curl http://127.0.0.1:8000/api/transcripts
```

Xóa dữ liệu transcript trước lượt test mới:

```bash
curl -X DELETE http://127.0.0.1:8000/api/transcripts
```

## 7. Tiêu chí nghiệm thu

- Ba laptop join được cùng phòng `paperless-demo`.
- Mỗi laptop publish được một microphone track.
- Transcript nháp cập nhật realtime.
- Lượt nói dài được chia thành segment xấp xỉ 15 giây.
- WavLM gán đúng người đã enrollment dù đổi vị trí mic.
- Chỉ nhận tên theo voice profile khi nhiều cửa sổ giọng nói cùng đạt ngưỡng
  điểm, margin và consensus.
- Voice chưa ghi danh hoặc score dưới open-set floor phải fallback về tên mic;
  không được lấy profile gần nhất làm tên mặc định.
- Hai mic thu cùng một câu chỉ tạo một bản final; timeline giữ nguồn có SNR,
  RMS và clipping tốt hơn.
- Nội dung không bị mất khi WavLM chưa đủ chắc chắn; khi đó dùng tên đăng
  nhập của mic với `identity_method=mic_fallback`.
- Transcript nguồn không bị Qwen viết lại theo từng segment.
- Qwen chỉ chạy sau final turn, lần lượt trong một queue để tránh tranh CPU
  với Zipformer/WavLM; UI cập nhật trạng thái **Đang cập nhật**.
- Mọi bullet biên bản không có `source_segment_id` hợp lệ bị backend loại bỏ.
- Speaker profile vẫn tồn tại sau khi restart.

## 8. Chạy test backend

```bash
cd /mnt/d/VNPT/Code/Multi_Speaker_Streaming
venv_linux/bin/python -B -m unittest discover -s tests -p 'test_*.py'
```

Smoke tests kiểm tra:

- Health và metadata phòng.
- Tạo/join phòng và LiveKit token.
- Bảo vệ internal API.
- Lưu transcript vào SQLite.
- Lưu/đọc biên bản JSON có version và chỉ nhận dẫn chứng từ transcript đã lưu.
- Loại final trùng giữa hai mic và giữ nguồn mạnh hơn.
- Packet PCM có timestamp/sequence và fallback raw-PCM.
- Coordinator giữ mic có chất lượng tốt hơn nhưng không loại overlap thật.
- Tách audio raw cho WavLM khỏi DSP nhẹ cho Zipformer.
- Đọc và tính WER/CER từ `audio/truth.csv`.

## 9. Dừng hệ thống an toàn

Thứ tự an toàn là:

```text
Người tham gia rời phòng
    → dừng pipeline WSL
    → dừng LiveKit/Nginx nếu bảo trì home server
    → tắt máy vật lý
```

### 9.1. Kết thúc một buổi demo thông thường

1. Yêu cầu các laptop rời phòng hoặc tắt microphone.
2. Chờ đến khi trạng thái biên bản là **Đã cập nhật** (hoặc tối đa 30 giây)
   để worker Qwen ghi biên bản cuối vào SQLite.
3. Có thể kiểm tra lần cuối:

```bash
curl http://127.0.0.1:8000/api/transcripts
curl http://127.0.0.1:8000/api/minutes
```

4. Tại terminal WSL đang chạy `run_demo.sh`, nhấn:

```text
Ctrl+C
```

5. Chờ terminal trả lại dấu nhắc. Script sẽ gửi tín hiệu dừng cho:

   - Backend Uvicorn cổng `8000`.
   - AI server cổng `8001`.
   - LiveKit worker `agent.py`.

6. Xác nhận không còn tiến trình của demo:

```bash
pgrep -af 'run_demo.sh|backend.api.main|ai_server.py|agent.py'
ss -ltnp | grep -E ':(8000|8001)\b'
```

Hai lệnh trên không nên trả về process/cổng của pipeline. Nếu một process vẫn
còn, lấy đúng PID từ `pgrep`, gửi `kill -TERM <PID>` và chờ process kết thúc.
Chỉ dùng `kill -KILL` khi `TERM` không có tác dụng sau một khoảng chờ hợp lý.

Không cần dừng Nginx, LiveKit, Headscale hoặc Tailscale trên home server khi
chỉ kết thúc một buổi demo. Giữ các dịch vụ này chạy giúp lần demo sau khởi
động nhanh hơn.

### 9.2. Dừng hoàn toàn để bảo trì home server

Thực hiện mục 9.1 để dừng WSL trước. Sau đó SSH vào server:

```bash
ssh ntdserver
```

Dừng LiveKit bằng đúng Docker Compose project:

```bash
cd /opt/livekit
docker compose stop livekit
docker compose ps
```

`docker compose ps` phải cho thấy LiveKit đã dừng. Không dùng
`docker compose down -v`, vì tùy chọn `-v` có thể xóa volume/dữ liệu.

Nếu cần bảo trì Nginx:

```bash
sudo nginx -t
sudo systemctl stop nginx
```

Không dừng `headscale.service` hoặc `tailscaled.service` trong một phiên bảo
trì thông thường. Chúng đang cung cấp đường kết nối giữa home server và WSL;
dừng chúng có thể làm mất kết nối quản trị từ xa.

### 9.3. Tắt nguồn home server

Sau khi WSL, LiveKit và các tác vụ ghi dữ liệu đã dừng:

```bash
sudo shutdown -h now
```

Nếu bắt buộc phải dừng Headscale/Tailscale để bảo trì chính các dịch vụ này,
thực hiện chúng cuối cùng và chỉ khi còn đường truy cập LAN/console:

```bash
sudo systemctl stop headscale
sudo systemctl stop tailscaled
```

Không rút nguồn trực tiếp khi Docker, SQLite hoặc Qdrant đang ghi dữ liệu.

### 9.4. Tắt hẳn WSL trên Windows

Chỉ thực hiện sau khi `run_demo.sh` đã dừng và terminal WSL đã trả lại dấu
nhắc. Trong PowerShell:

```powershell
wsl.exe --shutdown
```

Lệnh này dừng tất cả distro WSL đang chạy trên máy, không chỉ distro của
project.

### 9.5. Khởi động lại sau bảo trì

Trên home server:

```bash
cd /opt/livekit
docker compose up -d livekit
sudo systemctl start nginx
docker compose ps
curl -I https://meet.simplething.id.vn
```

Headscale, Tailscale, Docker và Nginx thường tự khởi động cùng hệ điều hành,
nhưng vẫn nên kiểm tra trạng thái:

```bash
systemctl is-active docker nginx headscale tailscaled
```

Sau đó chạy lại pipeline trong WSL:

```bash
cd /mnt/d/VNPT/Code/Multi_Speaker_Streaming
bash scripts/run_demo.sh
```
