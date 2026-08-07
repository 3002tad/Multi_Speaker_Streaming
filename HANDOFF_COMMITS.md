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
| eCabinet | `feature/meeting-platform-integration` | `b92ce1d` | Façade kết nối runtime Meeting |
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

## Minutes lifecycle — đã commit cục bộ

Đã triển khai và commit phần lifecycle tiếp theo:

- Root / Meeting Service: `572015e`
- eCabinet: `ca3ced1`

- Meeting Service có endpoint `minutes/review` và `minutes/approve`.
- Chỉ cho phép `DRAFT → REVIEWING → APPROVED`; transition sai trả `409`.
- Review/approve chỉ thực hiện sau khi runtime đã `COMPLETED`.
- eCabinet façade giới hạn approve cho chairperson/admin; secretary chỉ được
  gửi rà soát.
- `MinutesEditor` hiển thị nút gửi rà soát/duyệt theo trạng thái hiện tại.

Kiểm thử checkpoint:

- Named unittest: **112 tests passed**.
- Contract test, compile backend và frontend Vite build: đạt.
- Chưa triển khai DOCX/MinIO export trong slice này.
- Không repository nào được push lên remote.
## Checkpoint — DOCX/MinIO export

Đã triển khai và commit cục bộ theo merge plan:

- Meeting Service có `MinutesExportRecord`, migration `0004_minutes_exports.py`, renderer DOCX và object storage filesystem/MinIO.
- Export, metadata, download binary, idempotency và dọn object khi purge đã được nối vào Meeting Service.
- eCabinet façade kiểm tra quyền và UI `MinutesEditor` có nút Xuất DOCX tự tải file.
- OpenAPI đã mô tả request/response export, metadata và download.

Commit:

- Root / Meeting Service: `2d77247` — `feat(meeting): add minutes DOCX export and storage`.
- eCabinet: `5364ca2` — `feat(meeting): add minutes export facade and download UI`.

Kiểm thử: named unittest **114 tests passed**, contract/compile backend đạt, frontend Vite build trong Docker đạt, compose Meeting Service/Postgres/Redis/MinIO healthy và migration chạy thành công.

Review theo plan: persistence/lifecycle và export đã hoàn tất; không push repository eCabinet. Bước kế tiếp là enrollment dialog, Board Display và E2E audio LiveKit với credential ngoài source.

## Lịch sử cặp thay đổi code đồng thời

Bảng này chỉ ghi các commit có thay đổi code tương ứng ở cả hai repository.
Các commit chỉ cập nhật tài liệu, handoff hoặc test độc lập không đưa vào đây.

| Giai đoạn | Root / Meeting Service | eCabinet | Phạm vi thay đổi |
|---|---|---|---|
| Runtime token và façade đầu tiên | `6dfdd7a` | `87740ba` | Xác thực runtime token và tiếp tục tích hợp façade phiên họp |
| Transcript/minutes REST và UI workspace | `e3faffe` | `b92ce1d` | REST transcript/minutes, client façade và Meeting Workspace |
| Persistence/lifecycle callback và purge | `71564fb` | `d60d7a7` | Lưu transcript/minutes, callback bền vững và tombstone purge |
| Realtime workspace | `abd0b6a` | `0515115` | Socket.IO room và kết nối realtime trên UI |
| Sửa payload realtime và proxy dev | `c990709` | `75478a5` | JSON-safe AI event và Vite proxy REST/Socket.IO |
| LiveKit audio workspace | `c62bd6d` | `086b26c` | Token/room LiveKit, mic, playback và workspace audio |
| Contract chuẩn và MinutesEditor | `4c63753` | `3fcabc4` | Contract token/minutes revision và editor biên bản có cấu trúc |
| Vòng đời duyệt biên bản | `572015e` | `ca3ced1` | DRAFT → REVIEWING → APPROVED và phân quyền façade/UI |
| Export DOCX và object storage | `2d77247` | `5364ca2` | DOCX, MinIO/filesystem storage, metadata/download và quyền tải |

Các commit root/eCabinet còn lại trong lịch sử là commit tài liệu, checkpoint,
hoặc thay đổi riêng một repository; không được xem là cặp thay đổi đồng thời.

## Checkpoint — Voice Enrollment

Đã triển khai và commit cục bộ phần ghi danh giọng nói theo merge plan:

- Root / Meeting Service / AI: branch `feature/meeting-platform-microservices`, commit `ed38b29` — `feat(meeting): add voice enrollment internal contract`.
- eCabinet: branch `feature/meeting-platform-integration`, commit `4f62af4` — `feat(meeting): add voice enrollment dialog`.

Phạm vi thay đổi:

- AI có API nội bộ `POST/GET/DELETE /internal/v1/enrollments/{user_id}`; profile được khóa theo user ID, vẫn giữ endpoint `/enroll` cũ làm compatibility.
- Meeting Service nhận multipart audio, proxy tới AI và kiểm tra kích thước/AI availability.
- eCabinet chỉ cho user hiện tại ghi danh, xem trạng thái và xóa profile của chính mình.
- UI có nút `Ghi danh giọng nói` trong sidebar và dialog ghi WAV từ microphone, preview, gửi, ghi lại và xóa profile.
- Đoạn mẫu có 125 từ, yêu cầu UI tối thiểu 20 giây; backend chấp nhận từ 18 giây giọng sạch sau VAD để chừa khoảng ngắt tự nhiên.

Kiểm thử:

- Named unittest đầy đủ: **114 tests passed**.
- Contract test, compile backend/AI/eCabinet và frontend Vite production build: đạt.
- Headless Edge smoke test trên frontend eCabinet: mở layout, mở dialog, kiểm tra nội dung mẫu/nút và xử lý `Permission denied` khi browser không cấp microphone: đạt.
- Chưa chạy enrollment E2E thật qua Meeting AI/Qdrant vì local stack chưa bật đầy đủ auth, AI và microphone permission.

Review theo merge plan: Voice Enrollment đã hoàn tất; không thay đổi thuật toán ASR/speaker baseline. Board Display và public E2E còn thiếu. eCabinet là repository local-only, không được push.
Bước tiếp theo: triển khai Board Display read-only, sau đó chạy E2E login/join/record/enrollment/transcript/minutes/export bằng Edge và cập nhật Nginx/public healthcheck.

## Checkpoint — MeetingRoom light và kiểm tra runtime LiveKit

Đã commit cục bộ theo cặp thay đổi:

- Root / Meeting Service: branch `feature/meeting-platform-microservices`, commit `e75c12a` — `fix(meeting-service): complete enrollment delete and multipart runtime`.
- eCabinet: branch `feature/meeting-platform-integration`, commit `f1737a7` — `feat(meeting): unify MeetingRoom light workspace`.

Phạm vi thay đổi:

- Bổ sung `python-multipart` cho luồng multipart enrollment.
- Sửa endpoint xóa enrollment trả `204` đúng với FastAPI/Starlette hiện tại.
- Chuẩn hóa route `/meetings/:id/room` thành MeetingRoom duy nhất, giữ `/workspace` làm compatibility alias.
- Giao diện MeetingRoom dùng layout sáng của eCabinet, có transcript, MinutesEditor, playback và quyền mic theo role.
- Ẩn các nút thêm/xóa nội dung biên bản khi người dùng ở chế độ chỉ đọc.
- Nút từ MeetingDetail mở đúng MeetingRoom; nút bắt đầu runtime truyền `runtime_session_id`.

Kiểm thử đã chạy:

- Named unittest đầy đủ: **115 tests passed**.
- Contract test nằm trong bộ trên: đạt.
- Frontend Vite production build trong Docker: đạt; còn cảnh báo bundle JavaScript lớn hơn 500 KB.
- Edge headless E2E: đăng nhập → danh sách phiên họp → chi tiết → MeetingRoom; transcript và biên bản persisted hiển thị đúng; playback toggle hoạt động.
- LiveKit public diagnostic bằng PowerShell/SSH và SDK Python WSL: TLS, WebSocket, token và RTC UDP đều kết nối thành công.

Giới hạn còn lại:

- Runtime demo hiện có thể chuyển `FAILED` khi Meeting Service bật AI orchestration nhưng `ai_server.py` chưa cung cấp đầy đủ `/internal/v1/sessions` theo contract Meeting AI.
- Agent hiện vẫn dùng room tĩnh và callback legacy, chưa nhận assignment room động/callback event của Meeting Service. Vì vậy chưa đánh dấu E2E audio → Agent → transcript là đạt.
- Chưa sửa cấu hình Nginx/LiveKit trên home server; kiểm tra cho thấy hạ tầng LiveKit không phải nguyên nhân lỗi.

Review theo merge plan: đã hoàn thành vertical slice UI MeetingRoom light và kiểm tra hạ tầng LiveKit; còn thiếu contract session/assignment của Meeting AI và E2E media thực sự. Không thay đổi thuật toán ASR/speaker baseline. eCabinet là repository local-only, không được push.

Bước tiếp theo theo thứ tự ưu tiên: (1) triển khai/adapter API session và assignment cho Meeting AI, (2) chuyển Agent sang room động và callback Meeting Service, (3) dọn runtime FAILED theo quy trình an toàn rồi chạy E2E hai browser/mic, (4) cập nhật public healthcheck và kiểm thử lại trước checkpoint tiếp theo.
