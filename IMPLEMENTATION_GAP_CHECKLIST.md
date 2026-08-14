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

**Cập nhật 2026-08-10:** `[x]` — đã hoàn tất bảo vệ toàn bộ internal Meeting
Service API bằng `X-Service-Key` và loại bỏ đường tắt verify JWT bằng claims
object chưa ký. Alias `X-Internal-Api-Key` chỉ còn trong compatibility wrapper
của AI Core; caller mới dùng `X-Service-Key`.

- [x] Áp dụng service authentication cho toàn bộ internal Meeting Service API.
- [x] Chuẩn hóa `X-Service-Key`; alias cũ chỉ giữ trong compatibility wrapper
  và không được caller mới sử dụng.
- [x] Xóa bypass chấp nhận object claims không ký ở `RuntimeTokenVerifier`.
- [x] Bắt buộc kiểm tra JWT signature, issuer, audience, expiry, meeting,
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

**Cập nhật 2026-08-10:** `[x]` — đã hoàn thiện durable control-plane state
trong AI Core và test epoch/generation cho start→stop→start, AI restart.
Chưa đánh dấu đạt tới khi hai runtime liên tiếp và AI restart/reset generation
đều pass.

**Bằng chứng triển khai 2026-08-10:** AI Core đã lưu assignment active atomically
trong runtime state, restore lại sau process restart với `assignment_epoch` mới;
Agent reset cursor khi epoch mất/đổi và rời phòng khi runtime hoặc generation đổi
(kể cả start→stop→start dùng lại runtime row). Status từ Agent cũ bị AI Core bỏ qua
nếu epoch/generation không khớp; static fallback vẫn mặc định tắt.

- [x] Assignment state có schema/version, atomic replace, clear khi stop/terminal
  và tự bỏ qua file hỏng/khác schema.
- [x] Contract/OpenAPI bổ sung `assignment_epoch`; test cursor và generation rejoin.
- [x] Unit/contract/streaming regression hiện tại: **146 tests pass**; compileall đạt.
- [x] E2E LiveKit thật với audio tổng hợp và snapshot có participant đã pass:
  runtime `7a4b3158-f38a-431b-8c95-c32fbdde81ba`, room
  `meeting-5f01f4a8-debf-40dd-bc2e-b40b3f9c43e3`, Agent kết nối trước restart;
  AI restart làm epoch đổi `cb03f7cc1c73469dbde411a2cc51cafc` →
  `0c2277ae4ab461a9a0942d1d9d64c86`, giữ nguyên generation `4`, runtime/room;
  Agent ghi nhận reset cursor và rejoin, stop trả `COMPLETED`, state file được clear.
- [ ] Giới hạn: fixture dùng tone nên transcript có `0` segment; publisher probe
  không tự thoát sạch khi LiveKit remote disconnect nên đã stop runtime/terminate
  probe có kiểm soát sau khi assertion control-plane pass. Chưa kết luận WER/ASR.
- [x] Bổ sung hardening cần cho E2E: Meeting Service AI timeout cấu hình được
  (mặc định 60s), probe gửi `X-Service-Key` cho token/transcript; migration
  `0003_runtime_lock_hash` đã áp dụng trên PostgreSQL volume.

- [x] Agent nhận session mới sau start → stop → start mà không restart (đã có
  generation/rejoin guard và state lifecycle test).
- [x] Agent hồi phục sau AI Core restart/reset generation ở control-plane state
  (đã có atomic persist/restore và cursor reset test).
- [x] Thiết kế epoch/generation để cursor cũ không bỏ assignment mới hoặc
  join lặp room cũ.
- [x] Static room fallback chỉ là compatibility flag, mặc định tắt.

**Điều kiện đạt:** test hai runtime liên tiếp và AI restart giữa hai phiên.

### P0-07 — Multi-mic streaming reliability

**Quyết định phạm vi 2026-08-12:** `[x]` — P0 được chốt theo reliability của
vertical slice: nhiều track không được đứng/mất final, callback phải persist đúng
một lần, arbitration phải giữ speaker độc lập, và không được OOM/swap thrashing.
Chất lượng WER/CER đa mic không đạt baseline khóa được ghi nhận minh bạch bên dưới
và chuyển sang track ASR-Q1; đây **không phải** xác nhận chất lượng ASR đã đạt.

- [x] Chạy 2 rồi 4 track đồng thời từ `truth_1` theo frame production.
- [x] Kiểm tra không track nào đứng, callback final đủ, không duplicate
  cross-mic.
- [x] Phân biệt global-turn crosstalk (cùng người vọng qua nhiều mic) với
  nhiều người thực sự phát biểu chồng nhau.
- [x] Lưu WER/CER/RTF/CPU/RAM và chốt giới hạn tài nguyên.
- [!] Quality ASR: chưa đạt điều kiện WER/CER không kém baseline quá `0.01`
  tuyệt đối; được tách sang ASR-Q1, không được che giấu trong acceptance P0.

**Điều kiện đạt P0:** regression audio và E2E concurrent pass về structural
reliability, không OOM/swap thrashing kéo dài. Chất lượng transcript vẫn phải
được hiển thị là draft/evidence và biên bản phải cho phép review/chỉnh sửa.

**Nhật ký chạy 2026-08-10:**

- Harness đã gọi lifecycle `meeting/create` trước mỗi run để xoá transcript,
  reset adaptive dictionary/topic window và tránh kết quả phụ thuộc thứ tự test.
  Compatibility regression dùng `AGENT_ASSIGNMENT_ENABLED=false` vì probe
  legacy gọi `/api/meeting/join`, chưa tạo Meeting Service runtime assignment.
- 2-track: structural pass; mỗi nguồn có partial và final, không track đứng.
  Run sạch gần nhất ghi 17/16 partial và 2/1 final; `meeting_dictionary_reset=true`.
- 4-track: structural pass; cả 4 nguồn có partial/final, không duplicate
  `segment_id` (0 duplicate), không OOM/swap. Run sạch gần nhất có 93 event và
  5 final persisted.
- Queue audio mỗi mic tăng từ 400 lên 1200 frame (~24 giây) để vòng nhận frame
  không bị block khi scheduler Zipformer dùng chung; không thay model, beam,
  VAD threshold hoặc speaker threshold.
- Quality gate **chưa đạt**: các 4-track run sạch dao động mean WER
  `0.4510`–`0.6226`, CER `0.3773`–`0.5935`; lần chạy evaluator gần nhất là
  WER `0.6226`, CER `0.5935`. A/B tắt glossary ghi WER `0.4774`, CER
  `0.4514`, nên glossary không phải nguyên nhân duy nhất. Baseline truth_1 là
  WER `0.3126`, CER `0.2780`; mọi run đều vượt ngưỡng tăng `0.01`.
- Tài nguyên lần đo 4-track: wall `78.24s`, CPU `86%`, max RSS wrapper
  `1,824,796 KB` (~1.74 GiB), swap `0`; aggregate final RTF `0.2493`.
- Kiểm thử sau thay đổi: full named unit/contract suite **146 pass**,
  compileall đạt, `git diff --check` đạt. P0-07 vẫn giữ `[-]`, không được
  đánh dấu hoàn thành cho tới khi quality gate đạt.
- Scheduler fair round-robin theo `source_identity` đã thay FIFO ngầm nhưng
  vẫn chỉ gọi Zipformer trên **một** worker thread; thứ tự trong mỗi mic được
  giữ nguyên. Unit scheduler kiểm tra serialization, exception recovery và
  luân phiên A1 → B1 → A2 → B2 đạt.
- Harness sửa lỗi chấm 2 mic bằng cả 4 dòng truth: nay chỉ so sánh các voice
  đã publish, lấy baseline theo từng voice trong `baseline/manifest.json` và
  lưu telemetry scheduler từ `/health/ready`.
- Run 2 mic sau fair scheduler: 2/2 partial/final, WER `0.3702`, CER `0.3452`
  so với baseline tương ứng WER `0.2182`, CER `0.1910`; scheduler `1373`
  thao tác, pending tối đa `2`, chờ tối đa `40.8 ms`.
- Run 4 mic sau fair scheduler: 4/4 partial/final, `9` final, `0` duplicate;
  WER `0.6085`, CER `0.5513` so với baseline WER `0.3126`, CER `0.2780`;
  scheduler `2102` thao tác, pending tối đa `4`, chờ tối đa `117.7 ms`.
  Không có bằng chứng scheduler/queue bị nghẽn; các VAD turn đồng thời vẫn
  dùng chung global-turn nên transcript của nhiều speaker bị cạnh tranh.
- Kiểm thử sau lần tối ưu này: full named unit/contract suite **147 pass**,
  compileall và `git diff --check` đạt. P0-07 tiếp tục `[-]` vì quality gate
  và phân biệt overlap chưa đạt.
- `scripts/streaming_regression.py` tuần tự theo fixture baseline `truth.csv`
  vẫn **passed** (dual-mic probe `33.25s`, đầy đủ overlap/global-turn/quality
  metadata). Đây là smoke/regression cho compatibility wrapper, không phải
  control cùng fixture `truth_1`.
- Harness P0-07 nay có `--schedule sequential` để tạo control qua chính
  LiveKit/VAD/finalization; offline `truth_1` chỉ còn là tham chiếu chẩn đoán.
  Control 2 mic ghi WER `0.3749`, CER `0.3346`; concurrent 2 mic sau đó ghi
  WER `0.3901`, CER `0.3763`, scheduler max pending `2`, max wait `114.5 ms`.
  Một lần chạy chưa đủ loại trừ dao động VAD/LiveKit, vì vậy chưa được coi là
  bằng chứng degradation thuật toán hoặc đạt quality gate.
- Sau khi bổ sung readiness gate bằng silence pre-roll, một cặp control/concurrent
  mới ghi lần lượt WER/CER `0.3041/0.2640` và `0.3486/0.3060`; concurrent tăng
  `+0.0445/+0.0420`, nên quality gate vẫn chưa đạt. Kết quả này là bằng chứng
  cần lặp thêm trong P0-08, không phải lý do để đổi Zipformer/DSP.
- Acceptance 4 mic sau readiness fix đạt structural pass: 4/4 nguồn có partial,
  4/4 có final, `0` duplicate; evaluator ghép được `5` final. WER `0.4779`,
  CER `0.4405` so với locked baseline tương ứng `0.3126/0.2780`; scheduler
  chung ghi `2.712` thao tác, pending tối đa `4`, wait tối đa `102.0 ms`, queue
  wait tối đa `96.1 ms`, không pending sau khi kết thúc. Đây vẫn là quality
  regression, chưa đạt ngưỡng baseline + 0.01.
- Đã đủ ba lần lặp 2 mic cho control tuần tự và concurrent. Median control:
  WER/CER `0.3725/0.3203`, scheduler max wait `84.2 ms`, queue wait `0.5 ms`;
  median concurrent: WER/CER `0.3878/0.3493`, max wait `93.6 ms`, queue wait
  `82.3 ms`. Delta concurrent-control là `+0.0153` WER và `+0.0290` CER;
  cả 6 run đều có partial/final cho mọi nguồn và không duplicate. Chưa đạt
  tolerance WER +0.01 và chưa có bằng chứng crosstalk/true-overlap riêng.
- A/B boundary 2026-08-10: `ASR_SOFT_SPLIT_SECONDS=30` loại split nội bộ
  ngay trước VAD END trên clip ~14 giây; `VAD_MIN_SILENCE_SECONDS=1.4` giảm
  các END→START ngắt trên lời nói nhanh. Run LiveKit 4 mic với hai giá trị này
  ghi `4/4` partial/final, 5 final stored, 0 duplicate, WER/CER
  `0.5108/0.4603` (tốt hơn A/B soft-split 30 một mình `0.5851/0.5381` nhưng
  chưa đạt locked baseline). Hai giá trị được đặt làm default; độ trễ final
  tại endpoint tăng thêm tối đa ~0.5s. Agent→AI handshake timeout cũng được
  nâng lên 30s: acceptance 4 mic không còn fail toàn bộ track do opening
  handshake. Full unit suite sau thay đổi: **152 pass**.

**Giới hạn được chấp nhận cho MVP:** control streaming lặp đã đủ mẫu và P0-08
đã tách crosstalk khỏi true overlap trước EventSink. WER/CER đa mic vẫn vượt
locked baseline; không tự ý đổi tuning Zipformer, VAD hoặc speaker threshold
trong checkpoint P0. Đây là đầu vào bắt buộc của ASR-Q1, không phải lý do để
ngầm coi transcript là chính xác.

### Nhật ký quyết định — P0-07 — 2026-08-12

- Phạm vi P0 được chốt theo reliability integration: 2/4 mic không đứng,
  global-turn không triệt speaker độc lập, callback/persistence không duplicate,
  E2E LiveKit → Agent → AI → Meeting Service đã có transcript persisted và
  không có OOM/swap thrashing kéo dài.
- Evidence chất lượng được giữ nguyên: 4 mic `truth_1` đạt structural pass nhưng
  WER/CER tốt nhất gần đây vẫn vượt baseline khóa; các A/B scheduler, glossary,
  isolated decoder và endpointing chưa cho cải thiện ổn định đủ để đổi model/tuning.
- Quyết định sản phẩm: không tiếp tục tối ưu heuristic trong sprint integration;
  transcript được coi là evidence/draft và P1 minutes bắt buộc có nguồn,
  revision, review và chỉnh sửa thủ công. ASR-Q1 là track độc lập sau MVP.
- Không có thay đổi thuật toán ASR, DSP, VAD hay speaker threshold trong quyết định
  này. Bước kế tiếp: chuẩn bị P1-01 contract/evidence snapshot; sau đó mới thực
  hiện code P1-01.

### P0-08 — Control streaming lặp và phân biệt overlap đa speaker

**Cập nhật 2026-08-10:** `[x]` — đã đủ ba cặp control/concurrent 2 mic, có
readiness pre-roll, fixture chuyên biệt và gate crosstalk/true-overlap trước
EventSink; P0-07 vẫn mở riêng cho quality ASR đa mic.

- [x] Chạy control `truth_1` tuần tự và concurrent tối thiểu ba lần/2 mic;
  báo cáo median per-source WER/CER và scheduler wait để xác định delta thật.
- [x] Thiết kế gate theo tương quan/nội dung/identity để chỉ gộp mic khi có
  bằng chứng là cùng nguồn giọng; giữ các stream khác speaker độc lập.
- [x] Bổ sung fixture: cùng một audio phát qua nhiều mic (crosstalk) và nhiều
  audio khác nhau phát cùng lúc (true overlap).
- [x] Chạy 2/4 mic acceptance, so sánh per-source WER/CER với baseline khóa;
  xác nhận không duplicate transcript và không làm đứng track.

**Điều kiện đạt:** `[x]` global-turn không triệt transcript của speaker độc lập,
nhưng vẫn khử duplicate khi cùng người vọng sang nhiều mic.

**Nhật ký chạy P0-08 — 2026-08-10:**

- Harness trước đây chờ `active_streams` trước khi phát frame nên tạo deadlock
  với Agent; đã sửa bằng 5 giây silence pre-roll, sau đó mới chờ
  `/health/ready.active_streams` rồi phát phần speech đo WER. Probe phải chạy
  với `AGENT_ASSIGNMENT_ENABLED=false` vì đây là compatibility fixture gọi
  `/api/meeting/join`, không tạo runtime assignment của Meeting Service.
- Một control tuần tự 2 mic qua đúng LiveKit/VAD/finalization đạt 2/2 partial và
  final, WER `0.3041`, CER `0.2640`; scheduler dùng chung: 1.414 thao tác,
  pending tối đa `1`, wait tối đa `79.9 ms`, queue wait tối đa `0.4 ms`.
- Một run concurrent 2 mic dùng scheduler chung đạt 2/2 partial và final,
  không duplicate (`0`), WER `0.3486`, CER `0.3060`; delta so với control là
  `+0.0445` WER và `+0.0420` CER. Scheduler: 1.316 thao tác, pending tối đa
  `2`, wait tối đa `93.6 ms`, queue wait tối đa `82.3 ms`; queue từng mic
  không block (max depth 5/4, blocked puts 0).
- Thử nghiệm opt-in `ASR_ISOLATED_MIC_DECODERS=true` vẫn giữ mặc định tắt:
  structural pass, WER `0.3731`, CER `0.3105`, chậm hơn control; cold-start
  decoder đầu tiên `3.172 s`, decoder thứ hai `85 ms`, không có lợi thế chất
  lượng trong mẫu hiện tại. Đây chỉ là A/B, chưa thay đổi baseline và chưa
  được chọn làm kiến trúc mặc định.
- Gate mới chạy sau WavLM identity: hai accepted voice profile khác nhau luôn
  được giữ; speaker chưa enroll chỉ bị khử khi có time overlap, acoustic envelope
  và text cùng khớp, hoặc là bản vọng yếu rõ ràng. Candidate từ global-turn ID
  lệch vẫn được so sánh qua cùng gate để xử lý VAD split không đồng bộ.
- Fixture crosstalk dùng `build_sequential_cross_mic_audio`: cùng tín hiệu qua
  hai mic với gain khác nhau; true-overlap dùng hai recording khác nhau từ
  `truth_1`. Unit fixture/gate pass.
- E2E crosstalk LiveKit (`truth.csv`) pass toàn bộ check; transcript count trở
  về `4`, không còn fragment vọng thừa sau cross-turn arbitration. True-overlap
  2 mic pass 2/2 partial/final, WER/CER `0.3702/0.3370`; true-overlap 4 mic pass
  4/4 partial/final, `0` duplicate, WER/CER `0.4810/0.4332`. Run 4 mic cần
  readiness warmup `45s` thay vì `20s` do decoder thứ tư cold-start chậm.
- P0-08 đạt gate arbitration. P0-07 vẫn `[-]` vì WER/CER đa mic so với locked
  baseline chưa đạt, không được suy diễn là lỗi của gate crosstalk.

## P1 — Minutes AI, revision và lifecycle dữ liệu

### ASR-Q1 — Track chất lượng transcript sau MVP integration

- [ ] Đánh giá model/fine-tune ASR bằng fixture thực tế nói nhanh, nhiễu và
  overlap; không gộp với refactor/service contract.
- [ ] Thiết lập baseline chất lượng streaming có thể tái lập theo từng topology
  mic trước khi đổi model hoặc decoding.
- [ ] Chỉ thay model/tuning sau benchmark riêng; giữ transcript raw, evidence
  và khả năng rollback khi kết quả không tốt hơn.

**Trạng thái:** deferred theo quyết định phạm vi 2026-08-12. Đây là rủi ro chất
lượng đã biết, không phải P1-01; không tuyên bố WER/CER P0 đã đạt.

### P1-01 — Nối Meeting Service `minutes/analyze`

**Chuẩn bị 2026-08-12:** sẵn sàng bắt đầu sau P0 reliability. Phạm vi chỉ là
control-plane/evidence snapshot; không thay đổi ASR, DSP, VAD, speaker ID hoặc
đọc trực tiếp PostgreSQL/MinIO từ AI.

**Trạng thái thực thi 2026-08-12:** `[x]` — đã hoàn tất control-plane/evidence
snapshot, façade/UI status và degraded/retry state. P1-02 Qwen composition vẫn
để riêng.

- [x] Meeting Service lấy transcript final PostgreSQL, tạo evidence snapshot
  có `base_transcript_revision` và gọi AI contract.
- [x] Không cho AI truy cập PostgreSQL/MinIO trực tiếp.
- [x] Thêm eCabinet façade, UI trigger/status và retry/degraded state.

**Điều kiện đạt:** analyze bất đồng bộ, transcript realtime không bị block.

**Thứ tự triển khai:**

1. Chốt request/response `minutes/analyze`, idempotency và trạng thái
   `PENDING/RUNNING/SUCCEEDED/FAILED` tại Meeting Service.
2. Tạo evidence snapshot immutable từ transcript final có
   `base_transcript_revision`, rồi gọi AI bằng internal contract.
3. Bổ sung eCabinet façade và UI trigger/status/retry theo quyền hiện có.
4. Viết contract test, retry/degraded test và xác nhận analyze không chặn
   Socket.IO/partial transcript trước khi sang P1-02.

### Nhật ký thực thi — P1-01 — 2026-08-12

- Trạng thái: `[x]`.
- Meeting Service thêm bảng/migration `meeting_minutes_analyses`, lưu trạng thái
  `PENDING/RUNNING/SUCCEEDED/FAILED` và evidence immutable từ transcript final.
  `base_transcript_revision` là fingerprint tất định của snapshot; không dùng
  phép cộng revision có thể va chạm khi thêm/sửa segment.
- `POST/GET /internal/v1/meetings/{meeting_id}/minutes/analyze` chỉ gửi evidence
  qua internal contract tới Meeting AI. AI acceptance endpoint kiểm tra schema
  và trả accepted; chưa gọi Qwen hay ghi biên bản trong P1-01.
- eCabinet thêm façade theo quyền hiện có và nút/status/retry trên MeetingRoom.
  API public không trả evidence cho UI/BFF. Không service nào cho AI truy cập
  PostgreSQL hay MinIO của Meeting Service/eCabinet.
- Kiểm thử: compile Python đạt; full unit/contract suite **155 pass**; frontend
  production build trong image Docker đạt; migration graph đạt
  `0006_minutes_analysis (head)`; `git diff --check` đạt.
- Giới hạn: chưa chạy Docker E2E có Qwen/callback `minutes.updated`; đây là
  phạm vi P1-02. Khi Meeting AI chưa cấu hình/không phản hồi, trạng thái chuyển
  `FAILED` để UI có thể retry, transcript realtime vẫn độc lập.
- Đối chiếu merge plan: đúng P1 control-plane, additive trong Meeting Service và
  façade session; không sửa document/task/conclusion/voting/qlvb, ASR/DSP/VAD
  hay speaker identification. eCabinet vẫn local-only, không push.
- Bước tiếp theo: P1-02 — worker Qwen nhận evidence, tạo structured document và
  callback `minutes.updated`; sau đó P1-03 xử lý stale result/manual revision.

### P1-02 — Implement AI minutes composition thật

**Trạng thái thực thi 2026-08-12:** `[x]` — đã nối Ollama/Qwen composer,
structured validation, callback `minutes.updated` và persistence/broadcast DRAFT.
Automated gate và full Docker E2E với Ollama/Qwen thật đã đạt. P1-03 revision
conflict vẫn để riêng.

- [x] `/internal/v1/sessions/{runtime_id}/analyze` chạy Qwen composer, không
  chỉ trả `202`.
- [x] AI gửi `minutes.updated` structured document, evidence và generation.
- [x] Meeting Service persist DRAFT revision rồi mới Socket.IO broadcast.

**Điều kiện đạt:** transcript final tạo biên bản có evidence E2E.

### Nhật ký thực thi — P1-02 — 2026-08-12

- Trạng thái: `[x]` — implementation slice và acceptance E2E đã đạt.
- Meeting AI thêm `MinutesWorker`: nhận evidence bất biến, gọi
  `OllamaMinutesComposer` với `qwen2.5:3b`, ép `think=false` qua composer hiện có,
  validate mode `llm`, rồi gửi callback `minutes.updated` bất đồng bộ. AI không
  ghi database/MinIO và không nằm trên audio/transcript realtime loop.
- Callback có `analysis_id`, `generation_id`, `base_transcript_revision`,
  structured document và `generator_meta`. Meeting Service kiểm tra schema,
  kiểm tra mọi `source_segment_ids` thuộc transcript final đã persist, lưu revision
  `DRAFT`, cập nhật analysis `SUCCEEDED` và chỉ sau đó broadcast Socket.IO.
- Thêm degraded callback `pipeline.warning` cho lỗi Qwen; analysis chuyển
  `FAILED` và UI có thể retry. Sequence transcript/control được tách domain để
  callback minutes không làm transcript tiếp theo bị stale.
- Cấu hình mặc định minutes composer là `llm`; `timeline` vẫn tồn tại như
  fallback explicit cho probe cũ. Không thay đổi ASR/DSP/VAD/speaker-ID.
- Kiểm thử: targeted P1-02 **54 pass**; full unit/contract/regression suite
  trong WSL **158 pass**; compile Python đạt; frontend production build và
  Meeting Service/migration Docker images build đạt. E2E thật dùng
  `audio/thayDung_noi.wav` qua LiveKit: transcript final được persist với
  `speaker=Thay_Dung`, phonetic recovery nhận `mục 5.2`/`Hadoop Storage`,
  `/minutes/analyze` trả `202`, Qwen2.5:3B callback làm analysis `SUCCEEDED`
  và tạo DRAFT revision 1 có `source_segment_ids`. Thêm migration `0007`
  cho fingerprint transcript BIGINT và `0008` cho callback sequence BIGINT;
  migration chạy thành công.
- Giới hạn: E2E production network/HTTPS chưa chạy; callback E2E local dùng
  loopback WSL vì Agent chạy ngoài Docker. Chưa có thay đổi chất lượng ASR;
  ASR-Q1 vẫn deferred.
- Đối chiếu merge plan: P1-02 hoàn tất đúng phạm vi biên bản trong phiên họp,
  additive; không ghi decision/action sang task, conclusion, Văn bản chỉ đạo,
  document, voting hoặc QLVB. eCabinet Core không đổi schema.
- Bước tiếp theo: P1-03 — optimistic locking/manual edit, stale result,
  approved immutability và purge/outbox failure handling.

### P1-03 — Revision conflict, approved immutability và purge

**Trạng thái thực thi hiện tại:** `[x]` — đã hoàn tất P1-03a, P1-03b và
P1-03c. Dependency: P1-02 đã có persistence biên bản thật; P1-06a đã có profile
E2E ổn định. Không thay đổi thuật toán ASR, DSP, VAD hoặc speaker identification.

#### P1-03a — Revision/CAS và immutable approved

- [x] Manual edit dùng optimistic locking `base_revision`.
- [x] Approved revision immutable; edit sau approve tạo DRAFT revision mới.

#### P1-03b — Stale LLM callback

- [x] LLM result cũ không ghi đè manual revision mới hơn; callback stale được
  ghi nhận idempotent và trả trạng thái có thể xử lý lại.

#### P1-03c — Purge và object-storage retry

- [x] Purge dùng tombstone/idempotency retry và cascade runtime, transcript,
  minutes, exports, events/idempotency record.
- [x] MinIO upload/DB failure cleanup và MinIO delete failure có durable retry.

**Điều kiện đạt:** test manual-edit-versus-LLM, partial transaction failure và
delete retry không để metadata/object mồ côi.

### Nhật ký thực thi — P1-03 — 2026-08-12

- Revision/CAS: `save_minutes` từ chối `base_revision` cũ; revision APPROVED
  không bị ghi đè và chỉnh sửa sau duyệt chỉ tạo DRAFT.
- Stale LLM: evidence lưu `base_minutes_revision`/`generation_id`; callback
  kiểm tra analysis, transcript snapshot và trả `{"status":"stale"}` nếu bản
  sửa tay hoặc bản APPROVED đã xuất hiện. Worker coi stale là kết quả cuối,
  không retry vô hạn.
- Purge: thêm tombstone Meeting Service và migration `0009_purge_tombstones`;
  object storage lỗi được giữ pending, retry lần sau, còn metadata nội bộ được
  xóa idempotent. Runtime repository dọn retry records liên quan.
- Kiểm thử: `pytest -q tests/test_meeting_service_skeleton.py
  tests/test_minutes_lifecycle.py tests/test_minutes_worker.py
  tests/test_minutes_exports.py tests/test_contracts.py` đạt **49 pass**;
  test DB commit lỗi sau upload xác nhận object được xóa; full `pytest -q` đạt
  **162 pass**;
  SQLAlchemy SQLite smoke cho APPROVED → DRAFT đạt. `git diff --check` đạt.
- Kiểm thử bổ sung bằng Docker: `docker compose up -d --build` khởi chạy
  PostgreSQL/Redis/MinIO/Meeting Service healthy; migration head là
  `0009_purge_tombstones`. API smoke trong container đã chạy đủ runtime →
  transcript → DRAFT → stop/review/approve → DOCX qua MinIO → purge, kết quả
  `DOCKER_API_SMOKE_OK`; không xóa volume.
- Giới hạn: chưa chạy lại LiveKit E2E với AI/Agent sau thay đổi persistence.
  API/persistence Docker đã pass; LiveKit full-platform E2E vẫn cần bổ sung
  trước acceptance production.
- Đối chiếu merge plan: hoàn tất đúng lifecycle Meeting Service, không tạo FK,
  query chéo hoặc ghi sang document/task/conclusion/QLVB; không thay thuật toán
  ASR/DSP/VAD/speaker-ID.
- Bước tiếp theo: P1-04 — hoàn tất tách Meeting AI Core; sau đó P1-05 Compose
  full platform và chuyển callback bridge sang internal DNS.

## P1 — Refactor AI Core và container hóa

### P1-04 — Hoàn tất cấu trúc Meeting AI

**Trạng thái thực thi hiện tại:** `[x]` — đã hoàn tất cấu trúc Meeting AI và
đã xác nhận compatibility wrapper, streaming regression và E2E full platform.
`ai_server.py`/`agent.py` vẫn được giữ làm wrapper; không thay ASR/DSP/VAD/
speaker-ID trong task này.

- [x] Di chuyển FastAPI/API/WebSocket khỏi `ai_server.py` vào
  `meeting_ai/main.py`, `meeting_ai/api/` và application services.
- [x] Hoàn tất SessionManager, TranscriptCoordinator, callback infrastructure
  và Qdrant store boundary.
- [x] Di chuyển worker vào `meeting_ai/agent/`; `agent.py` chỉ là wrapper.
- [x] Không thay ASR/DSP/speaker threshold trong commit refactor.

**Điều kiện đạt:** wrapper cũ/mới pass compatibility + streaming regression;
WER/CER giữ baseline.

### Nhật ký thực thi — P1-04 — 2026-08-12

- Đã tách `ai_server.py` thành compatibility wrapper và entrypoint
  `meeting_ai/main.py`; `agent.py` tương tự gọi `meeting_ai.agent.worker`.
  `scripts/run_demo.sh` dùng module entrypoint mới. FastAPI factory nằm ở
  `meeting_ai/api/app.py`; SessionManager tách ở `meeting_ai/application/`;
  profile Qdrant được cô lập trong `meeting_ai/infrastructure/speaker_store.py`.
  Không chỉnh thuật toán ASR/DSP/VAD/speaker-ID hoặc ngưỡng nhận dạng.
- Regression harness nay tạo/dừng AI runtime assignment tạm trước/sau LiveKit
  probe, đúng control-plane thay vì bật static-room fallback. Một lượt đạt:
  2 final transcript, coverage 100%, WER/CER dưới ngưỡng. Một lượt kế tiếp
  tạo final trùng ở global turn đầu (3 transcript), WER/CER vượt ngưỡng.
- Kiểm thử đạt: targeted SessionManager/Agent/contract **22 pass**; full WSL
  `pytest -q` **165 pass, 6 subtests**; `git diff --check` đạt. Không còn
  native demo process sau regression cleanup.
- Blocker để đóng P1-04: cần ổn định hoặc tái lập được duplicate final trong
  streaming-VAD-finalization mà không thay baseline tuning trong commit
  refactor. Vì gate WER/CER chưa ổn định, giữ P1-04 là `[-]`.
- Khắc phục 2026-08-12: arbitration giữ candidate khi global turn còn source
  active (tối đa 6s), rồi settle tối thiểu 3s sau endpoint để chờ WavLM của
  mic rõ hơn. Điều này ngăn mic yếu endpoint sớm được publish trước candidate
  rõ, không thay DSP/ASR/VAD threshold/speaker-ID threshold. `pytest -q
  tests/test_audio_pipeline.py` đạt **23 pass**; LiveKit dual-mic regression
  đạt **2 final**, coverage 100%, WER/CER gates pass; full `pytest -q` WSL đạt
  **165 pass, 6 subtests**. Cần thêm lượt lặp/stress trước khi coi lỗi đã ổn
  định hoàn toàn và đóng P1-04.
- Refactor adaptive 2026-08-12: thay settle cứng bằng theo dõi lifecycle
  finalization từng mic. Coordinator ghi nhận EWMA latency WavLM/finalization,
  chỉ chờ khi global turn còn source active hoặc còn candidate pending, rồi
  settle ngắn theo latency quan sát được; 6s chỉ còn safety cap khi mic treo.
  Không thay decoder, DSP, VAD threshold hay speaker-ID threshold. Audio unit
  đạt **23 pass**; dual-mic LiveKit regression đạt **2 final**, coverage 100%
  và toàn bộ WER/CER gate; full `pytest -q` WSL đạt **165 pass, 6 subtests**.
- E2E full platform 2026-08-12: chạy đúng `scripts/run_e2e_streaming.sh`
  (P1-06a) sau khi dừng stack cũ nhưng giữ volumes. Lượt đầu fail do AI Core
  cũ giữ lock local Qdrant nên Agent forward 0 frame; đã dừng đúng process
  group cũ, không xóa data/runtime. Lượt chạy lại exit **0**: runner hoàn tất
  runtime → LiveKit fixture → AI callback transcript → Qwen minutes → stop;
  container và process do runner tạo đã cleanup, Docker không còn container
  Meeting Service chạy. Không ghi secret vào source.
- E2E repeat 2026-08-12: chạy `--keep` để kiểm tra persisted result rồi down
  không `-v`. `E2E_OK`: 1 transcript final, minutes revision 1 DRAFT. Timeline
  nhận đúng `Thay_Dung` qua voice_profile; raw ASR được phonetic recovery thành
  `mục 5.2`, `Hadoop Storage`, `HDFS`. Pipeline metadata: ASR final 1282ms,
  speaker ID 956ms; minutes revision được lưu khoảng 38s sau event final. Mốc
  media `ended_at` và server `created_at` chưa cùng clock, nên chưa dùng để
  báo end-to-end delay tuyệt đối; cần chuẩn hóa observability latency ở P1-06.

### Nhật ký bắt đầu subtask P1-04 — 2026-08-12

- Chọn phần còn thiếu: tách `TranscriptCoordinator` khỏi WebSocket handler,
  giữ callback publisher của Agent và toàn bộ thuật toán ASR nguyên trạng.
- Gate subtask: unit test coordinator, full pytest, streaming regression và
  E2E full platform; chưa đánh dấu P1-04 hoàn thành trước khi đủ bằng chứng.

- Đóng P1-04 sau khi hoàn tất subtask: `TranscriptCoordinator` đã tách policy
  partial/final khỏi WebSocket handler, có throttle partial theo monotonic
  clock, reset theo turn và chống publish final trùng khóa global turn. Callback
  publisher vẫn nằm trong Agent package; coordinator không truy cập DB, Qdrant
  hay xử lý audio.
- Bằng chứng kiểm thử 2026-08-12: targeted coordinator/SessionManager/Agent/
  contract **24 pass, 6 subtests**; full WSL `pytest -q` **167 pass, 5 warnings,
  6 subtests**; `compileall` và `git diff --check` đạt. Streaming regression
  đạt `DUAL_MIC_PROBE_OK`, **2/2 final**, coverage 100%, không có
  `unassigned_tail`, overlap đầy đủ và WER/CER gate pass. E2E full platform
  chạy `scripts/run_e2e_streaming.sh --audio audio/thayDung_noi.wav` đạt
  `E2E_OK`, 1 final transcript, minutes revision 1 và cleanup thành công.
- Kiểm tra compatibility wrapper sau bản sửa cuối: import `ai_server.app`,
  `meeting_ai.main.app` và `agent.main` đạt `COMPATIBILITY_IMPORT_OK`. Lần
  streaming regression và E2E cuối đều chạy sau thay đổi reset throttle ở
  `begin_turn()`; không còn container Meeting Service do runner tạo.
- Giới hạn còn lại: chất lượng ASR nói nhanh và chuẩn hóa clock/latency là
  phạm vi P0-07/P1-06, không mở rộng trong P1-04. Thay đổi hiện tại chưa
  commit; cần review diff trước checkpoint commit.
- Đối chiếu merge plan: hoàn tất đúng P1-04; bước ưu tiên tiếp theo là P1-05
  Compose full platform, chuyển AI/Agent khỏi process WSL và dùng internal DNS.

### P1-05 — Compose Meeting Platform đầy đủ

### Nhật ký bắt đầu P1-05 — 2026-08-12

- Chọn task: container hóa `meeting-ai-api` và `livekit-agent`, kết nối với
  Meeting Service bằng internal DNS. Runtime/model/Qdrant tiếp tục mount từ
  thư mục ngoài source; không thay thuật toán ASR/DSP/VAD/speaker-ID.
- Gate: build Compose, health/readiness, restart giữ Qdrant profile, streaming
  regression và E2E full platform không còn native AI/Agent process.

- [x] Thêm Dockerfile/requirements lock cho AI Core và LiveKit Agent.
- [x] Compose có AI, Agent, Meeting Service, Redis, MinIO, PostgreSQL với
  health dependencies và internal DNS/network.
- [x] Mount model/cache/Qdrant runtime ngoài source; restart không mất profile.
- [x] Không cần chạy AI/Agent thủ công bằng process WSL.

**Điều kiện đạt:** container restart đạt transcript và Qdrant persistence.

### Nhật ký thực thi — P1-05 — 2026-08-12

- Thêm `deploy/Dockerfile.meeting-ai`, `deploy/Dockerfile.livekit-agent`, lock
  dependency riêng và `deploy/compose.meeting-platform.yml`. `meeting-ai-api`
  là một worker duy nhất, mount `${MEETING_RUNTIME_HOST_PATH}` vào `/runtime`;
  Agent không mount model/Qdrant. Ollama dùng `${MEETING_RUNTIME_HOST_PATH}/ollama`.
  Không model/cache/key nào được copy vào source hoặc image.
- Chuyển đường gọi nội bộ sang `meeting-ai-api:8001`, `meeting-service:8002`
  và `ollama:11434`. `scripts/run_e2e_streaming.sh` nay chỉ chạy Compose,
  không gọi `run_demo.sh`, `ai_server.py` hay `agent.py` native. File native
  override cũ được ghi rõ là legacy P1-04.
- Bổ sung `python-multipart` vào AI lock sau khi container startup phát hiện
  endpoint enrollment cần dependency này. `/health/ready` báo thêm số profile
  Qdrant để kiểm tra persistence, không thay logic nhận dạng.
- Kiểm thử: Compose config và `bash -n` runner đạt; full WSL `pytest -q`
  **167 pass, 5 warnings, 6 subtests**. Build thật image AI/Agent đạt. E2E
  containerized với `audio/thayDung_noi.wav` đạt `E2E_OK`: 1 transcript final,
  minutes revision 1. Internal DNS Agent → AI và AI → Meeting Service đều
  trả HTTP 200.
- Restart gate: AI readiness trước/sau restart có `speaker_profiles: 15 → 15`;
  Zipformer/WavLM/VAD/Qdrant nạp lại từ `/runtime`. Agent ghi nhận restart
  control-plane và polling phục hồi; một số lỗi kết nối ngắn trong thời gian
  AI chưa ready là retry expected.
- Giới hạn: Ollama local đã unload model để tránh tranh RAM, nhưng daemon system
  chưa dừng hẳn vì WSL hiện yêu cầu mật khẩu sudo. Ollama container là runtime
  được dùng trong E2E. P1-05 chưa commit; cần review diff và kiểm tra cleanup
  trước checkpoint.
- Đối chiếu merge plan: P1-05 hoàn tất container hóa Meeting AI/Agent, giữ
  ranh giới microservice và không xâm lấn eCabinet. Bước tiếp theo: P1-06
  observability latency/graceful SIGTERM, sau đó P1-07 UI acceptance.

### P1-06 — Config, readiness và operation

- [x] **P1-06a — Profile E2E cục bộ đồng bộ:** một nguồn env runtime, script
  kiểm tra key/network/health, compose override cho WSL Agent ↔ Docker Meeting
  Service, probe fixture audio và cleanup có kiểm soát. Không ghi secret vào
  source hoặc tạo bản `.env` thứ hai.
- [x] Tách `.env.meeting`/`.env.ai`, inventory tuning runtime.
- [x] Startup fail khi key placeholder/yếu hoặc LiveKit secret thiếu.
- [x] Readiness Meeting Service kiểm tra DB/Redis/MinIO/AI; AI kiểm tra
  model/VAD/Qdrant/Ollama theo mode.
- [x] Qwen warm-up không block transcript; SIGTERM flush final turn/callback.
- [x] Ghi cold-start, peak CPU/RAM, degraded mode và safe shutdown result.

**Điều kiện đạt:** health/readiness/degraded/container restart pass.

### Nhật ký thực thi — P1-06a — 2026-08-12

- Thêm `scripts/run_e2e_streaming.sh`, `tests/livekit_e2e_probe.py` và
  `meeting_service/docker-compose.e2e.yml`. Runner đọc duy nhất private runtime
  env, ép `MEETING_SERVICE_KEY=INTERNAL_API_KEY`, kiểm tra health và thực hiện
  lifecycle audio fixture → transcript final → Qwen minutes → stop runtime.
- Hướng callback được cấu hình tường minh cho giai đoạn AI/Agent còn chạy WSL:
  container gọi AI qua `host.docker.internal:8001`; Agent gọi Meeting Service
  qua `127.0.0.1:8002`. Khi P1-05 container hóa AI/Agent, override này phải
  đổi sang internal DNS duy nhất `meeting-service:8002`.
- Kiểm thử: `bash -n scripts/run_e2e_streaming.sh`, Python compile, Compose
  config với giá trị giả đều đạt. Chạy thật runner với `audio/thayDung_noi.wav`
  đạt `E2E_OK`: 1 final transcript và minutes revision 1; `pytest -q` trong
  WSL đạt **158 pass**. Sau cleanup, không còn container hoặc native demo
  process; volume không bị xóa.
- Đối chiếu plan: đúng phần config/operation P1-06, không đổi thuật toán
  ASR/DSP/VAD/speaker-ID hay xâm lấn eCabinet. P1-03 vẫn là bước nghiệp vụ kế
  tiếp; P1-05 sẽ thay native/WSL bridge bằng Compose full platform.

### Nhật ký thực thi — P1-06 — 2026-08-12

- Tách template private config `deploy/meeting-service.env.example` và
  `deploy/meeting-ai.env.example`; Compose nhận riêng `MEETING_SERVICE_ENV_FILE`
  và `MEETING_AI_ENV_FILE`. Runtime/model/cache vẫn nằm ngoài source. Template
  chỉ inventory cấu hình operation, không chứa key thật hoặc tuning ASR.
- `MEETING_STRICT_CONFIG=true` trong Compose buộc secret nội bộ 24+ ký tự,
  runtime-token 32+ ký tự và LiveKit secret đầy đủ. Thử nghiệm runtime với env
  cũ đã fail đúng tại `MEETING_RUNTIME_TOKEN_SECRET`; không sửa `.env`, dùng
  token ngẫu nhiên chỉ trong shell E2E để xác nhận startup pass.
- `/health/ready` của Meeting Service kiểm tra PostgreSQL, Redis, MinIO và AI;
  AI trả model/VAD/Qdrant, trạng thái Ollama và telemetry cold-start/CPU/RSS.
  Ollama warm-up chạy background nên transcript path không chờ model minutes;
  lỗi warm-up chuyển readiness AI sang `degraded`, không che giấu ASR ready.
- SIGTERM AI đợi callback minutes trong timeout, đóng scheduler/final-turn và
  Qdrant tường minh. Gate runtime ghi `safe shutdown elapsed_ms=1–2`,
  `pending_callbacks=0`; warning destructor Qdrant đã được loại bỏ.
- Kiểm thử: compile + full WSL `pytest -q` **174 pass, 7 warnings, 6 subtests**;
  Compose config strict đạt khi cấp token runtime hợp lệ. Runtime readiness đạt
  DB/Redis/MinIO/AI `ok`, Ollama `ready`; telemetry thực ghi cold start khoảng
  51–81 s, warm-up 0.25–17.5 s và peak RSS khoảng 674–675 MB. E2E containerized
  `audio/thayDung_noi.wav` đạt `E2E_OK`, 1 transcript final, minutes revision 1;
  runner cleanup không xóa volume.
- Đối chiếu merge plan: P1-06 hoàn tất đúng config/readiness/operation,
  không thay ASR/DSP/VAD/speaker-ID và không xâm lấn eCabinet. Bước tiếp theo
  là P1-07 UI acceptance; P1-08 chỉ bắt đầu sau khi P1-07 đạt.

## P1 — Frontend, public deployment và acceptance

### P1-07 — Hoàn tất MeetingRoom thực tế

- [~] (2026-08-14) Đã hoàn thiện reconnect/rehydrate, playback gain có giới hạn
  và test hooks cho browser acceptance; còn thiếu E2E enrollment/role trên backend thật.
- [ ] Test Socket reconnect + REST rehydrate, hai tab cùng meeting,
  transcript update/retraction/minutes update.
- [x] Regression render minutes với transcript có `speaker` object
  (`label`/`identity_method`); không được unmount MeetingRoom sau analyze hoặc reload.
- [x] LiveKit fallback speaker giữ UUID kỹ thuật nhưng truyền `display_name` từ
  eCabinet vào token, để segment mới không hiển thị UUID thiết bị khi chưa enroll.
- [x] Frontend dev proxy Socket.IO dùng internal DNS `meeting-service:8002` và
  TranscriptPanel có REST rehydrate 3 giây như safety net khi Socket.IO bị hụt event.
- [x] Bổ sung playback gain có giới hạn; mặc định off và cảnh báo tai nghe.
- [ ] Chạy enrollment browser E2E: record, preview, upload, status, delete.
- [ ] Kiểm thử chair/member/observer qua backend thật, không chỉ ẩn nút.

**Điều kiện đạt:** reload không mất transcript/minutes; role flows pass.

### P1-07b — Điều khiển vòng đời runtime từ MeetingRoom

**Trạng thái:** `[-]` — bổ sung sau khi kiểm thử thực tế phát hiện người dùng chỉ
có thể rời trang, không có thao tác kết thúc runtime rõ ràng. Phạm vi giới hạn
trong session façade, MeetingRoom và contract lifecycle đã có; không tự động
dừng khi người dùng đóng tab.

- [ ] BFF trả capability `can_control` cùng runtime status để UI không suy diễn
  quyền chủ trì từ quyền micro.
- [ ] Chair có nút **Kết thúc họp**: xác nhận, gọi `runtime/stop`, tắt audio
  client sau khi stop thành công và hiển thị trạng thái COMPLETED/FAILED.
- [ ] Rời trang/đóng tab chỉ leave Socket.IO và LiveKit client; không gọi stop.
- [ ] Xác nhận `runtime/stop` clear AI assignment để một phiên mới có thể start;
  chạy unit/contract và Docker smoke không xóa volume.

**Điều kiện đạt:** member/observer không thấy hoặc gọi được stop; chair stop
idempotent, Agent rời room và AI assignment chuyển IDLE/COMPLETED; runtime mới
có thể khởi động sau đó.

**Nhật ký thực thi — 2026-08-14:** `[~]`

- BFF `runtime/status` nay trả capability `permissions.can_control` từ domain
  session eCabinet; Meeting Service vẫn không nhận role hoặc query eCabinet.
- MeetingRoom dùng capability này để chỉ render nút **Kết thúc họp** cho chủ
  trì/quyền control. Stop thành công mới đóng local mic/LiveKit; cleanup khi
  rời trang vẫn chỉ leave Socket.IO/LiveKit client, không gọi stop.
- Kiểm thử: compile BFF route trong container đạt; frontend production build
  đạt; full Python suite **174 passed**.
- Còn thiếu gate: chưa bấm stop trên runtime LiveKit thật đang được người dùng
  kiểm thử, vì thao tác đó sẽ chủ động kết thúc room hiện tại. Cần xác nhận
  chair/member/observer và start runtime kế tiếp sau stop trong E2E riêng.

### P1-07c — Hậu kỳ MeetingRoom và điều hướng sau khi kết thúc

**Trạng thái:** `[-]` — phát sinh từ kiểm thử UI: tab Kết luận của phiên họp
chưa hiển thị structured minutes, MeetingRoom chưa có roster người tham gia,
và sau khi kết thúc chưa có đường quay lại module Meetings.

- [x] Tab **Kết luận cuộc họp** của MeetingDetail tải và hiển thị minutes thuộc
  đúng meeting, không ghi vào module tài liệu/kết luận nghiệp vụ khác.
- [x] MeetingRoom hiển thị danh sách đại biểu từ eCabinet và trạng thái đang
  kết nối LiveKit ở mức best-effort.
- [~] Sau stop thành công có nút quay lại module Meetings và liên kết mở tab
  Kết luận của phiên vừa kết thúc; không tự động mất bản minutes.
- [~] Build frontend, kiểm tra route/contract BFF và browser smoke cho cả room
  đang hoạt động lẫn room đã COMPLETED.

**Điều kiện đạt:** minutes hiển thị đúng trong tab Kết luận; roster không làm
gián đoạn realtime; chair có đường về `/meetings`, member/observer không thấy
control stop.

**Nhật ký thực thi — 2026-08-14:** `[~]`

- Đã bổ sung MeetingDetail tải minutes/transcript theo tab `?tab=conclusions`
  và render read-only structured minutes; bản placeholder revision 0 không bị
  coi là biên bản đã tạo.
- Đã bổ sung roster đại biểu từ eCabinet trong MeetingRoom, ghép trạng thái
  LiveKit đang kết nối theo identity ở mức best-effort.
- Sau stop, MeetingRoom có hai đường rõ ràng: về `/meetings` hoặc mở lại
  `/meetings/{id}?tab=conclusions`.
- Kiểm thử: frontend production build pass; Playwright headless Edge xác nhận
  tab Kết luận render không lỗi console và MeetingRoom render roster `3 người`.
- Giới hạn: chưa bấm stop trên runtime đang dùng để xác nhận điều hướng thật,
  vì thao tác này sẽ kết thúc phiên test hiện tại; browser room probe còn gặp
  HTTP 409 LiveKit token do runtime/session đang được kiểm thử trước đó.

### Cập nhật P1-07 — 2026-08-14

- Đã sửa MeetingRoom: khi Socket.IO reconnect sẽ gọi REST rehydrate lại
  transcript, minutes và trạng thái phân tích; không chỉ tải lại transcript.
- Đã thêm playback gain qua Web Audio, giới hạn `1.0x–2.0x`, mặc định playback
  vẫn tắt và giữ cảnh báo nên dùng tai nghe. Đã thêm test hooks cho flow
  enrollment record/preview/upload/delete.
- Kiểm thử: frontend production build trong container tạm **pass**; toàn bộ
  pytest suite **174 passed** (gồm contract và Meeting Service).
- Browser smoke bằng headless Edge với frontend container tạm: `/login` và
  `MeetingRoom` render pass; playback mặc định off, cảnh báo tai nghe và slider
  gain `1.0x–2.0x` pass. Backend giả lập không được dùng để kết luận role/audio.
- Regression 2026-08-14: `MinutesEditor` đã chuẩn hóa speaker label trước khi
  render evidence. Trước đó API trả object `{label, identity_method}` nhưng UI
  render trực tiếp object, React throw và màn hình trắng sau `minutes/analyze`
  hoặc reload. Headless Edge đã reload MeetingRoom có transcript/minutes revision
  1 và nhấn lại `Tạo biên bản từ transcript`: trang giữ nguyên, console 0 error;
  frontend production build trong container Linux pass.
- Regression 2026-08-14: BFF LiveKit token đã truyền `current_user.full_name`
  (fallback username) sang Meeting Service; token service ký claim `name` bằng
  `display_name`, thay vì external UUID. Unit test xác nhận claim, full suite
  **174 passed**; Meeting Service được recreate không xóa volume và readiness
  đạt. Segment đã persist trước bản vá vẫn giữ nhãn cũ; chỉ segment mới nhận
  token mới sẽ hiện tên participant nếu chưa được voice profile nhận diện.
- Regression 2026-08-14: phát hiện Vite trong frontend container gọi
  `host.docker.internal:8002`; WebSocket upgrade bị `ECONNREFUSED` vì Meeting
  Service chỉ bind loopback WSL. Frontend dev đã join network
  `meeting_platform_internal` và proxy qua DNS nội bộ. Bổ sung rehydrate
  transcript mỗi 3 giây để giữ UI nhất quán nếu một realtime event bị hụt.
  Playwright xác nhận transcript tăng từ 5 lên 6 không reload sau callback,
  build production pass; hai segment probe sau đó đã được retract khỏi UI.
- Giới hạn: chưa chạy browser E2E với backend thật cho hai tab/reconnect,
  enrollment upload và chair/member/observer; P1-07 chưa đạt điều kiện hoàn tất.
- Đối chiếu merge plan: thay đổi chỉ ở MeetingRoom/enrollment UI, không thay
  thuật toán baseline hoặc contract eCabinet; bước tiếp theo là dựng runtime
  local và chạy browser/role acceptance trước khi mở P1-08.

### P1-08 — Deploy LAN và bàn giao runtime

- [x] Bổ sung Compose LAN LiveKit, tách URL WSS browser và URL Docker Agent.
- [ ] Cấp chứng chỉ CA nội bộ cho các laptop; deploy frontend eCabinet qua HTTPS LAN.
- [ ] Mở tối thiểu `7881/TCP`, `7882/UDP` và HTTPS UI trên firewall host; không public AI, Ollama, PostgreSQL, Redis, MinIO hay internal REST.
- [ ] Kiểm tra HTTPS UI, `/api`, Socket.IO, LiveKit WSS/UDP từ ba laptop cùng LAN.

**Điều kiện đạt:** LAN E2E ba laptop pass, không cần DDNS/Tailscale/port-forward.

**Nhật ký — 2026-08-14:** thêm `deploy/compose.meeting-platform.lan.yml`,
`LIVEKIT_CONFIG` cho LiveKit self-host, Caddy TLS nội bộ cho WSS và env example
không chứa secret. Meeting Service truyền `MEETING_LIVEKIT_AGENT_URL` cho Agent
trong Docker, còn token browser vẫn trả `MEETING_LIVEKIT_URL` LAN. Compose
render pass; contract/LiveKit/EventPublisher test đạt 20 pass. Chưa deploy thật
vì cần IP LAN cố định, env private và CA được cài trên laptop tham gia.

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

**Kiểm tra lại trước P0-07:** các test `test_runtime_token`,
`test_meeting_service_skeleton` và `test_contracts` chạy bằng runtime WSL,
**43 pass**. Xác nhận request thiếu/sai `X-Service-Key` bị từ chối, JWT
unsigned/object claims bị từ chối và Socket.IO kiểm tra meeting/runtime/
permission claims. Không phát hiện gap mới ở P0-02.

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
