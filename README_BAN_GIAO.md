# Tổng quan gói bàn giao — Paperless Meeting Demo

Gói source demo phòng họp không giấy: eCabinet quản lý nghiệp vụ phiên họp,
Meeting Service quản lý runtime/biên bản, Meeting AI xử lý audio realtime.
Demo phù hợp mạng LAN, dùng LiveKit self-host và không cần public Internet.

## Nội dung gói bàn giao

| Thành phần | Vai trò |
|---|---|
| `ecabinet/` | Hệ thống lõi: đăng nhập, lịch họp, đại biểu, role và giao diện MeetingRoom. |
| `meeting_service/` | Microservice runtime: session, token LiveKit, transcript, minutes, revision, DOCX metadata và Compose vận hành. |
| `meeting_ai/` | Microservice AI: LiveKit Agent, VAD, Zipformer ASR, speaker ID, transcript pipeline và composition biên bản. |
| `HUONG_DAN_CONFIG_RUN_ALL.md` | Hướng dẫn cấu hình, khởi động, test và dừng toàn bộ stack. |
| `HANDOFF_COMMITS.md` | Lịch sử cặp commit, checkpoint, kết quả test và giới hạn bàn giao. |

## Chức năng/module đã tích hợp

- Quản lý vòng đời phiên họp: tạo, bắt đầu/tham gia theo role, kết thúc và đóng
  phiên trên UI eCabinet.
- LiveKit LAN: cấp token theo quyền publish/subscribe; mic, playback mặc định tắt
  và danh sách người tham gia theo trạng thái kết nối.
- Pipeline audio nhiều mic: global-turn VAD, Zipformer streaming ASR, WavLM/Qdrant
  speaker identification và fallback identity theo người tham gia.
- Transcript realtime/final: persistence trong Meeting Service, polling/rehydrate
  UI và evidence theo từng segment.
- Biên bản structured: thông tin chung, tóm tắt, chủ đề, chi tiết, phát biểu,
  quyết định, việc cần làm, nguồn transcript và revision optimistic locking.
- Minutes AI: người dùng bấm phân tích lần đầu để bật auto-update; debounce,
  chống stale callback, timeline fallback và chống một transcript bị lặp ở nhiều
  khung phân loại.
- Enrollment giọng nói tùy chọn, adaptive dictionary/hotword và phonetic recovery
  cho thuật ngữ/tên riêng.
- Xuất metadata DOCX qua Meeting Service; biên bản thuộc vòng đời phiên họp, không
  tự đẩy sang module nghiệp vụ khác của eCabinet.

## Kiến trúc ngắn gọn

```text
Browser eCabinet ──REST/Socket──> Meeting Service ──internal API──> Meeting AI
       │                                  │                              │
       └────────────── LiveKit LAN ───────┴───── audio realtime ──────────┘
```

eCabinet không chia sẻ database với Meeting Service. Meeting AI không truy cập
database/MinIO eCabinet. Secret, model, cache và runtime data luôn để ngoài source.

Đọc `README.md` để xem giới thiệu gốc của dự án; đọc
`HUONG_DAN_CONFIG_RUN_ALL.md` để cài đặt và chạy demo LAN.
