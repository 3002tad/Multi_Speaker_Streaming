# Baseline lock trước refactor microservice

Baseline này là mốc regression bắt buộc cho quá trình tách Meeting Platform. Nó mô tả trạng thái thực tế của commit `6a9fa12d3073c8cc2753d2f99f707716565ba5ee`, không thay thế bằng mô tả kiến trúc dự kiến.

## Pipeline được khóa

```text
raw microphone audio
→ coordinated VAD/global-turn
→ legacy light DSP (high-pass + noise attenuation + dynamic loudness)
→ Zipformer 30M RNNT streaming, chunk 32, modified beam paths 4
→ adaptive dictionary/hotword
→ deterministic phonetic recovery
→ transcript final

raw VAD-selected audio
→ WavLM speaker identification (nhánh độc lập)
```

Các điểm cần phân biệt:

- `ASR_FRONTEND=legacy`.
- `ASR_ENHANCER=none`: DPDFNet/GTCRN adapter và model vẫn có trong runtime nhưng **không active** trên đường ASR baseline.
- `ENABLE_ASR_PREPROCESSING=true`.
- `ASR_FINAL_TURN_REDECODE_ENABLED=false`.
- Qwen2.5:3b chỉ compose biên bản; không refine transcript realtime.
- Speaker ID tiếp tục dùng `microsoft/wavlm-base-sv` trên waveform gốc đã được VAD chọn.

## Kết quả khóa ngày 2026-08-03

| Dataset | File | Số mẫu | Mean WER | Mean CER | RTF |
|---|---|---:|---:|---:|---:|
| Clean/ít nhiễu | `audio/truth.csv` | 2 | 0.0826 | 0.0698 | 0.0654 |
| Nói nhanh/có nhiễu | `audio/truth_1.csv` | 4 | 0.3126 | 0.2780 | 0.0633 |

Streaming dual-mic E2E cũng pass với 4 final segments, đầy đủ global-turn/quality metadata và đúng speaker profile. WER theo hai turn lần lượt là `0.4062` và `0.1000`; CER là `0.3178` và `0.0850`.

Baseline có một giới hạn cold-start đã được tái hiện: cấp phát Zipformer stream cho từng mic hiện còn synchronous. Nếu probe phát lời nói ngay sau publish track, đoạn đầu có thể bị mất dù health endpoint đã lên. Harness vì vậy chờ `15s` sau khi publish hai track rồi mới gửi audio đo. Đây là điều kiện đo ổn định, không phải thay đổi pipeline. Meeting AI readiness trong kiến trúc mới phải warm-up/cấp phát decoder trước khi báo `READY` để người dùng thật không cần chờ mù.

Ngưỡng regression cho refactor thuần cấu trúc:

- WER và CER mỗi dataset không được xấu hơn baseline quá `0.01` tuyệt đối.
- Contract/event shape phải pass toàn bộ contract tests.
- Streaming E2E phải giữ final transcript, global-turn metadata, quality metadata và speaker fallback.
- Không thay threshold, model hoặc frontend audio trong cùng commit refactor kiến trúc.

## Lệnh tái lập

Chạy từ WSL tại root repository:

```bash
/home/ntd/meeting_runtime/venv_linux/bin/python -B scripts/evaluate_asr.py \
  --mode light \
  --frontend legacy \
  --enhancer none \
  --postprocess phonetic \
  --truth audio/truth.csv \
  --output tmp/baseline_lock_truth.json

/home/ntd/meeting_runtime/venv_linux/bin/python -B scripts/evaluate_asr.py \
  --mode light \
  --frontend legacy \
  --enhancer none \
  --postprocess phonetic \
  --truth audio/truth_1.csv \
  --output tmp/baseline_lock_truth_1.json
```

Streaming gate sau khi các process demo đã sẵn sàng:

```bash
/home/ntd/meeting_runtime/venv_linux/bin/python -B scripts/streaming_regression.py \
  --probe-connection-warmup 15 \
  --output tmp/streaming_regression_contract_lock.json
```

Thông tin máy, hash dataset/model và kết quả từng mẫu nằm trong `manifest.json` cùng thư mục.
