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

**Cập nhật 2026-08-10:** `[x]` — đã hoàn thiện route snapshot, response/error shape
và canonical transcript; `minutes/analyze` được xác nhận thuộc Meeting AI/P1-01,
không giả lập route trong Meeting Service.

**Trạng thái thực thi hiện tại:** `[x]` — OpenAPI, FastAPI, eCabinet BFF/client và
test contract đã đồng bộ. P0-06 vẫn chờ contract session/assignment riêng.

- [x] Chốt endpoint transcript canonical là `/transcript`.
- [x] Đồng bộ OpenAPI, FastAPI routes, eCabinet BFF/client và contract tests.
- [x] Implement `PUT /snapshot`; xác nhận `POST /minutes/analyze` thuộc Meeting AI
  và deferred sang P1-01, không tạo route giả trong Meeting Service.
- [x] Đối chiếu HTTP method, payload, error code/problem response và security;
  idempotency được tách sang P0-04.

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

**Cập nhật 2026-08-10:** `[x]` — đã triển khai khóa active runtime bằng
constraint/transaction của Meeting Service, idempotency record cho start/stop/purge
và truyền `Idempotency-Key` từ eCabinet BFF; các test concurrent/retry/stop lặp đã đạt.

- [x] Dùng DB partial unique constraint cho một active runtime/meeting và atomic
  stop claim; service lock chống duplicate side effect trong cùng worker.
- [x] Dùng bảng idempotency với operation/key/request fingerprint/response cho
  start, stop và purge.
- [x] Test concurrent start, retry timeout và stop lặp.

**Điều kiện đạt:** không tạo hai runtime active hoặc state DB mâu thuẫn.

### Nhật ký thực thi — P0-04 — 2026-08-10

- Trạng thái: `[x]`.
- Meeting Service có partial unique index `uq_meeting_runtime_active_meeting` cho
  một runtime chưa terminal trên mỗi `meeting_id`; `claim_stop` chuyển sang
  `STOPPING` nguyên tử trước khi gọi AI.
- Bảng `meeting_idempotency_records` được mở rộng request fingerprint; start,
  stop và purge lưu/replay response, từ chối dùng lại key với payload khác.
- OpenAPI yêu cầu `Idempotency-Key` cho start/stop/purge; eCabinet BFF truyền key
  từ caller hoặc sinh fallback phù hợp cho UI cũ, không thay schema nghiệp vụ.
- Kiểm thử: targeted P0-04 + contract **38 pass**; full unit/contract suite
  **129 pass**; compile Meeting Service, tests và eCabinet session BFF đạt;
  `git diff --check` đạt.
- Giới hạn: chưa chạy contention test trên PostgreSQL/Redis production thật;
  SQLite metadata test và InMemory concurrent test đã pass. Retry purge qua
  eCabinet tombstone dùng key `purge:{meeting_id}` ổn định.
- Đối chiếu merge plan: P0-04 đã đạt gate, không thay đổi ASR/DSP/LiveKit
  baseline và không xâm lấn module eCabinet ngoài session façade. Batch chưa
  commit; eCabinet là repository local-only, không push.
- Bước tiếp theo: P0-05 callback persistence/ordering/retry, sau đó P0-06
  participant assignment động và P0-07 regression nhiều mic.

### P0-05 — Callback persistence, ordering và retry

**Cập nhật 2026-08-10:** `[x]` — đã hoàn tất callback validation theo runtime,
upsert transcript theo revision, persistence trước Socket.IO và bounded retry spool.
Đã bổ sung test duplicate/stale/retraction, callback failure recovery và reopen
database để mô phỏng khôi phục sau restart.

- [x] Validate callback runtime tồn tại, đúng meeting và ở trạng thái hợp lệ.
- [x] Final transcript commit DB trước Socket.IO emit.
- [x] Persist/emit đầy đủ partial, final, updated, retracted; retraction sửa
  persisted state, không chỉ UI reducer.
- [x] Xử lý accepted/duplicate/stale theo event ID, sequence và revision.
- [x] Tách callback publisher, thêm bounded spool, retry/backoff/timeout và
  graceful flush khi Agent dừng.

**Bằng chứng và giới hạn:** targeted P0-05/contract/publisher **39 pass**; full
unit/contract suite **136 pass**; compileall và `git diff --check` đạt. Test SQLite
đóng/reopen xác nhận transcript final và event ID được khôi phục đúng một lần,
đồng thời test emit xác nhận DB commit xảy ra trước Socket.IO. Chưa chạy fault
injection trên PostgreSQL/Redis/MinIO production hoặc outage mạng LiveKit thật;
spool hiện là process-local bounded queue và sequence không được khôi phục qua
AI process restart (phần này thuộc P0-06 assignment recovery).

**Đối chiếu merge plan:** P0-05 đã đạt gate, chỉ thay đổi event contract,
Meeting Service persistence và Agent callback transport; không thay đổi ASR/DSP,
speaker ID hoặc LiveKit baseline, không xâm lấn module eCabinet. eCabinet là
repository local-only, không push. Bước tiếp theo theo thứ tự ưu tiên: P0-06
Agent assignment recovery, sau đó P0-07 multi-mic streaming regression.

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

### Nhật ký thực thi — P0-01 — 2026-08-10

- Trạng thái: `[x]`.
- Đã chốt route transcript canonical là `GET /internal/v1/meetings/{meeting_id}/transcript`;
  OpenAPI, FastAPI Meeting Service và eCabinet client/BFF dùng cùng method, path và payload.
- Đã triển khai `PUT /internal/v1/meetings/{meeting_id}/snapshot` với kiểm tra
  `snapshot_revision` tăng dần, từ chối runtime không tồn tại hoặc đã kết thúc, và
  đồng bộ endpoint BFF eCabinet để tái tạo snapshot từ dữ liệu eCabinet.
- Lỗi của internal API đã thống nhất về `application/problem+json`, có `code`,
  `message` và `correlation_id`; test contract/validation đã được bổ sung.
- `POST /minutes/analyze` không được thêm vào Meeting Service: phân tích thuộc
  Meeting AI qua `/internal/v1/sessions/{runtime_session_id}/analyze`, được ghi rõ
  trong OpenAPI và plan, thực hiện ở P1-01.
- Kiểm thử: targeted contract + Meeting Service **26 pass**; full unit/contract
  suite **123 pass**. Không có thay đổi audio/ASR/LiveKit nên không phát sinh tuning
  baseline.
- Giới hạn còn lại: idempotency key thuộc P0-04; đồng bộ participant động với AI
  thuộc P0-06; snapshot hiện vẫn do eCabinet làm nguồn sự thật và không cho phép
  Meeting Service truy cập database eCabinet.
- Đối chiếu merge plan: P0-01 đã hoàn tất đúng phạm vi contract/persistence,
  additive, không xâm lấn các module document/task/conclusion/voting/qlvb. Batch
  hiện chưa commit; eCabinet là repository local-only, không push.
- Bước tiếp theo theo thứ tự ưu tiên: P0-04 (idempotency và retry an toàn), P0-05
  (callback/event contract), P0-06 (participant assignment động), sau đó P0-07
  (regression nhiều mic).
