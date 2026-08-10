# Checklist khép các gap của Meeting Platform

> Checkpoint: 2026-08-07. Đây là backlog thực thi bắt buộc của
> `merge_feature_plan.md`, không phải danh sách feature tùy chọn.

## Quy tắc sử dụng bắt buộc

- Trước khi code, chọn chính xác ID task và đọc phần **Điều kiện đạt**.
- Cập nhật trạng thái cùng thay đổi code: `[ ]` chưa làm, `[-]` đang làm,
  `[x]` đã đạt gate, `[!]` bị chặn. Không đánh dấu `[x]` khi chưa có test.
- Mỗi task hoàn thành phải ghi test, kết quả, commit hash (nếu có), giới hạn
  còn lại vào **Nhật ký thực thi**.
- Không làm feature/refactor ngoài checklist khi còn task P0 chưa đạt. Nếu
  phát hiện gap mới, thêm task vào file này và cập nhật merge plan trước.
- eCabinet là repository local-only; không ghi secret, `.env`, model/cache
  hay runtime output vào checklist.

## Bằng chứng checkpoint đã có

- [x] Baseline lock tồn tại; DPDFNet/GTCRN không active mặc định.
- [x] Meeting Service có PostgreSQL migration, Socket.IO, transcript final,
  minutes lifecycle và DOCX/MinIO export.
- [x] eCabinet có façade, MeetingRoom sáng, playback opt-in và enrollment UI.
- [x] E2E cục bộ một nguồn audio: LiveKit → Agent → AI → callback →
  persisted transcript.
- [x] Named unit suite: 115 test pass.
- [ ] Các bằng chứng trên không thay thế acceptance gate dưới đây.

## P0 — Contract, quyền và realtime reliability

### P0-01 — Đồng bộ contract Meeting Service với implementation

**Cập nhật 2026-08-10:** `[-]` — route transcript đã chốt là số ít và OpenAPI
đã bỏ route chưa hiện thực; vẫn còn kiểm tra payload/idempotency/security.

**Trạng thái thực thi hiện tại:** `[-]` — đang đối chiếu FastAPI, OpenAPI và
runtime probe. P0-06 được kiểm thử cùng vì assignment Agent phụ thuộc trực tiếp
vào contract session này.

- [ ] Chốt endpoint transcript canonical (`/transcript` hoặc `/transcripts`).
- [ ] Đồng bộ OpenAPI, FastAPI routes, eCabinet client/UI và test app thật.
- [ ] Implement hoặc loại chính thức `PUT /snapshot` và `POST /minutes/analyze`.
- [ ] Đối chiếu HTTP method, payload, error code, idempotency và security.

**Điều kiện đạt:** không còn route contract thiếu hoặc route thực tế sai shape.

### P0-02 — Bảo vệ internal API và Socket.IO

**Cập nhật 2026-08-10:** `[-]` — bắt đầu chuẩn hóa `X-Service-Key`, bảo vệ
toàn bộ internal Meeting Service API và loại bỏ đường tắt verify JWT bằng claims
đã parse sẵn. Chưa thay đổi quyền LiveKit (thuộc P0-03).

- [ ] Áp dụng service authentication cho toàn bộ internal Meeting Service API.
- [ ] Chuẩn hóa `X-Service-Key`; alias cũ chỉ giữ có test/deadline loại bỏ.
- [ ] Xóa bypass chấp nhận object claims không ký ở `RuntimeTokenVerifier`.
- [ ] Bắt buộc kiểm tra JWT signature, issuer, audience, expiry, meeting,
  runtime, permissions và `jti`.

**Điều kiện đạt:** request sai key/token/meeting/runtime bị từ chối; Socket.IO
permission matrix pass.

### P0-03 — Permission server-side cho mic và minutes

  **Cập nhật 2026-08-10:** `[x]` — đã đối chiếu và acceptance role/permission từ eCabinet
với LiveKit token và runtime snapshot. Không thay đổi thuật toán audio.

  - [x] LiveKit token cấp `canPublish` theo signed claim, không luôn `true`.
  - [x] Observer/viewer không thể publish bằng UI lẫn SDK trực tiếp.
  - [x] `_runtime_snapshot()` không hard-code `CONTROL` cho mọi role.
  - [x] Chuẩn hóa và dùng `can_view_meeting`, `can_manage_meeting`,
  `can_control_ai`, `can_edit_minutes`, `can_approve_minutes`.
  - [x] UI chỉ dùng permission server/claim, không lấy `localStorage` làm nguồn
  quyết định quyền.

**Điều kiện đạt:** outsider `403`; member/observer không leo quyền;
chair/secretary/admin đúng ma trận plan.

### P0-04 — Active-session lock và idempotency thật

- [ ] Dùng Redis lock hoặc DB transaction/constraint nguyên tử cho start/stop.
- [ ] Dùng bảng idempotency cho start, stop và purge.
- [ ] Test concurrent start, retry timeout và stop lặp.

**Điều kiện đạt:** không tạo hai runtime active hoặc state DB mâu thuẫn.

### P0-05 — Callback persistence, ordering và retry

- [ ] Validate callback runtime tồn tại, đúng meeting và ở trạng thái hợp lệ.
- [ ] Final transcript commit DB trước Socket.IO emit.
- [ ] Persist/emit đầy đủ partial, final, updated, retracted; retraction sửa
  persisted state, không chỉ UI reducer.
- [ ] Xử lý accepted/duplicate/stale theo event ID, sequence và revision.
- [ ] Tách callback publisher, thêm bounded spool, retry/backoff/timeout và
  graceful flush khi Agent dừng.

**Điều kiện đạt:** tắt Meeting Service tạm thời rồi khôi phục vẫn nhận final
transcript đúng một lần; REST rehydrate đúng sau update/retraction.

### P0-06 — Agent assignment recovery

**Cập nhật 2026-08-10:** `[x]` — Agent recovery sau start/stop/start và AI
Core restart đã đạt bằng E2E LiveKit; static fallback vẫn mặc định tắt.

**Trạng thái thực thi hiện tại:** `[-]` — chạy sau khi contract session của
P0-01 có route và payload canonical; không thay đổi thuật toán ASR/DSP.

- [ ] Agent nhận session mới sau start → stop → start mà không restart.
- [ ] Agent hồi phục sau AI Core restart/reset generation.
- [ ] Thiết kế epoch/generation để cursor cũ không bỏ assignment mới hoặc
  join lặp room cũ.
- [ ] Static room fallback chỉ là compatibility flag, mặc định tắt.

**Điều kiện đạt:** test hai runtime liên tiếp và AI restart giữa hai phiên.

### P0-07 — Multi-mic streaming regression

- [ ] Chạy 2 rồi 4 track đồng thời từ `truth_1` theo frame production.
- [ ] Kiểm tra không track nào đứng, callback final đủ, không duplicate
  cross-mic và global-turn đúng.
- [ ] Lưu WER/CER/RTF/CPU/RAM; không kém baseline quá 0.01 tuyệt đối.

**Điều kiện đạt:** regression audio và E2E concurrent pass, không OOM/swap
thrashing kéo dài.

## P1 — Minutes AI, revision và lifecycle dữ liệu

### P1-01 — Nối Meeting Service `minutes/analyze`

- [ ] Meeting Service lấy transcript final PostgreSQL, tạo evidence snapshot
  có `base_transcript_revision` và gọi AI contract.
- [ ] Không cho AI truy cập PostgreSQL/MinIO trực tiếp.
- [ ] Thêm eCabinet façade, UI trigger/status và retry/degraded state.

**Điều kiện đạt:** analyze bất đồng bộ, transcript realtime không bị block.

### P1-02 — Implement AI minutes composition thật

- [ ] `/internal/v1/sessions/{runtime_id}/analyze` chạy Qwen composer, không
  chỉ trả `202`.
- [ ] AI gửi `minutes.updated` structured document, evidence và generation.
- [ ] Meeting Service persist DRAFT revision rồi mới Socket.IO broadcast.

**Điều kiện đạt:** transcript final tạo biên bản có evidence E2E.

### P1-03 — Revision conflict, approved immutability và purge

- [ ] Manual edit dùng optimistic locking `base_revision`.
- [ ] LLM result cũ không ghi đè manual revision mới hơn.
- [ ] Approved revision immutable; edit sau approve tạo DRAFT revision mới.
- [ ] Purge dùng tombstone/idempotency retry và cascade runtime, transcript,
  minutes, exports, events/idempotency record.
- [ ] MinIO upload/DB failure cleanup và MinIO delete failure durable retry.

**Điều kiện đạt:** test manual-edit-versus-LLM, partial transaction failure và
delete retry không để metadata/object mồ côi.

## P1 — Refactor AI Core và container hóa

### P1-04 — Hoàn tất cấu trúc Meeting AI

- [ ] Di chuyển FastAPI/API/WebSocket khỏi `ai_server.py` vào
  `meeting_ai/main.py`, `meeting_ai/api/` và application services.
- [ ] Hoàn tất SessionManager, TranscriptCoordinator, callback infrastructure
  và Qdrant store boundary.
- [ ] Di chuyển worker vào `meeting_ai/agent/`; `agent.py` chỉ là wrapper.
- [ ] Không thay ASR/DSP/speaker threshold trong commit refactor.

**Điều kiện đạt:** wrapper cũ/mới pass compatibility + streaming regression;
WER/CER giữ baseline.

### P1-05 — Compose Meeting Platform đầy đủ

- [ ] Thêm Dockerfile/requirements lock cho AI Core và LiveKit Agent.
- [ ] Compose có AI, Agent, Meeting Service, Redis, MinIO, PostgreSQL với
  health dependencies và internal DNS/network.
- [ ] Mount model/cache/Qdrant runtime ngoài source; restart không mất profile.
- [ ] Không cần chạy AI/Agent thủ công bằng process WSL.

**Điều kiện đạt:** container restart đạt transcript và Qdrant persistence.

### P1-06 — Config, readiness và operation

- [ ] Tách `.env.meeting`/`.env.ai`, inventory tuning runtime.
- [ ] Startup fail khi key placeholder/yếu hoặc LiveKit secret thiếu.
- [ ] Readiness Meeting Service kiểm tra DB/Redis/MinIO/AI; AI kiểm tra
  model/VAD/Qdrant/Ollama theo mode.
- [ ] Qwen warm-up không block transcript; SIGTERM flush final turn/callback.
- [ ] Ghi cold-start, peak CPU/RAM, degraded mode và safe shutdown result.

**Điều kiện đạt:** health/readiness/degraded/container restart pass.

## P1 — Frontend, public deployment và acceptance

### P1-07 — Hoàn tất MeetingRoom thực tế

- [ ] Test Socket reconnect + REST rehydrate, hai tab cùng meeting,
  transcript update/retraction/minutes update.
- [ ] Bổ sung playback gain có giới hạn; mặc định off và cảnh báo tai nghe.
- [ ] Chạy enrollment browser E2E: record, preview, upload, status, delete.
- [ ] Kiểm thử chair/member/observer qua backend thật, không chỉ ẩn nút.

**Điều kiện đạt:** reload không mất transcript/minutes; role flows pass.

### P1-08 — Nginx, deploy và public network

- [ ] Thêm proxy `/meeting-runtime/socket.io/`; giữ `/ws/` legacy nguyên vẹn.
- [ ] Build/deploy frontend production dạng release/symlink atomically.
- [ ] Không public AI 8001, Ollama, PostgreSQL, Redis, MinIO hoặc internal REST.
- [ ] Kiểm tra HTTPS, `/api`, Socket.IO, LiveKit WSS/UDP từ ngoài mạng.

**Điều kiện đạt:** public E2E qua `meet.simplething.id.vn` pass.

### P1-09 — Acceptance và bàn giao

- [ ] Test 3 laptop/mic: chair start, member join/mute/unmute, observer không
  publish.
- [ ] Test known/unknown speaker, đổi mic/vị trí, overlap/crosstalk.
- [ ] Test AI/Ollama/MinIO/Meeting Service unavailable, callback retry,
  revision conflict, safe stop/restart.
- [ ] Chạy baseline regression trong container và lưu snapshot so sánh.
- [ ] Cập nhật runbook start/stop/rollback, handoff và ZIP bàn giao sạch.

**Điều kiện đạt:** tất cả Definition of Done mục 15.5 của merge plan có bằng
chứng test.

## Nhật ký thực thi

### Cập nhật P0-01 — 2026-08-10

- Đồng bộ thêm response shape: runtime luôn trả `schema_version` và
  `created_at`; minutes rỗng dùng đúng `minutes-document` schema.
- OpenAPI không còn công bố `Idempotency-Key` hay `200 existing runtime` khi
  implementation chưa có idempotency storage. Hạng mục đó vẫn thuộc P0-04.
- Kiểm thử: 23 contract/Meeting Service tests pass; full unit suite 118 pass.
  Chưa commit. P0-01 vẫn mở cho kiểm tra input/error code sau khi P0-04 có
  idempotency thật.

| 2026-08-10 | P0-01 (một phần) | Chốt `/internal/v1/meetings/{meeting_id}/transcript` là canonical; OpenAPI bỏ `/snapshot` và `/minutes/analyze` chưa hiện thực; thêm test route thật. | Full unit suite đạt; còn payload/idempotency/security. | Chưa commit batch hiện tại. | `[-]` |
| 2026-08-10 | P0-06 | Thêm `assignment_epoch` cho AI Core và `AssignmentCursor` cho Agent để reset cursor khi AI restart. | start→stop→start đạt; restart riêng AI rồi runtime mới đạt 1 transcript; streaming dual-mic regression đạt 4 final segment, ngưỡng WER/CER đạt; 117 unit tests pass. | Chưa commit batch hiện tại. | `[x]` |

| Ngày | ID task | Bằng chứng/thay đổi | Test và kết quả | Commit root / eCabinet | Trạng thái |
|---|---|---|---|---|---|
| 2026-08-07 | Baseline checkpoint | Dynamic E2E một nguồn audio đã persist transcript; chưa phải acceptance multi-mic/container. | 115 named unit tests pass; runtime probe có 2 segment. | Chưa commit batch hiện tại. | `[-]` |

### Cập nhật P0-02 — 2026-08-10

- Đã áp dụng `X-Service-Key` bắt buộc cho toàn bộ route `/internal/v1` của
  Meeting Service và callback `/internal/v1/ai-events`; health vẫn public.
- Meeting Service gọi AI, Agent gọi AI/Meeting Service và eCabinet BFF gọi
  Meeting Service bằng header chuẩn. AI Core vẫn giữ alias
  `X-Internal-Api-Key` tạm thời để compatibility wrapper, nhưng caller mới
  không còn dùng alias này.
- `RuntimeTokenVerifier` chỉ nhận JWT dạng chuỗi, kiểm tra chữ ký, issuer,
  audience, hạn dùng, `jti`, permissions và từ chối claims object chưa ký.
- Kiểm thử: full unit suite **120 pass**; kiểm thử contract nằm trong suite và
  pass; test riêng verifier/auth route pass. Chưa chạy streaming regression vì
  thay đổi chỉ ở auth/contract, không thay đổi audio.
- Trạng thái: `[x]` cho gate P0-02 hiện tại. Việc loại bỏ alias cũ khỏi
  compatibility wrapper phải hoàn tất trước public production.

| 2026-08-10 | P0-02 | Bảo vệ internal API bằng `X-Service-Key`, chuẩn hóa caller, JWT strict verification. | Full unit + contract: 120 pass; auth route và unsigned claims đều bị từ chối. Chưa chạy streaming regression. | Chưa commit batch hiện tại; eCabinet local-only, không push. | `[x]` |

### Cập nhật P0-03 — 2026-08-10

- LiveKit token không còn mặc định `canPublish=true`; quyền được suy ra từ
  permission signed bởi eCabinet (`PUBLISH_AUDIO`). Observer chỉ nhận token
  subscribe, còn member/chair/secretary/admin được publish khi là thành viên
  hợp lệ.
- Runtime snapshot và Socket.IO token không còn hard-code `CONTROL`; actor
  permissions được tạo từ các helper `can_view_meeting`,
  `can_manage_meeting`, `can_control_ai`, `can_edit_minutes` và
  `can_approve_minutes`.
- Contract token bổ sung `permissions` request và `can_publish` response.
- Kiểm thử: full unit suite **121 pass**, contract và LiveKit token tests pass;
  đã kiểm tra token observer không publish. Chưa chạy browser/LiveKit server
  acceptance với role thật.
  - Trạng thái: `[x]` — acceptance role Member/Observer đã đạt trong cùng runtime
  qua LiveKit SDK và kiểm tra UI không thể publish khi thiếu quyền.

| 2026-08-10 | P0-03 | Permission matrix được chuyển vào eCabinet snapshot/token và LiveKit JWT; observer bị khóa publish mặc định. | Full unit + contract: 121 pass; browser role E2E đã bổ sung sau khi nạp cấu hình LiveKit. | Chưa commit batch hiện tại; eCabinet local-only, không push. | `[x]` |

### Bổ sung kiểm thử tích hợp — 2026-08-10

- Build Docker thành công cho Meeting Service, eCabinet backend và frontend;
  production Vite build thành công (129 modules).
- Smoke test Meeting Service create → status → stop → purge thành công với
  `X-Service-Key`; browser E2E đăng nhập → mở meeting → start runtime → tải
  Transcript/Minutes → Socket.IO join thành công.
- Đã phát hiện CORS origin local làm Socket.IO 403; xử lý ở cấu hình runtime
  test bằng cách thêm `http://127.0.0.1:3000`/`http://localhost:3000`, sau đó
  WebSocket được Meeting Service accept. Không thay đổi source production.
  - LiveKit token vẫn 503 trong local smoke vì không nạp URL/API key LiveKit;
    do đó chưa thể hoàn tất role E2E publish/observer bằng SDK thật.
  - Đã nạp cấu hình LiveKit local/remote từ runtime WSL (không lưu secret vào source),
    recreate Meeting Service và eCabinet API. E2E Chủ trì: đăng nhập → mở phiên →
    bắt đầu phòng → Room kết nối LiveKit thành công, UI hiển thị trạng thái đã kết
    nối và quyền bật micro. Console không có lỗi ứng dụng. Còn thiếu acceptance
    độc lập cho Member/Observer với LiveKit SDK thật; mục này đã được chạy lại ở
    acceptance role bên dưới.
  - Chạy lại E2E lần 2 trên Edge headless: đăng nhập admin → mở phiên `hop test` →
    bắt đầu phòng với runtime LiveKit đã cấu hình; kết quả Room kết nối thành công,
    hiển thị transcript nguồn, biên bản có cấu trúc và nút `Bật micro`, console không
    có lỗi ứng dụng. Full unit/contract suite: **121 pass**. Acceptance độc lập
    Member/Observer đạt: Member publish được, Observer chỉ subscribe; P0-03 đã đóng.
  - Acceptance role lần này: Member và Observer đăng nhập bằng hai browser context,
    truy cập cùng `runtime_session_id`. Member nhận response `can_publish=true`, JWT
    `video.canPublish=true` và UI có nút `Bật micro`; Observer nhận
    `can_publish=false`, JWT `video.canPublish=false`, `video.canSubscribe=true` và
    UI không có nút micro. Hai tài khoản tạm đã được gỡ khỏi attendee; do ràng buộc
    dữ liệu lịch sử, tài khoản được vô hiệu hóa mềm (`is_active=false`).
