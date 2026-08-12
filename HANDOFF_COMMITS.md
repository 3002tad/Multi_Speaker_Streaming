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

## Checkpoint P1-04 — 2026-08-12

- Root / Meeting AI + Meeting Service: `f32fcef`
  (`feature/meeting-platform-microservices`)
- eCabinet: `022ac25` (local-only, không push; không có thay đổi trong
  checkpoint này).

Phạm vi: tách entrypoint AI/Agent thành `meeting_ai`, giữ wrapper tương thích
`ai_server.py` và `agent.py`; bổ sung SessionManager, FastAPI adapter, Qdrant
speaker-store boundary và runner assignment cho streaming regression. Cơ chế
arbitration finalization được đổi sang adaptive theo global turn + EWMA latency
WavLM để tránh transcript final trùng khi mic yếu endpoint sớm.

Kiểm thử đã chạy: full WSL `pytest -q` đạt **165 pass, 6 subtests**; audio
unit **23 pass**; LiveKit dual-mic regression đạt 2 final, coverage 100% và
mọi gate WER/CER; E2E P1-06a full platform đạt runtime → LiveKit fixture →
transcript callback → Qwen minutes revision 1 → stop runtime. E2E test chỉ
dùng runner/process/container tạm và đã cleanup, không xóa volumes.

Giới hạn: transcript fixture Thầy Dũng vẫn có WER cao với thuật ngữ đặc thù;
đó là giới hạn ASR baseline, không phải lỗi contract/E2E. UI browser realtime,
enrollment, playback và Socket.IO acceptance còn thuộc P1-07. Bước tiếp theo:
hoàn tất các boundary còn lại của P1-04, lặp stress regression rồi chuyển
sang P1-05 container hóa AI/Agent.

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

## Checkpoint — Service authentication và LiveKit role permissions

Đã commit cục bộ theo cặp thay đổi đồng thời:

- Root / Meeting Service: branch `feature/meeting-platform-microservices`, commit `af97248` — `feat(security): enforce service auth and LiveKit role permissions`.
- eCabinet: branch `feature/meeting-platform-integration`, commit `de3d04c` — `feat(meeting): map server permissions to LiveKit workspace`.

Phạm vi thay đổi:

- Bắt buộc `X-Service-Key` cho internal Meeting Service API và AI callback; chuẩn hóa xác thực service-to-service.
- Runtime token chỉ chấp nhận JWT đã ký, kiểm tra binding meeting/runtime, issuer, audience, thời hạn, permissions và `jti`.
- LiveKit token không còn mặc định publish; quyền được suy ra từ permission `PUBLISH_AUDIO` do eCabinet cấp.
- Runtime snapshot và Socket.IO token dùng permission matrix theo role; UI MeetingRoom không lấy `localStorage` làm nguồn quyết định quyền mic/minutes.
- eCabinet mapping quyền server sang BFF, LiveKit token response và giao diện MeetingRoom.
- Không thay đổi thuật toán ASR, speaker identification hoặc tuning baseline.

Kiểm thử đã chạy:

- Full unit/contract suite trong WSL: **121 tests passed**.
- Edge headless E2E với cùng `runtime_session_id`:
  - Member: response `can_publish=true`, JWT `video.canPublish=true`, UI có nút `Bật micro`.
  - Observer: response `can_publish=false`, JWT `video.canPublish=false`, `video.canSubscribe=true`, UI không có nút micro.
  - Cả hai kết nối được cùng phòng LiveKit và đọc được transcript/biên bản.
- `git diff --check`: đạt trước khi commit.
- Hai tài khoản E2E đã được gỡ khỏi attendee; tài khoản còn ràng buộc lịch sử được vô hiệu hóa mềm.

Giới hạn còn lại:

- Chưa thực hiện kiểm thử thu microphone thật trong browser headless; kết quả publish được xác nhận bằng permission response và signed JWT claim.
- Chưa chạy E2E audio → Agent → ASR → transcript với mic vật lý.
- Chưa cập nhật public Nginx/healthcheck trên home server.

Đối chiếu merge plan: P0-02 và P0-03 đã hoàn tất; không có điểm lệch kiến trúc. Bước tiếp theo là triển khai contract session/assignment cho Meeting AI, chuyển Agent sang room động, rồi chạy E2E media thực tế. Repository `ecabinet` là local-only và không được push.

## Checkpoint — Hoàn tất P0-01: đồng bộ contract transcript và snapshot

Đã commit cục bộ theo cặp thay đổi đồng thời:

- Root / Meeting Service: branch `feature/meeting-platform-microservices`, commit `03e9a8a` — `feat(contract): complete P0-01 meeting contract sync`.
- eCabinet: branch `feature/meeting-platform-integration`, commit `6749312` — `feat(meeting): sync runtime snapshot contract`.

Phạm vi thay đổi:

- Chốt route transcript canonical `GET /internal/v1/meetings/{meeting_id}/transcript` và đồng bộ OpenAPI, FastAPI, eCabinet client/BFF.
- Triển khai `PUT /internal/v1/meetings/{meeting_id}/snapshot` với kiểm tra revision tăng dần, runtime tồn tại và trạng thái terminal; eCabinet vẫn là nguồn sự thật của snapshot.
- Chuẩn hóa lỗi internal API về `application/problem+json` với `code`, `message` và `correlation_id`.
- Không thêm `/minutes/analyze` vào Meeting Service; endpoint phân tích thuộc Meeting AI và được ghi ở P1-01.
- Cập nhật `IMPLEMENTATION_GAP_CHECKLIST.md` và `merge_feature_plan.md`; không xâm lấn module document/task/conclusion/voting/qlvb, không thay đổi thuật toán ASR/speaker baseline.

Kiểm thử đã chạy:

- Targeted contract + Meeting Service: **26 tests passed**.
- Full unit/contract suite trong WSL: **123 tests passed**.
- `git diff --check`: đạt ở root và eCabinet trước khi commit.

Giới hạn còn lại:

- Idempotency key và retry an toàn thuộc P0-04.
- Callback/event contract thuộc P0-05.
- Đồng bộ participant động với Meeting AI thuộc P0-06; snapshot hiện chưa tự phát assignment sang Agent.
- Chưa chạy lại regression nhiều mic ở checkpoint này vì thay đổi chỉ nằm ở contract/persistence, không ở audio/LiveKit.

Đối chiếu merge plan: P0-01 đã hoàn tất đúng phạm vi, additive và không làm thay đổi kiến trúc lõi eCabinet. Bước tiếp theo theo thứ tự là P0-04, P0-05, P0-06 rồi P0-07. Repository `ecabinet` là local-only và không được push.

## Checkpoint — Hoàn tất P0-04: active-session lock và idempotency

Đã commit cục bộ theo cặp thay đổi đồng thời:

- Root / Meeting Service: branch `feature/meeting-platform-microservices`, commit `2c9305a` — `feat(reliability): complete P0-04 runtime idempotency`.
- eCabinet: branch `feature/meeting-platform-integration`, commit `0ba851d` — `feat(meeting): propagate runtime idempotency keys`.

Phạm vi thay đổi:

- Thêm partial unique index cho một runtime chưa terminal trên mỗi `meeting_id` và atomic stop claim trước side effect AI.
- Hoàn thiện idempotency record cho start/stop/purge với request fingerprint và response replay; reuse key với payload khác bị từ chối.
- Bổ sung migration `0003_runtime_lock_idempotency_hash.py`.
- OpenAPI yêu cầu `Idempotency-Key` cho lifecycle start/stop/purge; eCabinet BFF truyền key của caller hoặc fallback ổn định cho UI cũ.
- Cập nhật checklist/merge plan và test concurrency/retry; không thay đổi ASR/DSP/LiveKit baseline hoặc module nghiệp vụ eCabinet ngoài session façade.

Kiểm thử đã chạy:

- Targeted P0-04 + contract: **38 tests passed**.
- Full unit/contract suite trong WSL: **129 tests passed**.
- Compile Meeting Service, tests và eCabinet session BFF: đạt.
- `git diff --check`: đạt trước commit ở cả hai repository.

Giới hạn còn lại:

- Chưa chạy contention test với PostgreSQL/Redis production thật; đã kiểm tra SQLite metadata và InMemory concurrent behavior.
- Callback persistence/ordering/retry vẫn thuộc P0-05; participant assignment động thuộc P0-06.

Đối chiếu merge plan: P0-04 đã đạt gate đúng phạm vi additive. Hai repository đã sạch sau commit; eCabinet là local-only và không được push. Bước tiếp theo là P0-05 callback persistence, ordering và retry.

## Checkpoint — Hoàn tất P0-05: callback persistence, ordering và retry

Đã commit cục bộ theo cặp trạng thái repository:

- Root / Meeting Service: branch `feature/meeting-platform-microservices`, commit `598d5e6` — `feat: hoàn thiện P0-05 callback persistence và retry`.
- eCabinet: branch `feature/meeting-platform-integration`, commit hiện tại `0ba851d` — không có thay đổi trong checkpoint này.
- Handoff: commit tài liệu được tạo ngay sau checkpoint code; eCabinet vẫn local-only và không push.

Phạm vi thay đổi:

- Mở rộng AI event contract với `transcript.updated`; validate schema version, event type, timestamp, runtime/meeting binding và trạng thái runtime.
- Persist/upsert `partial`, `final`, `updated`, `retracted` theo `segment_id` và `revision`; xử lý `accepted`, `duplicate`, `stale` theo event ID, sequence và revision.
- Commit transcript trong Meeting Service trước khi Socket.IO emit; REST rehydrate lọc partial/retracted đúng trạng thái persisted.
- Chuẩn hóa callback publisher trong Agent, tự tăng revision theo segment, bounded spool, retry/backoff/timeout và graceful flush khi dừng.
- Không thay đổi ASR/DSP, speaker identification, tuning baseline, LiveKit contract hoặc module nghiệp vụ eCabinet.

Kiểm thử đã chạy:

- Targeted P0-05 + contract + publisher: **39 tests passed**.
- Full unit/contract/regression suite trong WSL: **136 tests passed**.
- Compileall cho Agent, Meeting Service, Meeting AI và test publisher: đạt.
- `git diff --check`: đạt trước khi commit.
- SQLite đóng/mở lại xác nhận transcript final và event ID được khôi phục; test callback xác nhận DB commit xảy ra trước Socket.IO emit.

Giới hạn còn lại:

- Chưa fault-injection trên PostgreSQL/Redis/MinIO production hoặc outage mạng LiveKit thật.
- Spool của AI hiện bounded theo process; sequence sau AI process restart thuộc P0-06 assignment recovery.
- Chưa chạy E2E audio nhiều mic ở checkpoint này vì thay đổi chỉ nằm ở callback/persistence transport.

Đối chiếu merge plan: P0-05 đã đạt gate additive, không xâm lấn eCabinet. Bước tiếp theo theo thứ tự là P0-06 Agent assignment recovery, sau đó P0-07 multi-mic streaming regression.

## Checkpoint — P0-06 hoàn tất; P0-07/P0-08 đã có bằng chứng regression

Đã commit cục bộ theo cặp trạng thái repository:

- Root / Meeting AI + Meeting Service: branch `feature/meeting-platform-microservices`, commit `91b634d` — `feat(meeting-ai): recover assignments and harden multi-mic streaming`.
- eCabinet: branch `feature/meeting-platform-integration`, commit hiện tại `0ba851d` — không có thay đổi trong checkpoint này.
- eCabinet là repository local-only và không được push.

Phạm vi thay đổi:

- Persist/restore assignment control-plane trong Meeting AI, thêm epoch/generation và kiểm tra stale update để Agent có thể khôi phục sau restart AI process.
- Hoàn thiện contract/session state, runtime probe và các test assignment liên quan; compatibility wrapper `ai_server.py` và `agent.py` vẫn được giữ.
- Đổi scheduler Zipformer dùng chung sang fair round-robin theo mic, thêm telemetry và tăng queue audio nhằm tránh một mic chiếm toàn bộ hàng đợi.
- Bổ sung harness concurrent có control tuần tự, readiness pre-roll và đánh giá per-source; thử nghiệm decoder tách mỗi mic được giữ opt-in, mặc định tắt vì không cải thiện quality trong mẫu đo.
- Không thay đổi tuning Zipformer, VAD, DSP, ngưỡng speaker identification hay module nghiệp vụ eCabinet.

Kiểm thử đã chạy:

- Full unit/contract trong WSL: **147 passed**, 6 subtests passed.
- Targeted assignment/scheduler/contract: **27 passed**, 6 subtests passed.
- `compileall` và `git diff --check`: đạt.
- Streaming regression `truth.csv`: passed; dual-mic probe `33.25s` với đầy đủ metadata global-turn/quality.
- P0-08: ba cặp control/concurrent 2 mic đều có partial/final và không duplicate; median control WER/CER `0.3725/0.3203`, concurrent `0.3878/0.3493`.
- Acceptance 4 mic: structural pass, 4/4 nguồn có partial/final, không duplicate; WER/CER `0.4779/0.4405`.

Giới hạn còn lại:

- P0-07 và P0-08 chưa đạt quality gate: concurrent tăng median `+0.0153` WER, `+0.0290` CER so với control; 4 mic còn vượt locked baseline.
- Chưa có fixture riêng cho crosstalk (cùng audio qua nhiều mic) và true overlap (nhiều audio khác nhau cùng lúc), nên chưa thể sửa global-turn một cách có kiểm chứng.
- Chưa chạy fault injection PostgreSQL/Redis/MinIO hoặc LiveKit outage production.

Đối chiếu merge plan: P0-06 đạt đúng phạm vi additive. P0-07/P0-08 vẫn mở và đã được cập nhật evidence trong checklist; không có sai lệch kiến trúc. Bước tiếp theo là tạo hai fixture overlap, thiết kế gate global-turn theo correlation/identity rồi chạy lại 2/4 mic acceptance. Handoff tài liệu được commit cục bộ ngay sau checkpoint code; không push.

## Checkpoint — Hoàn tất P0-08: phân biệt crosstalk và true overlap

Đã commit cục bộ theo cặp trạng thái repository:

- Root / Meeting AI: branch `feature/meeting-platform-microservices`, commit `26e78b8` — `feat(global-turn): complete crosstalk overlap arbitration`.
- eCabinet: branch `feature/meeting-platform-integration`, commit hiện tại `0ba851d` — không có thay đổi trong checkpoint này.
- eCabinet là repository local-only và không được push.

Phạm vi thay đổi:

- Đưa speaker profile đã được WavLM chấp nhận vào candidate trước global-turn arbitration. Hai candidate có profile khác nhau được giữ lại để bảo toàn true overlap.
- Với speaker chưa ghi danh, gate chỉ gộp khi đồng thời có bằng chứng chồng thời gian, envelope âm học và tương đồng transcript; quy tắc mic yếu/crosstalk vẫn được giữ.
- Global turn so sánh candidate qua cả các `turn_id` lệch nhau để không tạo tail transcript khi VAD cắt cùng một tiếng nói ở hai mic khác thời điểm.
- Bổ sung fixture xác định cho crosstalk tuần tự và true overlap, cùng các unit test cho profile xung đột, unknown source và turn ID lệch.
- Không thay đổi tuning Zipformer, VAD, DSP, ngưỡng speaker identification, LiveKit contract hoặc module nghiệp vụ eCabinet.

Kiểm thử đã chạy:

- Full unit/contract suite trong WSL: **152 passed**, 5 warnings, 6 subtests passed.
- Targeted audio fixture/backend smoke: **32 passed**, 5 warnings.
- `compileall` cho Agent, Meeting AI, scripts và tests: đạt; `git diff --check`: đạt trước commit.
- LiveKit crosstalk regression: đạt, dual-mic probe `33.25s`, transcript count `4`, không có tail transcript sau cross-turn arbitration.
- LiveKit true overlap 2 mic: `2/2` nguồn có partial/final, không duplicate; WER/CER `0.3702/0.3370`.
- LiveKit true overlap 4 mic: `4/4` nguồn có partial/final, không duplicate; WER/CER `0.4810/0.4332`. Cần warmup `45s` do decoder thứ tư cold-start.

Giới hạn còn lại:

- P0-07 vẫn mở do quality ASR chưa đạt locked baseline khi true overlap 4 mic; không thực hiện thay đổi tuning trong checkpoint này.
- Chưa fault-injection PostgreSQL/Redis/MinIO production hoặc LiveKit outage production.

Đối chiếu merge plan: P0-08 đã đạt gate về điều phối crosstalk/true overlap, đúng phạm vi additive. Không có sai lệch kiến trúc. Bước tiếp theo theo ưu tiên là xử lý P0-07: phân tích quality/độ trễ multi-mic mà không làm suy giảm baseline và chạy lại regression 2/4 mic.

## Checkpoint — Hoàn tất P1-01: Minutes analysis control-plane

Đã commit cục bộ theo cặp repository:

- Root / Meeting Service + Meeting AI: branch `feature/meeting-platform-microservices`, commit `709575e` — `feat(minutes): add P1-01 evidence analysis control plane`.
- eCabinet: branch `feature/meeting-platform-integration`, commit `022ac25` — `feat(meeting): add minutes analysis trigger and status`.
- eCabinet là repository local-only; không push remote.

Phạm vi thay đổi:

- Meeting Service thêm bảng/migration `meeting_minutes_analyses`, lưu trạng thái
  `PENDING/RUNNING/SUCCEEDED/FAILED` và evidence bất biến lấy từ transcript final.
- Bổ sung `GET/POST /internal/v1/meetings/{meeting_id}/minutes/analyze`; Meeting
  Service chỉ gửi evidence qua internal contract tới Meeting AI. AI không truy cập
  PostgreSQL hoặc MinIO của Meeting Service/eCabinet.
- AI acceptance endpoint kiểm tra schema evidence và trả `202 accepted`; chưa
  gọi Qwen hoặc tạo `minutes.updated`, phần đó thuộc P1-02.
- eCabinet bổ sung façade theo quyền hiện có, API client và UI trigger/status/retry
  trong Meeting Workspace. Không sửa các module document/task/conclusion/voting/qlvb.
- `base_transcript_revision` dùng fingerprint tất định của tập segment final để
  tránh va chạm khi segment được sửa hoặc thêm mới.

Kiểm thử đã chạy:

- Full unit/contract suite trong WSL: **155/155 pass**.
- Python compile cho Meeting Service, Meeting AI, tests và eCabinet backend: đạt.
- Frontend production build trong image Docker: đạt.
- Meeting Service migration image build và Alembic head: `0006_minutes_analysis`.
- `git diff --check`: đạt trước commit; sau commit root và eCabinet đều sạch.

Giới hạn còn lại:

- Chưa chạy full Docker E2E PostgreSQL/Redis/MinIO + Qwen + callback
  `minutes.updated`; đây là phạm vi P1-02.
- Khi Meeting AI chưa cấu hình hoặc không phản hồi, analysis chuyển `FAILED` để
  UI retry; transcript realtime vẫn độc lập.
- WER/CER vẫn là ASR-Q1 đã deferred, không thay đổi trong checkpoint này.

Đối chiếu merge plan: P1-01 đã hoàn tất đúng phạm vi control-plane/evidence,
additive và không xâm lấn kiến trúc eCabinet. Bước tiếp theo là P1-02: Qwen
composer nhận evidence, tạo structured minutes và callback `minutes.updated`; sau
đó P1-03 xử lý stale result, manual revision và approved immutability.

## Checkpoint — Hoàn tất P1-03: revision guard và purge retry

Đã commit cục bộ theo cặp repository:

- Root / Meeting Service + Meeting AI: branch `feature/meeting-platform-microservices`, commit `27b3b8f` — `feat: enforce minutes revisions and retryable purge`.
- eCabinet: branch `feature/meeting-platform-integration`, commit hiện tại `022ac25` — không có thay đổi trong checkpoint này.
- eCabinet là repository local-only và không được push remote.

Phạm vi thay đổi:

- Manual edit dùng optimistic locking `base_revision`; conflict trả `409`.
- Revision `APPROVED` bất biến; chỉnh sửa sau duyệt chỉ sinh revision `DRAFT`.
- Evidence minutes lưu `base_minutes_revision` và `generation_id`; callback LLM
  kiểm tra đúng analysis/transcript snapshot, không ghi đè bản sửa tay hoặc bản
  đã duyệt. Callback stale trả trạng thái `stale` và worker không retry vô hạn.
- Thêm tombstone purge bền vững và migration `0009_purge_tombstones`; lỗi xóa
  object storage được giữ pending để retry, trong khi metadata Meeting Service
  được cascade/idempotent. Export dọn object nếu DB commit lỗi.
- Không thay thuật toán ASR/DSP/VAD/speaker-ID, không tạo FK/query chéo và
  không ghi sang module document/task/conclusion/voting/QLVB của eCabinet.

Kiểm thử đã chạy:

- Focused lifecycle/contract/worker/export: **49 pass**, 6 subtests pass.
- Full unit/contract suite trong WSL: **162 pass**, 5 warnings, 6 subtests pass.
- SQLAlchemy SQLite smoke cho APPROVED → DRAFT: đạt.
- Fault injection DB commit lỗi sau upload: object được cleanup; purge delete
  lỗi rồi retry: tombstone chuyển `PENDING` → `COMPLETED`.
- `git diff --check`: đạt; root và eCabinet đều sạch sau commit.

Giới hạn còn lại:

- Docker daemon hiện không khả dụng nên chưa chạy migration/partial-failure trên
  PostgreSQL/MinIO container thật và chưa chạy lại LiveKit E2E sau thay đổi
  persistence. Cần chạy gate này khi Docker hoạt động trước acceptance production.
- E2E production HTTPS/public network vẫn chưa chạy.

Đối chiếu merge plan: P1-03 đạt đúng lifecycle dữ liệu thuộc Meeting Service,
additive và không xâm lấn eCabinet. Bước tiếp theo theo thứ tự là P1-04 — hoàn
tất cấu trúc Meeting AI Core; sau đó P1-05 Compose full platform và đổi bridge
WSL sang internal DNS.

## Checkpoint — Hoàn tất P1-04: tách TranscriptCoordinator

Đã commit cục bộ theo cặp repository:

- Root / Meeting Service + Meeting AI: branch `feature/meeting-platform-microservices`,
  commit triển khai `177c66d` — `refactor: extract transcript coordinator`.
- eCabinet: branch `feature/meeting-platform-integration`, commit hiện tại
  `022ac25` — không có thay đổi trong checkpoint này.
- eCabinet là repository local-only và không được push remote.

Phạm vi thay đổi:

- Tách policy publish partial/final ra `meeting_ai/application/TranscriptCoordinator`;
  partial có throttle theo monotonic clock, reset theo global turn và final chống
  publish trùng khóa global turn.
- WebSocket handler chỉ kết nối transport callback; không thay decoder, DSP,
  VAD, speaker-ID hoặc ngưỡng nhận dạng. Compatibility wrapper `ai_server.py`
  và `agent.py` vẫn được giữ.
- Cập nhật checklist P1-04 với bằng chứng kiểm thử và giới hạn còn lại.

Kiểm thử đã chạy:

- Targeted coordinator/SessionManager/Agent/contract: **24 pass, 6 subtests**.
- Full WSL `pytest -q`: **167 pass, 5 warnings, 6 subtests**.
- `compileall`, `git diff --check` và compatibility import wrapper: đạt.
- Streaming regression: `DUAL_MIC_PROBE_OK`, 2/2 interval có transcript,
  không có `unassigned_tail`, overlap và WER/CER gate đạt.
- E2E full platform với `audio/thayDung_noi.wav`: `E2E_OK`, 1 final transcript,
  minutes revision 1, container/process do runner tạo đã cleanup.

Giới hạn còn lại:

- Chất lượng ASR khi nói nhanh và chuẩn hóa clock/latency vẫn thuộc P0-07/P1-06;
  không mở rộng trong checkpoint này.
- Chưa container hóa AI Core và LiveKit Agent; vẫn còn bridge process WSL.

Đối chiếu merge plan: P1-04 hoàn tất đúng phạm vi refactor additive, không truy
cập chéo database/MinIO và không xâm lấn eCabinet. Bước tiếp theo là P1-05:
Compose đầy đủ AI Core/Agent, mount runtime ngoài source và chuyển callback sang
internal DNS.

## Checkpoint — Hoàn tất P1-05: Compose Meeting Platform đầy đủ

Đã commit cục bộ theo cặp repository:

- Root / Meeting Service + Meeting AI: branch `feature/meeting-platform-microservices`,
  commit `be1cf87` — `feat: containerize meeting AI platform`.
- eCabinet: branch `feature/meeting-platform-integration`, commit hiện tại
  `022ac25` — không có thay đổi trong checkpoint này.
- eCabinet là repository local-only và không được push remote.

Phạm vi thay đổi:

- Bổ sung Compose overlay, Dockerfile và dependency lock riêng cho Meeting AI
  Core và LiveKit Agent; AI/Agent chạy container độc lập với Meeting Service.
- AI chỉ mount runtime ngoài source tại `/runtime`; model, Hugging Face cache,
  Qdrant và Ollama không được copy vào image hoặc repository. Agent không mount
  model/Qdrant.
- Chuyển đường gọi nội bộ sang DNS Compose `meeting-ai-api`, `meeting-service`
  và `ollama`; runner E2E không còn khởi chạy `ai_server.py`/`agent.py` native.
- Thêm profile count vào AI readiness để kiểm tra Qdrant persistence, giữ nguyên
  ASR/DSP/VAD/speaker-ID và các compatibility wrapper.
- Cập nhật hướng dẫn vận hành containerized và dọn các build cache/Python cache
  tái tạo được; không xóa image runtime, volume, model hoặc dữ liệu phiên họp.

Kiểm thử đã chạy:

- `git diff --check`, Compose config và `bash -n scripts/run_e2e_streaming.sh`:
  đạt.
- Full WSL `pytest -q`: **167 pass, 5 warnings, 6 subtests pass**.
- Build thật hai image AI/Agent: đạt.
- E2E containerized với `audio/thayDung_noi.wav`: `E2E_OK`, 1 transcript final,
  minutes revision 1; Agent → AI và AI → Meeting Service qua internal DNS trả
  HTTP 200.
- Restart AI container giữ Qdrant profile `15 → 15`; sau dọn cache, Meeting
  Service và Meeting AI healthcheck vẫn `ok`.

Giới hạn còn lại:

- P1-06 chưa hoàn tất readiness đầy đủ, telemetry cold-start/CPU/RAM, Qwen
  warm-up và graceful SIGTERM flush.
- P1-07 UI acceptance, P1-08 public Nginx và E2E ngoài mạng chưa thực hiện.

Đối chiếu merge plan: P1-05 hoàn tất đúng container/runtime boundary, additive
và không xâm lấn eCabinet. Bước tiếp theo theo ưu tiên là P1-06, sau đó P1-07.

## Checkpoint — Hoàn tất P1-06: cấu hình, readiness và vận hành

Đã commit cục bộ theo cặp repository:

- Root / Meeting Service + Meeting AI: branch `feature/meeting-platform-microservices`,
  commit `7fbdb31` — `feat: harden meeting platform operations`.
- eCabinet: branch `feature/meeting-platform-integration`, commit hiện tại
  `022ac25` — không có thay đổi trong checkpoint này.
- eCabinet là repository local-only và không được push remote.

Phạm vi thay đổi:

- Bổ sung template cấu hình private tách Meeting Service/Meeting AI; strict
  deployment từ chối key placeholder/yếu, runtime token thiếu và LiveKit thiếu
  cấu hình. Không có secret hoặc file env runtime nào được commit.
- Readiness Meeting Service kiểm tra PostgreSQL, Redis, MinIO và Meeting AI;
  AI báo trạng thái model/Qdrant/Ollama cùng cold-start, CPU/RSS. Qwen warm-up
  chạy nền nên không chặn realtime transcript.
- SIGTERM đợi callback minutes trong timeout, đóng final-turn scheduler và
  Qdrant rõ ràng; bổ sung test configuration/readiness/shutdown. Không thay
  ASR/DSP/VAD/speaker-ID hoặc ranh giới database giữa các service.

Kiểm thử đã chạy:

- Full WSL `pytest -q`: **174 pass, 7 warnings, 6 subtests pass**.
- Compile Python, `bash -n` runner và `git diff --check`: đạt.
- Compose strict khởi động với readiness DB/Redis/MinIO/AI `ok`; Ollama warm-up
  đạt. SIGTERM AI flush `pending_callbacks=0` và không còn warning Qdrant.
- E2E containerized với split env private và fixture audio: `E2E_OK`, 1
  transcript final, minutes revision 1; runner cleanup container/network mà
  không xóa volume.

Giới hạn còn lại:

- Cảnh báo deprecation `FastAPI on_event` và test JWT key ngắn của legacy test
  suite còn tồn tại, không thuộc P1-06.
- P1-07 UI acceptance, P1-08 public Nginx và E2E ngoài mạng chưa thực hiện.

Đối chiếu merge plan: P1-06 hoàn tất đúng config/readiness/operation,
additive và không xâm lấn eCabinet. Bước tiếp theo là P1-07 — UI acceptance
MeetingRoom; P1-08 chỉ bắt đầu sau khi P1-07 đạt.
