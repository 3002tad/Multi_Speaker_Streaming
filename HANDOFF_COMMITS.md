# Nhật ký bàn giao và checkpoint

Workspace này gồm hai Git repository cục bộ:

- Repository root: Meeting AI và Meeting Service.
- Repository `ecabinet/`: eCabinet Core và giao diện tích hợp Meeting.

Repository eCabinet chỉ được giữ cục bộ và bàn giao bằng ZIP; không push lên
remote bên ngoài.

## Quy tắc đóng gói

ZIP bàn giao cần có source root, `ecabinet/`, `contracts/`, `meeting_ai/`,
`meeting_service/`, tests và file này.

Không đưa vào ZIP: `.git/`, `.env`, key/token, model, cache Hugging Face/Ollama,
dữ liệu Qdrant, Docker volume, `__pycache__/`, `output/` hoặc runtime output.
Người nhận phải tự cấu hình secret từ các file `.env.example`.

## Kiểm tra trước khi bàn giao

1. Chạy toàn bộ named unittest và contract test bằng Python trong WSL.
2. Chạy compile backend và build frontend nếu có thay đổi tương ứng.
3. Kiểm tra `git status` của cả hai repository phải sạch.
4. Ghi lại branch, commit hash, phạm vi thay đổi, test đã chạy và giới hạn
   chưa kiểm thử.
5. Không push repository eCabinet.

## Các checkpoint lịch sử

| Thành phần | Branch | Commit | Nội dung |
|---|---|---|---|
| Root / Meeting Service | `feature/meeting-platform-microservices` | `e3faffe` | Runtime lifecycle, runtime token và REST contract transcript/minutes |
| eCabinet | `feature/meeting-platform-integration` | `133aa58` | Façade kết nối runtime Meeting |
| Root | `feature/meeting-platform-microservices` | `c990709` | JSON-safe event Socket.IO |
| eCabinet | `feature/meeting-platform-integration` | `75478a5` | Vite proxy cho REST và Socket.IO |
| Root | `feature/meeting-platform-microservices` | `2f18d6f` | Handoff Day 5 LiveKit |

## Checkpoint hiện tại — 2026-08-07

### LiveKit workspace

- Root / Meeting Service: `c62bd6d`
- eCabinet: `086b26c`

Đã có cấu hình ký token LiveKit, kết nối Room, bật/tắt mic, playback mặc định
tắt, transcript reducer, reconnect rehydrate và permission `can_view_meeting`.
Backend eCabinet tham gia network Docker ngoài `meeting_platform_internal`.

LiveKit media thật chưa được kiểm thử vì local stack chưa cấu hình
`MEETING_LIVEKIT_URL`, `MEETING_LIVEKIT_API_KEY` và
`MEETING_LIVEKIT_API_SECRET`; token façade trả `503` đúng thiết kế khi thiếu
secret.

### Chuẩn hóa contract và MinutesEditor

- Root / Meeting Service: `4c63753`
- eCabinet: `3fcabc4`
- Handoff cập nhật: `dfc5ec5`

Đã hoàn thành:

- Contract token chuẩn: `/internal/v1/meetings/{meeting_id}/tokens`.
- Endpoint runtime token cũ được giữ làm compatibility alias ẩn.
- Identity LiveKit có dạng `user:{user_id}:device:{device_id}`.
- Token trả về `runtime_session_id`.
- Minutes dùng optimistic locking qua `base_revision`; revision cũ trả `409`.
- `PATCH` là phương thức minutes chuẩn; `PUT` vẫn tương thích.
- `MinutesEditor.jsx` thay textarea JSON bằng các block thông tin chung, tóm
  tắt, chủ đề, đề xuất, quyết định, action item và transcript evidence.

## Kiểm thử checkpoint hiện tại

- Named unittest đầy đủ: **111 tests passed**.
- `tests/test_contracts.py`: đạt.
- Compile Meeting Service và eCabinet backend: đạt.
- Frontend Vite production build: đạt.
- Không repository nào được push lên remote.

## Bước tiếp theo theo merge plan

Sau khi commit lifecycle bên dưới, triển khai quyền export và DOCX/MinIO; sau
đó triển khai enrollment dialog, Board Display và E2E audio LiveKit với
credential được cấu hình riêng ngoài source.

## Working tree — minutes lifecycle (chưa commit)

Đã triển khai phần lifecycle tiếp theo, đang chờ review/commit:

- Meeting Service có endpoint `minutes/review` và `minutes/approve`.
- Chỉ cho phép `DRAFT → REVIEWING → APPROVED`; transition sai trả `409`.
- Review/approve chỉ thực hiện sau khi runtime đã `COMPLETED`.
- eCabinet façade giới hạn approve cho chairperson/admin; secretary chỉ được
  gửi rà soát.
- `MinutesEditor` hiển thị nút gửi rà soát/duyệt theo trạng thái hiện tại.

Kiểm thử working tree:

- Named unittest: **112 tests passed**.
- Contract test, compile backend và frontend Vite build: đạt.
- Chưa triển khai DOCX/MinIO export trong slice này.
