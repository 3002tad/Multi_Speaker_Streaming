# Kế hoạch tích hợp Meeting Platform theo kiến trúc microservice vào eCabinet

## 1. Mục tiêu và trạng thái kế hoạch

Kế hoạch này thay thế phương án MVP 2–3 ngày trước đây. Thời gian mục tiêu mới là **5–6 ngày làm việc**, cho phép refactor sâu theo ranh giới microservice nhưng không thay đổi thuật toán AI đã được kiểm thử.

Kết quả cần đạt:

```text
LiveKit audio
→ Meeting AI Core
→ transcript partial/final + speaker identity
→ Meeting Service PostgreSQL + Socket.IO
→ biên bản có evidence/revision
→ chỉnh sửa, duyệt draft và export DOCX qua MinIO
```

### 1.1 Phạm vi MVP

- Một phiên AI hoạt động tại một thời điểm.
- Nhiều người dùng eCabinet tham gia cùng một phiên bằng laptop/microphone riêng.
- Transcript realtime, global-turn/crosstalk, speaker enrollment/identification, adaptive dictionary, hotword và phonetic recovery giữ nguyên từ baseline.
- Qwen2.5:3b chỉ tạo biên bản bất đồng bộ, không refine transcript realtime.
- Meeting Workspace và Board Display dùng design system/layout của eCabinet.
- Transcript final và biên bản được lưu trong database do Meeting Service sở hữu; eCabinet truy cập qua API, không đọc bảng chéo service.
- Voice profile nằm trong Qdrant embedded do Meeting AI sở hữu.
- Biên bản và metadata export thuộc vòng đời của module phiên họp; file DOCX dùng MinIO làm object storage nhưng không tạo bản ghi nghiệp vụ trong module document.
- Phạm vi ghi dữ liệu của AI dừng tại phiên họp: transcript, biên bản, revision và export. `decisions/actions` chỉ là nội dung gợi ý trong biên bản, không đẩy sang Văn bản chỉ đạo, task, conclusion, voting, document hoặc QLVB.
- Chạy bằng container trong WSL2; Nginx, frontend tĩnh và LiveKit tiếp tục nằm trên home server.

MVP là **phase 1 của quá trình tách bounded context Meeting theo strangler pattern**: nhánh realtime, transcript và biên bản mới đi vào Meeting Service độc lập ngay từ đầu; lịch họp, attendee, role và authentication hiện hữu tạm ở eCabinet Core. Không di chuyển các bảng legacy này trong sprint 5–6 ngày vì sẽ mở rộng blast radius sang nhiều module. Contract external ID đã được thiết kế để có thể tách tiếp phần lịch/participant ở phase sau mà không nhập lại dữ liệu realtime.

### 1.2 Ngoài phạm vi 5–6 ngày

- Nhiều phiên AI chạy đồng thời.
- Scale ngang nhiều AI worker hoặc Qdrant Server.
- High availability/failover production.
- Redis Streams/message broker và durable outbox hoàn chỉnh.
- Tối ưu hoặc thay model ASR, enhancer, WavLM hay LLM.
- Feature AI voting suggestion mới; baseline hiện chưa có logic này.
- Load test dài và security hardening ở mức production.
- Mọi thay đổi schema/contract/UI hoặc ghi dữ liệu AI vào các module Văn bản chỉ đạo, document, task, conclusion, voting hoặc QLVB hiện có.
- Chức năng promote/tạo bản nháp nghiệp vụ từ `decisions/actions`; chỉ xem và chỉnh sửa chúng trong biên bản ở MVP.

### 1.3 Thứ tự ưu tiên bàn giao

1. **P0 — Baseline họp realtime trong module phiên họp:** start/stop theo meeting, join LiveKit, publish/mute mic, global-turn, DPDFNet/Zipformer, speaker identity, partial/final transcript, PostgreSQL persistence, Socket.IO room, reconnect và safe shutdown. Không bắt đầu tối ưu feature ngoài phiên họp khi vertical slice này chưa pass E2E.
2. **P1 — Biên bản nằm trong phiên họp:** Qwen compose từ transcript final, revision/evidence, review/approve, xem transcript nguồn và export DOCX theo quyền. P1 không được làm chậm hoặc thay đổi thuật toán realtime P0.
3. **Không có P2 cross-module trong sprint:** decision/action dừng ở nội dung biên bản; không ghi sang Văn bản chỉ đạo hoặc các module nghiệp vụ khác.

---

## 2. Nguyên tắc bắt buộc

1. **eCabinet là hệ thống core.** eCabinet tiếp tục sở hữu lịch phiên họp, thành viên, vai trò và authentication; không chuyển các bảng hiện hữu trong sprint.
2. **Meeting Service là microservice nghiệp vụ realtime.** Service này sở hữu runtime session, transcript, biên bản, export metadata, Socket.IO và database riêng.
3. **Meeting AI Core là compute microservice độc lập.** AI không truy cập PostgreSQL/MinIO của eCabinet hoặc Meeting Service; chỉ nhận lệnh/evidence và trả event/result qua API contract.
4. **Không thay thuật toán trong lúc di chuyển code.** Refactor cấu trúc phải giữ nguyên output và thông số baseline.
5. **Refactor tăng dần.** Dùng compatibility wrapper; không copy code thành hai nguồn sự thật và không xóa entrypoint cũ trước khi E2E pass.
6. **Audio không đi qua eCabinet hoặc Meeting Service.** Browser và LiveKit AI Agent kết nối trực tiếp LiveKit.
7. **Final trước, realtime sau.** Transcript final phải commit database Meeting Service thành công trước khi service broadcast final.
8. **Event có idempotency và revision.** Callback retry không được tạo dữ liệu trùng hoặc ghi đè revision mới.
9. **Structured minutes là contract chính.** Không flatten document làm mất topic, speaker hoặc `source_segment_ids`.
10. **Không dùng database chung về mặt ownership.** Có thể dùng cùng PostgreSQL instance ở demo, nhưng eCabinet và Meeting Service dùng database/schema, credential và migration riêng; không có FK hoặc query trực tiếp xuyên service.
11. **Tích hợp additive.** Giữ contract các module eCabinet hiện có; chỉ thêm façade/client, permission helper và UI entry cần thiết.
12. **Không public AI API/Ollama.** Client chỉ truy cập API gateway, Socket.IO gateway path và LiveKit; port nội bộ không public.
13. **Meeting-first containment.** AI chỉ được ghi thông qua Meeting Service vào aggregate realtime/biên bản. Không import CRUD/service của Văn bản chỉ đạo, task, conclusion, voting, document hoặc QLVB để tạo/cập nhật dữ liệu nghiệp vụ.

### 2.1 Integration hardening bắt buộc

Bốn hạng mục dưới đây thuộc phạm vi merge, không được đẩy sang backlog vì trực tiếp bảo vệ dữ liệu và vòng đời phiên họp:

1. **Permission tập trung cho phiên họp.** Tạo và dùng thống nhất `can_view_meeting`, `can_manage_meeting`, `can_control_ai`, `can_edit_minutes`, `can_approve_minutes`. Áp dụng cho endpoint AI mới và các endpoint phiên họp hiện có được luồng mới sử dụng: get/patch meeting, attendees, token, transcript, minutes, export và download. Người không thuộc phiên phải nhận `403`; member/observer không được điều khiển AI hoặc sửa biên bản.
2. **Hoàn thiện delete lifecycle trong phạm vi Meeting Service.** Cascade nội bộ xử lý runtime sessions, transcript segments, minutes revisions, minutes exports, idempotency/retry records và object MinIO. `delete_session` của eCabinet chỉ phát lệnh purge idempotent; không sửa schema/business logic của task/conclusion. Lỗi `MeetingTask` tồn tại từ trước được ghi backlog riêng trừ khi nó trực tiếp chặn E2E tích hợp.
3. **State guard tối thiểu.** AI chỉ được start khi phiên `APPROVED` hoặc `ONGOING`; không start khi `COMPLETED/CANCELLED`. Biên bản chỉ được approve sau khi phiên kết thúc; revision `APPROVED` immutable, sửa tiếp phải tạo draft revision mới.
4. **Transaction và regression test.** Bắt buộc kiểm thử permission matrix, delete cascade, callback/idempotency, DB/MinIO partial failure và state transition. Không merge nếu một trong các gate này fail.

---

## 3. Kiến trúc đích

```mermaid
graph TB
    subgraph Client
        WEB["eCabinet React"]
        BOARD["Board Display"]
    end

    subgraph HOME["Home server Xubuntu"]
        NGX["Nginx + TLS + static frontend"]
        LK["LiveKit self-hosted"]
    end

    subgraph WSL["Ubuntu WSL2 - 12 CPU / 10 GB RAM"]
        subgraph EC["eCabinet Core"]
            API["eCabinet API / BFF"]
            ECPG["PostgreSQL - ecabinet DB"]
        end

        subgraph MS["Meeting Platform Compose"]
            MEETING["Meeting Service"]
            MSPG["PostgreSQL - meeting_service DB"]
            REDIS["Redis - runtime lock/retry"]
            MINIO["MinIO - meeting-minutes bucket"]
            AIAPI["Meeting AI Core"]
            AGENT["LiveKit AI Agent"]
            OLLAMA["Ollama - Qwen2.5:3b"]
            QDRANT["Qdrant embedded volume"]
        end
    end

    WEB -->|HTTPS REST / Socket.IO| NGX
    BOARD -->|HTTPS REST / Socket.IO| NGX
    NGX -->|/api| API
    NGX -->|/meeting-runtime/socket.io| MEETING
    WEB -->|WSS/WebRTC audio| LK
    LK -->|audio tracks| AGENT
    AGENT -->|WS PCM 16 kHz| AIAPI
    API -->|signed meeting snapshot / BFF REST| MEETING
    MEETING -->|control + evidence| AIAPI
    AIAPI -->|events/results| MEETING
    API --> ECPG
    MEETING --> MSPG
    MEETING --> REDIS
    MEETING --> MINIO
    AIAPI --> OLLAMA
    AIAPI --> QDRANT
```

### 3.1 Quyền sở hữu dữ liệu

| Dữ liệu | Owner | Ghi chú |
|---|---|---|
| Meeting, attendee, role | eCabinet Core/PostgreSQL `ecabinet` | Meeting Service chỉ nhận snapshot đã xác thực, không query DB eCabinet |
| Runtime session | Meeting Service/PostgreSQL `meeting_service` | Khóa bằng `meeting_id` external và `runtime_session_id` |
| Transcript final | Meeting Service/PostgreSQL `meeting_service` | Nguồn chính thức của nhánh realtime |
| Partial transcript | Meeting Service runtime/Socket.IO | Không bắt buộc lưu |
| Minutes revisions | Meeting Service/PostgreSQL `meeting_service` | Lưu structured document và trạng thái `DRAFT/REVIEWING/APPROVED` |
| Decisions/actions do AI tổng hợp | `meeting_minutes_revisions.document_json` | Chỉ là nội dung biên bản có evidence; không tạo task/conclusion/Văn bản chỉ đạo |
| Task, conclusion, Văn bản chỉ đạo, voting, document, QLVB | Module hiện có của eCabinet | Không thay schema/contract và không nhận dữ liệu AI trong MVP |
| DOCX biên bản | Meeting Service + MinIO bucket riêng | Metadata gắn `meeting_id/minutes_revision`; không tạo `Document/File` nghiệp vụ trong module tài liệu |
| Voice profiles | Meeting AI/Qdrant embedded | Khóa bằng `ecabinet_user_id` |
| Model/cache | Meeting AI runtime volume | Không đóng model vào image |
| Event idempotency/retry | Meeting Service DB/Redis | Meeting AI chỉ giữ bounded spool khi chưa gửi được event |

### 3.2 Network

- Tạo internal network có tên cố định `meeting_platform_internal`; eCabinet API, Meeting Service và Meeting AI Core cùng join bằng Compose external network.
- eCabinet gọi Meeting Service bằng DNS `http://meeting-service:8002`; Meeting Service gọi AI bằng `http://meeting-ai-api:8001`.
- Meeting AI gửi callback tới `http://meeting-service:8002/internal/v1/ai-events`, không gọi ngược eCabinet API.
- Port `8001`, Ollama, PostgreSQL, Redis và MinIO không public. Port Meeting Service `8002` chỉ được eCabinet nội bộ gọi REST và Nginx home server truy cập qua Tailscale cho đúng Socket.IO path.
- Nginx public `/api` tới eCabinet BFF và `/meeting-runtime/socket.io` tới Meeting Service; media dùng domain LiveKit riêng.
- Có thể dùng cùng PostgreSQL/MinIO instance trong demo để tiết kiệm RAM, nhưng phải tách database/bucket/credential và tuyệt đối không tạo FK xuyên service.

---

## 4. Chiến lược refactor Meeting Platform

### 4.1 Cấu trúc source đích

```text
meeting_ai/
├── __init__.py
├── main.py                         # FastAPI app factory/lifespan
├── config.py                       # Settings + validation
├── api/
│   ├── routes.py                   # Internal control/enrollment API
│   └── websocket.py                # PCM stream từ Agent
├── application/
│   ├── session_manager.py          # Một active session, lifecycle/state
│   ├── transcript_coordinator.py   # Global-turn/cross-mic arbitration
│   ├── minutes_service.py          # Nhận evidence snapshot, gọi composer
│   └── enrollment_service.py
├── core/
│   ├── audio_pipeline.py
│   ├── final_turn.py
│   ├── speaker_identity.py
│   ├── text_refinement.py
│   ├── adaptive_dictionary.py
│   ├── topic_discovery.py
│   ├── minutes_composer.py
│   └── evaluation.py
├── infrastructure/
│   ├── callback.py                 # Event sink tới Meeting Service + bounded spool
│   ├── qdrant_store.py
│   ├── ollama_client.py
│   └── runtime_store.py            # assignment/session state tối thiểu
└── agent/
    ├── main.py
    └── assignment_client.py        # poll active assignment, join/leave room

deploy/
├── Dockerfile.meeting-service
├── Dockerfile.meeting-ai
├── Dockerfile.livekit-agent
├── requirements.meeting-service.lock.txt
├── requirements.meeting-ai.lock.txt
├── requirements.livekit-agent.lock.txt
├── compose.meeting-platform.yml
├── compose.ecabinet-demo.yml       # override: tắt reload, thêm env/network
├── .env.meeting.example
├── .env.ai.example
└── nginx/meet.simplething.id.vn

ai_server.py                        # compatibility wrapper
agent.py                            # compatibility wrapper
backend/                            # compatibility imports cho test/script cũ
```

Microservice nghiệp vụ realtime được tách riêng, không đặt trong `ecabinet/backend/app/src`:

```text
meeting_service/
├── app/
│   ├── main.py                     # FastAPI + Socket.IO lifespan
│   ├── config.py
│   ├── api/
│   │   ├── socketio.py             # runtime token + meeting rooms
│   │   └── internal.py             # eCabinet BFF và AI callback
│   ├── application/
│   │   ├── runtime_service.py
│   │   ├── transcript_service.py
│   │   ├── minutes_service.py
│   │   └── export_service.py
│   ├── domain/
│   │   ├── models.py
│   │   ├── permissions.py
│   │   └── events.py
│   └── infrastructure/
│       ├── database.py
│       ├── repositories.py
│       ├── ai_client.py
│       ├── minio_store.py
│       └── token_verifier.py
├── alembic/
├── tests/
└── requirements.lock.txt
```

### 4.2 Quy tắc di chuyển code

- Dùng `git mv` khi module trở thành nguồn chính; không copy rồi duy trì hai bản.
- File cũ chỉ re-export/import từ `meeting_ai` để script regression cũ tiếp tục chạy.
- `ai_server.py` chỉ còn gọi app/entrypoint mới sau khi extraction pass.
- `agent.py` chỉ còn gọi `meeting_ai.agent.main` sau khi agent mới join được phòng.
- Mỗi nhóm di chuyển phải chạy unit test và streaming regression trước khi chuyển nhóm tiếp theo.
- `backend/api/main.py` demo cũ được giữ làm compatibility/rollback trong 6 ngày, nhưng không còn là data plane chính của eCabinet.

### 4.3 Ranh giới core và adapter

Meeting AI Core không import FastAPI domain của eCabinet, SQLAlchemy, MinIO hoặc Socket.IO. Các interface tối thiểu:

```text
ASREngine             decode_stream / finalize_turn
SpeakerProfileStore   enroll / identify / status
EventSink             emit(event)
MinutesGenerator      compose(evidence, previous_document)
SessionRuntimeStore   activate / stop / assignment / health
```

Thuật toán DPDFNet/DSP, Zipformer, WavLM, hotword và phonetic recovery được giữ nguyên; chỉ dependency injection và lifecycle được thay đổi.

---

## 5. Contract định danh và lifecycle

### 5.1 ID chuẩn

- `meeting_id`: UUID `meeting_sessions.id` của eCabinet, được Meeting Service lưu như external ID; không có FK hoặc query chéo database.
- `runtime_session_id`: UUID do Meeting Service tạo cho một lần start/stop; dùng làm correlation ID xuyên Meeting Service, AI Core và Agent.
- `livekit_room`: `meeting-{meeting_id}` hoặc tên deterministic tương đương.
- `segment_id`: ID ổn định của một utterance, giữ nguyên qua revision.
- `event_id`: UUID duy nhất cho từng event/callback attempt logical.
- `source_identity`: LiveKit participant identity theo user và device.
- `speaker_user_id`: external eCabinet user UUID, chỉ có khi voice profile match chắc chắn; không có FK trong Meeting Service DB.

Identity LiveKit đề xuất:

```text
user:{ecabinet_user_id}:device:{device_id}
```

Không sử dụng display name làm khóa kỹ thuật.

### 5.2 Một active session

Trong MVP, Meeting Service sở hữu Redis/transaction lock `meeting-runtime:active-session`:

- Start khi đã có session STARTING/READY/RECORDING khác: trả `409`.
- Start lặp lại cùng meeting và idempotency key: trả trạng thái hiện tại.
- Lock có owner `runtime_session_id` và được release khi COMPLETED/FAILED.
- Restart phải reconcile trạng thái trong Meeting Service với readiness của AI/Agent; eCabinet chỉ đọc status qua façade API.

### 5.3 Agent assignment

LiveKit Agent là container độc lập và không đọc một `MEETING_ROOM` cố định mãi mãi.

```text
eCabinet xác thực start + tạo signed meeting snapshot
→ POST Meeting Service runtime {meeting_id, participants, roles, actor claims}
→ Meeting Service tạo runtime_session_id, LiveKit browser/agent token
→ Meeting Service POST AI Core assignment {runtime_session_id, meeting_id, room, agent_token, participants}
→ AI Core lưu assignment generation N
→ Agent poll GET /internal/v1/agent/assignment
→ disconnect assignment N-1 nếu có
→ join room của generation N
→ POST /internal/v1/agent/status READY
→ AI Core báo Meeting Service runtime READY
```

Stop:

```text
eCabinet xác thực stop và gọi Meeting Service
→ Meeting Service runtime STOPPING
→ Meeting Service yêu cầu AI Core stop
→ Agent dừng nhận track, chờ finalization có timeout
→ Agent disconnect LiveKit
→ AI flush event/result tới Meeting Service
→ Meeting Service commit final transcript/minutes job và chuyển COMPLETED
```

LiveKit API secret chỉ nằm tại Meeting Service. Browser/Agent chỉ nhận token ngắn hạn; eCabinet Core và AI Core không cần giữ secret.

---

## 6. API contracts

### 6.1 API public façade của eCabinet

Frontend tiếp tục gọi endpoint cùng origin của eCabinet. Core xác thực session hiện có, kiểm tra membership/role, tạo signed actor claims rồi proxy request ít tần suất sang Meeting Service; eCabinet không persist transcript/minutes.

| Method | Endpoint | Quyền/ý nghĩa |
|---|---|---|
| POST | `/api/v1/meetings/{id}/ai/start` | Chủ trì/thư ký/admin; tạo runtime qua Meeting Service |
| POST | `/api/v1/meetings/{id}/ai/stop` | Chủ trì/thư ký/admin; graceful stop |
| GET | `/api/v1/meetings/{id}/ai/status` | Thành viên đọc trạng thái runtime |
| POST | `/api/v1/meetings/{id}/livekit-token` | Lấy LiveKit token và short-lived Socket.IO runtime token |
| GET | `/api/v1/meetings/{id}/ai/transcripts` | BFF hydrate transcript từ Meeting Service |
| GET | `/api/v1/meetings/{id}/ai/minutes` | BFF lấy revision biên bản mới nhất |
| PATCH | `/api/v1/meetings/{id}/ai/minutes` | Chỉnh sửa với `base_revision` |
| POST | `/api/v1/meetings/{id}/ai/analyze` | Yêu cầu Meeting Service tạo/regenerate minutes |
| POST | `/api/v1/meetings/{id}/ai/minutes/review` | Chuyển draft sang chờ rà soát |
| POST | `/api/v1/meetings/{id}/ai/minutes/approve` | Người có quyền duyệt chốt revision chính thức |
| POST | `/api/v1/meetings/{id}/ai/minutes/exports/docx` | Tạo DOCX draft/chính thức |
| GET | `/api/v1/meetings/{id}/ai/minutes/exports/{export_id}/download` | BFF stream file theo quyền phiên họp |
| POST | `/api/v1/users/me/voice-enrollment` | Proxy enroll voice của user hiện tại |
| GET | `/api/v1/users/me/voice-enrollment/status` | Trạng thái profile |
| DELETE | `/api/v1/users/me/voice-enrollment` | Xóa profile của chính user |

Endpoint start không phải endpoint join. Mỗi participant lấy token riêng sau khi eCabinet kiểm tra membership. Socket.IO kết nối trực tiếp Meeting Service qua Nginx nhưng chỉ chấp nhận runtime token ngắn hạn do eCabinet ký.

### 6.2 API nội bộ eCabinet gọi Meeting Service

Chỉ mở trong internal network, dùng service key và signed actor/meeting claims. Meeting Service không gọi ngược database hoặc endpoint CRUD nội bộ của eCabinet để tự suy quyền.

| Method | Endpoint | Ý nghĩa |
|---|---|---|
| POST | `/internal/v1/meetings/{meeting_id}/runtime` | Tạo runtime từ meeting/participant snapshot |
| POST | `/internal/v1/runtimes/{runtime_session_id}/stop` | Flush và stop |
| GET | `/internal/v1/meetings/{meeting_id}/status` | Readiness/status |
| PUT | `/internal/v1/meetings/{meeting_id}/snapshot` | Cập nhật participant names/roles/hotwords có version |
| POST | `/internal/v1/meetings/{meeting_id}/tokens` | Meeting Service cấp LiveKit token theo actor claims; eCabinet tự ký Socket.IO runtime token |
| GET | `/internal/v1/meetings/{meeting_id}/transcripts` | Hydrate transcript |
| GET/PATCH | `/internal/v1/meetings/{meeting_id}/minutes` | Đọc/chỉnh revision biên bản |
| POST | `/internal/v1/meetings/{meeting_id}/minutes/{action}` | `analyze`, `review`, `approve`, `export` |
| DELETE | `/internal/v1/meetings/{meeting_id}` | Purge idempotent dữ liệu Meeting Service khi eCabinet xóa phiên |
| POST/GET/DELETE | `/internal/v1/enrollments/{user_id}` | Proxy voice profile qua AI Core |
| GET | `/health/live` | Meeting Service liveness |
| GET | `/health/ready` | DB/Redis/MinIO/AI dependency readiness |

Snapshot tối thiểu:

```json
{
  "schema_version": 1,
  "meeting_id": "uuid",
  "snapshot_revision": 3,
  "meeting": {"title": "...", "status": "APPROVED", "started_at": "..."},
  "actor": {"user_id": "uuid", "role": "CHAIRPERSON", "permissions": ["CONTROL", "EDIT_MINUTES"]},
  "participants": [{"user_id": "uuid", "display_name": "Nguyễn Văn A", "role": "MEMBER"}]
}
```

### 6.3 API nội bộ Meeting Service gọi Meeting AI Core

| Method | Endpoint | Ý nghĩa |
|---|---|---|
| POST | `/internal/v1/sessions` | Tạo AI assignment từ runtime snapshot |
| POST | `/internal/v1/sessions/{runtime_session_id}/stop` | Finalize và stop |
| GET | `/internal/v1/sessions/{runtime_session_id}` | AI readiness/status |
| POST | `/internal/v1/sessions/{runtime_session_id}/participants` | Cập nhật participant/hotword snapshot |
| POST | `/internal/v1/sessions/{runtime_session_id}/analyze` | Compose minutes từ evidence do Meeting Service cung cấp |
| POST/GET/DELETE | `/internal/v1/enrollments/{user_id}` | Voice enrollment/profile |
| GET | `/internal/v1/agent/assignment` | Agent lấy assignment generation hiện tại |
| POST | `/internal/v1/agent/status` | Agent báo CONNECTING/READY/FAILED/STOPPED |
| GET | `/health/live` | AI liveness |
| GET | `/health/ready` | Model/Qdrant readiness |

AI không đọc database. Analyze request luôn mang evidence snapshot do Meeting Service lấy từ database của chính nó.

### 6.4 Callback Meeting AI Core gọi Meeting Service

```text
POST http://meeting-service:8002/internal/v1/ai-events
Header: X-Internal-Api-Key
```

Envelope dùng `runtime_session_id` làm correlation ID:

```json
{
  "schema_version": 1,
  "event_id": "uuid",
  "type": "transcript.final",
  "meeting_id": "uuid",
  "runtime_session_id": "uuid",
  "occurred_at": "ISO-8601 UTC",
  "payload": {}
}
```

Event MVP: `session.status`, `speaker.active`, `transcript.partial`, `transcript.final`, `transcript.retracted`, `minutes.updated`, `pipeline.warning`.

Meeting Service trả `{"status":"accepted|duplicate|stale"}`. AI đưa lỗi gửi event vào bounded persistent spool, retry exponential backoff và không chặn audio loop.

---

## 7. Database và migration Meeting Service

Meeting Service dùng database, credential và chuỗi migration riêng. `meeting_id` và `speaker_user_id` chỉ là external UUID tham chiếu logic tới eCabinet; tuyệt đối không tạo FK hoặc query trực tiếp sang database eCabinet.

### 7.1 `meeting_runtime_sessions`

Đây là runtime record cho một lần start/stop; chưa cam kết lưu raw recording file trong MVP.

```text
id UUID PK                         # runtime_session_id
meeting_id UUID NOT NULL           # external eCabinet meeting ID, indexed, no FK
meeting_snapshot_json JSONB NOT NULL
livekit_room VARCHAR UNIQUE NOT NULL
status VARCHAR NOT NULL
started_at TIMESTAMPTZ
ended_at TIMESTAMPTZ
error_code VARCHAR NULL
error_message TEXT NULL
created_at/updated_at
```

Status:

```text
STARTING | READY | RECORDING | STOPPING | COMPLETED | FAILED
```

### 7.2 `meeting_transcripts`

```text
id UUID PK
meeting_id UUID NOT NULL                 # external ID, no FK
runtime_session_id UUID FK meeting_runtime_sessions ON DELETE CASCADE
event_id UUID NOT NULL
segment_id VARCHAR NOT NULL
source_identity VARCHAR NOT NULL
speaker_user_id UUID NULL                # external eCabinet user ID, no FK
speaker_label VARCHAR NOT NULL
identity_method VARCHAR NOT NULL
raw_text TEXT NOT NULL
content_text TEXT NOT NULL
started_at TIMESTAMPTZ NOT NULL
ended_at TIMESTAMPTZ NOT NULL
confidence FLOAT NULL
revision INTEGER NOT NULL DEFAULT 1
status VARCHAR NOT NULL DEFAULT FINAL
pipeline_meta JSONB
created_at/updated_at
```

Constraints:

- Unique `(runtime_session_id, segment_id)`.
- Unique `event_id` cho event final mới nhất đã chấp nhận.
- Incoming revision nhỏ hơn hoặc bằng revision hiện tại: trả `stale/duplicate`, không update.
- `RETRACTED` không bị trả trong danh sách mặc định nhưng vẫn được giữ để audit.
- Adapter đổi Unix timestamp từ baseline thành UTC datetime trước khi lưu.

### 7.3 `meeting_minutes_revisions`

Mỗi edit/regenerate tạo một immutable revision:

```text
id UUID PK
meeting_id UUID NOT NULL                  # external ID, no FK
runtime_session_id UUID FK meeting_runtime_sessions ON DELETE SET NULL
revision INTEGER NOT NULL
base_transcript_revision INTEGER NOT NULL
document_json JSONB NOT NULL
metadata_json JSONB
status VARCHAR NOT NULL
generated_at TIMESTAMPTZ
submitted_at TIMESTAMPTZ NULL
approved_at TIMESTAMPTZ NULL
approved_by UUID NULL                     # external eCabinet user ID, no FK
created_at/updated_at
UNIQUE(meeting_id, revision)
```

`document_json` phải giữ schema baseline:

```json
{
  "schema_version": 1,
  "meeting": {"title": "...", "started_at": "..."},
  "summary": [
    {"content": "...", "source_segment_ids": ["seg-1"]}
  ],
  "topics": [
    {
      "title": "...",
      "details": [],
      "proposals": [],
      "decisions": [],
      "actions": [],
      "source_segment_ids": []
    }
  ],
  "source_segment_ids": []
}
```

Mọi item draft được Meeting Service gắn `item_id` ổn định khi persist để UI chọn/duyệt.

### 7.4 Vòng đời biên bản và quyền tải

Biên bản là aggregate do Meeting Service sở hữu và là thành phần logic trong vòng đời phiên họp eCabinet; nó không phải tài liệu đầu vào của module document:

```text
meeting session CREATED/SCHEDULED
→ session IN_PROGRESS: tạo/cập nhật minutes DRAFT từ transcript
→ session ENDED: khóa audio input và tạo revision tổng hợp
→ minutes REVIEWING: thư ký/chủ trì rà soát
→ minutes APPROVED: revision chính thức, không sửa trực tiếp
→ session ARCHIVED: biên bản đi cùng hồ sơ phiên họp
```

Quy tắc revision:

- `PATCH` luôn tạo revision mới; không ghi đè revision cũ.
- Revision `APPROVED` là immutable.
- Chỉnh sửa sau khi đã duyệt tạo revision `DRAFT` mới. Bản đã duyệt trước đó vẫn là bản chính thức cho tới khi revision mới được duyệt; khi đó bản cũ chuyển `SUPERSEDED` để giữ audit.
- Khi eCabinet xóa phiên họp theo đúng nghiệp vụ hiện có, core gọi purge idempotent sang Meeting Service. Meeting Service tự xử lý transcript, minutes revision, export metadata và object storage theo transaction/retry nội bộ.

Quyền export/download:

| Trạng thái minutes | Chủ trì/thư ký/admin | Thành viên phiên họp |
|---|---|---|
| `DRAFT` | Được export/tải bản có watermark `DỰ THẢO` | Không được tải |
| `REVIEWING` | Được export/tải bản có watermark `DỰ THẢO` | Chỉ xem nếu permission phiên họp cho phép; không tải mặc định |
| `APPROVED` | Được tải bản chính thức | Được tải nếu có quyền xem phiên họp |
| `ARCHIVED` | Được tải bản chính thức | Theo ACL của phiên họp |

Metadata export được lưu trong database Meeting Service, ví dụ `meeting_minutes_exports`:

```text
id UUID PK
meeting_id UUID NOT NULL                  # external ID, no FK
minutes_revision_id UUID FK meeting_minutes_revisions ON DELETE CASCADE
minutes_revision INTEGER NOT NULL
minutes_status VARCHAR NOT NULL
format VARCHAR NOT NULL DEFAULT 'docx'
storage_key VARCHAR NOT NULL
filename VARCHAR NOT NULL
content_type VARCHAR NOT NULL
size_bytes BIGINT
checksum VARCHAR
created_by UUID NULL                     # external eCabinet user ID, no FK
created_at TIMESTAMPTZ
UNIQUE(minutes_revision_id, format)
```

### 7.5 Ranh giới với các module nghiệp vụ khác

- `decisions`, `actions`, `owner_hint`, `due_date_hint` và `field_hint` chỉ nằm trong `meeting_minutes_revisions.document_json` để người dùng rà soát trong ngữ cảnh biên bản.
- Không thêm cột AI vào `meeting_tasks`, `meeting_conclusions`, Văn bản chỉ đạo, voting, document hoặc QLVB.
- Không có endpoint promote. Meeting Service và Meeting AI Core không import hoặc gọi CRUD/service của các module trên.
- Biên bản `APPROVED` xác nhận nội dung hồ sơ phiên họp nhưng không tự biến decision/action thành lệnh điều hành chính thức.
- Nếu nghiệp vụ cần, người dùng tiếp tục nhập thủ công bằng workflow hiện hữu. Handoff có kiểm soát hoặc form prefill chỉ được xem xét ở phase sau với plan và test riêng.

### 7.6 Alembic và delete lifecycle

- Meeting Service có Alembic riêng trong `meeting_service/alembic`; không thêm model hoặc migration bảng AI vào eCabinet.
- Review migration thủ công, không chỉ tin autogenerate.
- FK cascade chỉ tồn tại giữa các bảng do Meeting Service sở hữu.
- `DELETE /internal/v1/meetings/{meeting_id}` là idempotent. eCabinet gọi endpoint này khi xóa phiên; nếu timeout/lỗi thì ghi tombstone/outbox tích hợp có bounded retry thay vì tạo transaction phân tán.
- Cleanup MinIO là retryable: DB có thể đánh dấu pending cleanup rồi worker xóa object; không giữ transaction DB mở trong lúc gọi MinIO.
- Demo được phép dùng cùng PostgreSQL instance, nhưng phải tách database/schema, user và migration history; production có thể tách instance mà không đổi contract.
- Backup cả database eCabinet và Meeting Service trước migration demo; mỗi service có downgrade độc lập.

---

## 8. Speaker enrollment và Qdrant

- Enrollment lấy user từ session eCabinet; frontend không gửi tên làm khóa.
- eCabinet ký actor claims và proxy request tới Meeting Service; Meeting Service chuyển `ecabinet_user_id`, display name và audio 20–30 giây tới AI.
- AI giữ quality gate, VAD, multiple embeddings, outlier removal và profile prototypes hiện có.
- Qdrant payload mới chứa:

```json
{
  "ecabinet_user_id": "uuid",
  "speaker_label": "Tên hiển thị",
  "profile_version": 3,
  "prototype_index": 0,
  "prototype_kind": "centroid"
}
```

- Query/open-set decision group theo `ecabinet_user_id`, không group theo label.
- Không tự động xóa collection cũ. Tạo collection version mới hoặc backup trước migration; profile cũ được giữ cho rollback.
- Chỉ một Meeting AI API process mở Qdrant embedded.
- Người chưa enroll hoặc match không đạt confidence/margin phải fallback về participant nguồn mic.

---

## 9. Socket.IO và realtime state

Meeting Service tự mount Socket.IO tại path riêng; không sửa hoặc tái sử dụng Socket.IO `/ws` hiện có của eCabinet:

```javascript
io(window.location.origin, {
  path: '/meeting-runtime/socket.io',
  transports: ['websocket'],
  auth: { token: runtimeSocketToken },
});
```

`runtimeSocketToken` là token ngắn hạn do eCabinet ký sau khi kiểm tra session, membership và role. Claims tối thiểu gồm `sub`, `meeting_id`, `runtime_session_id`, permissions, `iat`, `exp`, `jti`; không đưa session key dài hạn của eCabinet sang Meeting Service.

Meeting Service cần:

- `connect`: verify chữ ký, issuer/audience/expiry của runtime token và lưu actor claims trong Socket.IO session.
- `join_meeting_room`: chỉ cho join đúng `meeting_id/runtime_session_id` trong token; không query database eCabinet.
- `leave_meeting_room`: `await sio.leave_room(...)`.
- Room duy nhất: `meeting:{meeting_id}`.
- CORS lấy từ `ALLOWED_ORIGINS`; demo public không để `*` cùng credentials.
- Revoke theo runtime state: token còn hạn nhưng runtime đã `COMPLETED/FAILED` không được join lại.

Client lifecycle:

1. REST hydrate transcript/minutes.
2. Kết nối Socket.IO và join room.
3. Merge partial theo `segment_id`.
4. Final thay partial cùng segment.
5. Bỏ event revision cũ.
6. `transcript.retracted` loại segment khỏi UI nhưng giữ DB audit.
7. Reconnect thì hydrate lại REST trước khi nhận event tiếp.

REST hydrate vẫn đi qua eCabinet BFF; event realtime đi thẳng Nginx → Meeting Service. Không dùng Redis Pub/Sub làm nguồn dữ liệu chính trong MVP. Redis do Meeting Service sở hữu, dùng active-session lock và tùy chọn Socket.IO manager; PostgreSQL Meeting Service vẫn là nguồn chính thức của final state.

---

## 10. Frontend eCabinet

### 10.1 Cấu trúc

```text
ecabinet/frontend/src/features/meeting-ai/
├── api.js
├── socket.js
├── hooks/
│   ├── useMeetingAI.js
│   ├── useLiveKitAudio.js
│   └── useMeetingEvents.js
├── components/
│   ├── AudioControls.jsx
│   ├── ParticipantPanel.jsx
│   ├── TranscriptPanel.jsx
│   ├── MinutesEditor.jsx
│   ├── PipelineStatus.jsx
│   └── VoiceEnrollmentDialog.jsx
└── utils/
    └── eventReducer.js

src/pages/MeetingLive.jsx
src/pages/MeetingBoard.jsx
src/layouts/BoardLayout.jsx
```

Không copy nguyên frontend baseline. Tất cả component dùng theme, typography, button, spacing và responsive conventions của eCabinet.

### 10.2 Routes

- `/meetings/:id/live`: protected route trong `MainLayout` hoặc focus layout phù hợp.
- `/meetings/:id/board`: protected top-level route với `BoardLayout`, không nằm trong sidebar layout.
- `MeetingDetail.jsx` chỉ thêm entry button và khu vực truy cập biên bản sau họp; tránh nhúng state realtime vào file hiện có.

### 10.3 LiveKit audio

`useLiveKitAudio` phải quản lý đầy đủ:

- Xin microphone permission.
- Gọi eCabinet lấy token theo user/device.
- Connect/disconnect LiveKit an toàn.
- Publish local audio track.
- Mic mute/unmute; không có nút Pause session trong MVP.
- Playback remote mặc định tắt.
- Khi bật playback, cảnh báo dùng tai nghe.
- Dùng WebAudio gain/normalization có giới hạn để ổn định âm lượng playback.
- Reconnect và token refresh khi cần.
- Unpublish/stop track khi rời phòng hoặc component unmount.

### 10.4 Minutes editor và ranh giới nghiệp vụ

- Hiển thị structured minutes theo topic/timeline và source segment.
- Click source mở transcript tương ứng.
- Edit gửi `base_revision`; server trả `409` nếu đã có revision mới.
- Decision/action hiển thị trong biên bản cùng `item_id` và evidence nhưng phải có nhãn “Nội dung AI tổng hợp”, không mang trạng thái giao việc/chỉ đạo.
- Không hiển thị nút promote/tạo task/kết luận/Văn bản chỉ đạo trong MVP.
- UI hiển thị rõ `DRAFT`, `REVIEWING` hoặc `APPROVED` và revision đang xem.
- Chỉ chủ trì/thư ký/admin thấy nút export draft; file draft luôn có watermark `DỰ THẢO`, revision và thời điểm xuất.
- Thành viên chỉ tải được revision `APPROVED` nếu có quyền xem phiên họp.
- Export/download gọi API minutes của phiên họp, không gọi API document hiện có.

### 10.5 Voice enrollment

Dialog tối thiểu:

- Hiển thị đoạn văn mẫu 20–30 giây.
- Record, preview và gửi audio.
- Hiển thị lỗi duration, silence, clipping hoặc profile consistency.
- Cho phép ghi lại/xóa profile.
- Enrollment không chặn người dùng join meeting.

---

## 11. DOCX thuộc vòng đời phiên họp

Tạo exporter trong Meeting Service. Có thể tái sử dụng package thuần cho DOCX hoặc contract object-storage chung, nhưng không import business service, route, ACL, ORM hay bản ghi `Document/File` của module tài liệu eCabinet:

```text
structured minutes
→ render BytesIO bằng python-docx/template
→ thêm watermark DỰ THẢO hoặc dấu hiệu bản CHÍNH THỨC
→ lưu object MinIO theo meeting-minutes/{meeting_id}/{revision}.docx
→ ghi meeting_minutes_exports trong transaction Meeting Service
→ download qua eCabinet BFF sau khi kiểm tra permission phiên họp
```

Yêu cầu:

- Endpoint tạo file dùng POST, không dùng GET.
- Mỗi minutes revision tạo file riêng hoặc trả export đã tồn tại theo idempotency key.
- Draft chỉ chủ trì/thư ký/admin được export và tải; tên file, header/footer hoặc watermark phải thể hiện `DỰ THẢO`.
- Bản chính thức chỉ được tạo từ revision `APPROVED`; thành viên tải theo permission của phiên họp.
- Tên file đề xuất: `Bien_ban_{session_code}_DU_THAO_v{revision}.docx` và `Bien_ban_{session_code}_CHINH_THUC_v{revision}.docx`.
- Không sửa contract và dữ liệu của module document hiện tại.
- Nếu upload MinIO thành công nhưng transaction DB thất bại, xóa object vừa tạo.
- Template DOCX là asset có version, không hard-code toàn bộ format trong route.
- Export không được làm thay đổi minutes document.
- Sau khi duyệt, nội dung revision chính thức immutable; mọi chỉnh sửa tiếp theo tạo draft revision mới và phải duyệt lại.

---

## 12. Container và runtime

### 12.1 Services

| Service | Image/process | Public |
|---|---|---|
| `api` | eCabinet FastAPI/BFF | REST qua Nginx |
| `postgres` | PostgreSQL; database `ecabinet` và `meeting_service` tách biệt trong demo | Không |
| `meeting-service` | Realtime lifecycle, transcript, minutes, export, Socket.IO | Chỉ Socket.IO qua Nginx; REST qua eCabinet BFF |
| `redis` | Lock/runtime cache do Meeting Service sở hữu | Không |
| `minio` | Bucket/credential riêng của Meeting Service | Chỉ download có kiểm soát qua BFF |
| `meeting-ai-api` | Meeting AI FastAPI/model runtime | Không |
| `livekit-agent` | LiveKit subscription worker | Không |
| `ollama` | Qwen2.5:3b | Không |

Trong demo có thể tái sử dụng một PostgreSQL/MinIO process để tiết kiệm RAM, nhưng không tái sử dụng database/schema/user/bucket. Compose phải cho phép tách chúng sang host riêng mà không thay đổi code nghiệp vụ.

### 12.2 Model và cache mounts

Không dùng named volume rỗng cho model. Dùng runtime Linux hiện có:

```yaml
volumes:
  - ${MEETING_RUNTIME_DIR}/Zipformer-30M-RNNT-Streaming-6000h:/runtime/zipformer:ro
  - ${MEETING_RUNTIME_DIR}/models:/runtime/models:ro
  - ${MEETING_RUNTIME_DIR}/huggingface:/runtime/huggingface
  - ai_qdrant_data:/runtime/data/qdrant
  - ai_retry_spool:/runtime/data/spool
  - ollama_cache:/root/.ollama
```

Không mount model từ `/mnt/d`. Source được COPY vào image để tránh runtime import chậm trên DrvFS.

### 12.3 Environment

Giữ đúng tên biến mà baseline đang đọc:

```text
# Meeting Service
MEETING_DATABASE_URL=postgresql+asyncpg://meeting_service:...@postgres:5432/meeting_service
MEETING_REDIS_URL=redis://redis:6379/0
MEETING_AI_URL=http://meeting-ai-api:8001
MEETING_MINIO_BUCKET=meeting-minutes
ECABINET_TOKEN_ISSUER=ecabinet
ECABINET_TOKEN_AUDIENCE=meeting-service
ECABINET_TOKEN_PUBLIC_KEY_FILE=/run/secrets/ecabinet_token_public_key
MEETING_AI_SERVICE_KEY_FILE=/run/secrets/meeting_ai_service_key

# Meeting AI Core
ZIPFORMER_MODEL_DIR=/runtime/zipformer
ASR_ENHANCER_MODEL=/runtime/models/dpdfnet_baseline.onnx
PHONETIC_G2P_MODEL_PATH=/runtime/models/g2p_multilingual_byT5_tiny_onnx
SPEAKER_DATABASE_PATH=/runtime/data/qdrant
OLLAMA_URL=http://ollama:11434
MINUTES_COMPOSER_MODEL=qwen2.5:3b
MINUTES_COMPOSER_MODE=llm
```

Toàn bộ tuning env hiện tại phải được inventory và chuyển sang `.env.ai`; cấu hình Meeting Service nằm trong `.env.meeting` riêng. Không dựa vào default mới. Internal key dùng secret file hoặc `${VAR:?required}` và startup phải fail nếu key thiếu/yếu/placeholder. Demo có thể dùng HMAC chung nếu cần rút ngắn thời gian, nhưng production ưu tiên eCabinet ký bất đối xứng và Meeting Service chỉ giữ public key.

### 12.4 Ollama init/warm-up

- Pin image tag/digest đã kiểm thử; không dùng `latest` trong cấu hình demo ổn định.
- Có init step kiểm tra/pull `qwen2.5:3b` vào volume.
- Warm-up trước khi đánh dấu AI ready nếu minutes mode là `llm`.
- Không expose port 11434 ra Internet.

### 12.5 Health và shutdown

- Liveness không tải model lại và không phụ thuộc downstream.
- Readiness Meeting Service kiểm tra database, Redis, MinIO và khả năng gọi AI; dependency lỗi được trả chi tiết nhưng không làm liveness fail.
- Readiness AI kiểm tra Zipformer, WavLM, Qdrant, G2P cần thiết và Ollama theo mode.
- `stop_grace_period` tối thiểu 30 giây cho AI/Agent.
- SIGTERM Meeting Service ngừng nhận start mới, chờ request/event đang xử lý và flush job metadata. SIGTERM AI dừng nhận audio mới, finalize turn, flush callback có timeout rồi đóng LiveKit/Qdrant/http client.
- Demo override tắt Uvicorn `--reload`; hot reload chỉ dùng ở profile dev.

---

## 13. HTTP status và permission

### 13.1 Error code

| Code | Trường hợp |
|---|---|
| 401 | Thiếu/hết hạn eCabinet session hoặc runtime token |
| 403 | Không thuộc meeting hoặc không có quyền điều khiển/edit/export |
| 404 | Meeting, runtime session, minutes hoặc voice profile không tồn tại |
| 409 | Có active session khác, revision conflict, start/stop sai trạng thái |
| 422 | Enrollment/audio/document payload không đạt validation |
| 502 | Downstream trả response không hợp lệ |
| 503 | AI, LiveKit, Ollama hoặc MinIO không sẵn sàng |
| 504 | Downstream timeout |

Service key/chữ ký actor claims sai trả 403; event duplicate/stale hợp lệ vẫn trả 200 kèm trạng thái để AI ngừng retry.

### 13.2 Permission MVP

- Permission trong eCabinet phải đi qua helper tập trung: `can_view_meeting`, `can_manage_meeting`, `can_control_ai`, `can_edit_minutes`, `can_approve_minutes`; không rải điều kiện role thủ công ở từng route.
- Sau khi kiểm tra quyền, eCabinet phát signed actor/meeting claims ngắn hạn cho request BFF hoặc Socket.IO. Meeting Service chỉ tin claims đã xác minh chữ ký, issuer, audience, expiry và không tin role/permissions do browser gửi trực tiếp.
- Với REST proxy, eCabinet truyền correlation ID và actor claims; Meeting Service vẫn áp state guard/domain authorization dựa trên claims, không chỉ dựa vào việc request đến từ internal network.
- View/join: chairperson và authenticated attendee có quyền truy cập meeting; người không thuộc meeting trả `403` ở get meeting, attendees, LiveKit token, transcript, minutes và download.
- Start/stop/analyze/edit/export draft: chairperson, secretary hoặc admin.
- Approve minutes: role được cấu hình phê duyệt; MVP dùng chairperson hoặc admin, secretary chỉ được approve nếu được cấp quyền rõ ràng.
- Member/observer không được patch meeting, thêm/xóa attendee, điều khiển AI, sửa/duyệt minutes hoặc export draft.
- Enrollment: chỉ user hiện tại.
- Board: authenticated display/user có quyền xem meeting; không có control API.
- `speaker_user_id` từ callback phải có trong participant snapshot đã ký của runtime; nếu không hợp lệ thì lưu null + fallback label. Meeting Service không query bảng user/attendee của eCabinet.

---

## 14. Kế hoạch 6 ngày

### Ngày 1 — Freeze baseline và contract

Tasks:

1. Ghi lại commit/status và không sửa thuật toán AI trong sprint.
2. Chạy baseline regression trên bộ truth hiện có, đặc biệt `truth_1` có noise; lưu WER/CER/latency/RAM.
3. Inventory dependency từ `venv_linux`, model paths, Hugging Face cache và toàn bộ env tuning đang active.
4. Chốt ranh giới ownership và OpenAPI/JSON Schema cho ba contract: eCabinet → Meeting Service, Meeting Service → AI Core và AI callback → Meeting Service.
5. Chốt signed actor/runtime token, external ID, idempotency và purge contract; cấm FK/query chéo database.
6. Tạo package `meeting_ai`, `meeting_service` và các interface/application skeleton.
7. Tạo compatibility tests để entrypoint AI cũ và mới cho cùng contract.

Gate cuối ngày:

- Baseline chạy lại được.
- Contract không còn mismatch callback path, ID hoặc minutes schema.
- Chưa di chuyển core nếu regression snapshot chưa có.

### Ngày 2 — Refactor AI Core có kiểm soát

Tasks:

1. Di chuyển config và các pure/core module bằng `git mv`.
2. Tách WebSocket audio handler khỏi orchestration.
3. Tạo SessionManager và TranscriptCoordinator.
4. Đưa cross-mic/global-turn arbitration vào trước EventSink.
5. Tách QdrantStore và refactor identity theo `ecabinet_user_id`.
6. Tách MinutesService nhận evidence snapshot.
7. Giữ `ai_server.py`, `agent.py` và import path cũ làm wrapper.
8. Chạy unit + streaming regression sau từng nhóm.

Gate cuối ngày:

- WER/CER không kém baseline quá 1 điểm phần trăm.
- Transcript event shape đúng contract.
- Nếu gate fail, giữ implementation cũ phía sau façade và dời phần extraction lỗi sang sau demo; không tiếp tục big-bang refactor.

### Ngày 3 — Meeting Service skeleton, lifecycle, Agent và container

Tasks:

1. Tạo Meeting Service FastAPI + Socket.IO với database/Alembic riêng.
2. Implement runtime lifecycle, participant snapshot, active-session lock và LiveKit token service.
3. Implement Meeting Service → AI control client và AI callback endpoint với idempotency/revision.
4. Implement assignment polling/generation và Agent join/leave phòng động.
5. Implement callback adapter phía AI + bounded retry spool.
6. Tạo Dockerfiles/Compose cho Meeting Service, AI và Agent; bind model runtime và persistent volumes.
7. Tạo Ollama init/warm-up, liveness/readiness và graceful SIGTERM.
8. Chạy regression trong container, kiểm tra CPU/RAM và model cold-start.

Gate cuối ngày:

- Meeting Service tạo runtime → Agent READY → audio vào AI → callback commit DB → Socket.IO emit thành công.
- Container restart không mất Qdrant profile.
- Meeting Service migration độc lập và không tạo bảng/cột AI trong database eCabinet.
- Không có OOM hoặc swap thrashing kéo dài trong WSL2 10 GB.

### Ngày 4 — eCabinet façade và realtime vertical slice

Tasks:

1. Tạo integration client eCabinet → Meeting Service bằng shared `httpx.AsyncClient` hiện có.
2. Tạo façade routes giữ public contract `/api/v1/meetings/{id}/ai/*`; không thêm repository/model AI vào eCabinet.
3. Tạo permission helper tập trung, signed actor claims và runtime Socket.IO token; áp dụng cho endpoint mới cùng endpoint session hiện có nằm trên luồng tích hợp.
4. Proxy start/stop/status/token/transcript/minutes/enrollment; Meeting Service sở hữu active-session lock và persistence.
5. Implement Meeting Service transcript/minutes REST, edit revision, analyze evidence snapshot và export metadata; giữ decision/action bên trong structured minutes.
6. Implement Socket.IO handshake auth, join permission, exact path và REST rehydrate.
7. Thêm state guard cho start/stop AI và review/approve minutes tại Meeting Service.
8. eCabinet `delete_session` gọi purge idempotent sang Meeting Service; thêm tombstone/outbox retry mà không sửa task/conclusion.
9. Viết transaction/regression tests cho permission, claims, idempotency, purge, state transition và DB/MinIO partial failure.
10. Thêm dependency-boundary test bảo đảm Meeting Service/AI không import CRUD/model nghiệp vụ, không query database eCabinet và không ghi vào task/conclusion/Văn bản chỉ đạo/voting/document/QLVB.

Gate cuối ngày:

- Duplicate/stale callback không làm thay đổi DB sai.
- User không thuộc meeting không join được room/LiveKit.
- Final transcript commit trước Socket.IO emit.
- Xóa phiên có transcript/minutes/export không để metadata hoặc object AI mồ côi.
- Không có query, FK hoặc transaction phân tán giữa database eCabinet và Meeting Service.
- Không có thay đổi schema/contract hay bản ghi mới trong task/conclusion/Văn bản chỉ đạo/document/voting/QLVB.
- Start/approve sai trạng thái trả `409`; thao tác sai quyền trả `403`.

### Ngày 5 — Frontend, minutes và DOCX

Tasks:

1. Tạo feature `meeting-ai` theo design system eCabinet.
2. Implement LiveKit connect, token, mic mute/unmute, playback opt-in và cleanup.
3. Implement transcript reducer/hydration/reconnect/retraction.
4. Implement MinutesEditor với evidence và optimistic revision.
5. Implement VoiceEnrollmentDialog.
6. Implement Board Display read-only bằng BoardLayout.
7. Implement vòng đời `DRAFT → REVIEWING → APPROVED`, permission export/download, DOCX template và MinIO transaction cleanup.
8. Thêm entry button vào MeetingDetail, không nhúng realtime state vào file này.

Gate cuối ngày:

- Một browser hoàn thành join → nói → transcript → minutes edit → export.
- Reload trang không mất transcript/minutes.

### Ngày 6 — E2E public, regression và bàn giao

Tasks:

1. Test 3 laptop/microphone qua `meet.simplething.id.vn`.
2. Test known/unknown speaker, di chuyển qua mic khác và crosstalk.
3. Test callback retry, revision conflict, retraction, AI/Ollama/MinIO unavailable.
4. Chạy headless Edge cho luồng login/join/transcript/minutes/export.
5. Build frontend và deploy static files lên home server.
6. Cập nhật Nginx API + Socket.IO proxy và healthcheck public.
7. Chạy lại baseline regression trong container; so sánh snapshot ngày 1.
8. Kiểm tra safe stop/restart và viết hướng dẫn vận hành/rollback.
9. Chạy lại integration hardening suite: permission matrix, delete lifecycle AI, state guard, transaction failure và dependency-boundary.
10. Dùng thời gian còn lại chỉ để sửa blocker; không thêm feature mới.

---

## 15. Test matrix và acceptance

### 15.1 AI regression

- Offline evaluation trên toàn bộ truth files.
- Streaming regression với chunk/frame đúng production.
- `truth_1` chạy cả frontend baseline đã chốt và pipeline container mới.
- WER/CER không kém baseline quá 1 điểm phần trăm.
- Không thay thresholds/config nếu chưa có benchmark riêng.

### 15.2 Backend integration

- Start idempotent và conflict active meeting.
- Permission matrix: outsider `403`; member/observer read-only; chairperson/secretary/admin đúng quyền view/join/start/stop/edit/export/approve.
- Các endpoint session hiện có được AI UI sử dụng (`get/patch`, attendees) cũng phải đi qua permission helper, không chỉ endpoint AI mới.
- Signed claims/runtime token: đúng issuer/audience/expiry; token giả mạo, hết hạn hoặc khác meeting bị từ chối.
- Callback AI → Meeting Service accepted/duplicate/stale; AI không gọi callback vào eCabinet.
- Final/revision/retraction ordering.
- Session deletion gọi purge idempotent; kiểm thử timeout, retry/tombstone và cascade nội bộ Meeting Service với transcript, minutes, export, runtime, retry/idempotency và object MinIO.
- State guard: start chỉ khi `APPROVED/ONGOING`; cấm start khi `COMPLETED/CANCELLED`; approve minutes chỉ sau khi phiên kết thúc; approved revision immutable.
- Analyze nhận evidence từ PostgreSQL Meeting Service, AI không tự truy cập bất kỳ DB nào.
- Minutes manual edit không bị event LLM cũ ghi đè.
- Transaction failure: MinIO upload thành công nhưng DB commit lỗi phải xóa object; DB delete thành công nhưng MinIO cleanup lỗi phải được ghi nhận để retry mà không rollback dữ liệu đã commit.
- Contract test xác nhận Meeting Service không query database eCabinet và eCabinet không query database Meeting Service; không có cross-service FK.
- Boundary test xác nhận eCabinet không có migration/bảng AI mới, và không có import ghi dữ liệu hoặc API promote tới task/conclusion/Văn bản chỉ đạo/document/voting/QLVB.
- Mô phỏng eCabinet tạm unavailable: runtime đang chạy tiếp tục nhận/commit transcript; request điều khiển mới bị từ chối an toàn khi claims hết hạn.

### 15.3 Realtime/frontend

- Mic permission allow/deny.
- Mute/unmute và leave cleanup.
- Playback mặc định off, cảnh báo tai nghe và gain có giới hạn.
- Socket reconnect + REST rehydrate.
- Socket.IO dùng `/meeting-runtime/socket.io`; `/ws` legacy của eCabinet không bị thay đổi.
- Hai tab cùng meeting nhận event nhất quán.
- Board chỉ đọc và không có control.
- Enrollment thành công/thất bại/xóa profile.

### 15.4 Operational

- Health/live và health/ready đúng trạng thái.
- Ollama/model warm-up.
- Container restart và Qdrant persistence.
- SIGTERM/Compose stop flush final turn trong timeout.
- Không OOM; lưu peak RAM/CPU trong báo cáo test.
- Meeting Service REST nội bộ, AI port, database, Redis, MinIO và Ollama không truy cập được từ Internet; chỉ Socket.IO path được public qua Nginx.

### 15.5 Definition of Done

MVP chỉ hoàn thành khi:

1. Frontend không gọi trực tiếp AI API.
2. Meeting AI Core không truy cập database/MinIO; Meeting Service không truy cập database eCabinet; eCabinet không truy cập database Meeting Service.
3. Agent có thể nhận assignment mới mà không restart thủ công.
4. Ba laptop gửi audio và thấy transcript cùng meeting.
5. Speaker known/unknown fallback hoạt động đúng open-set rule.
6. Transcript final được lưu PostgreSQL Meeting Service và phục hồi qua eCabinet BFF sau reload.
7. Minutes giữ structured document, evidence và revision.
8. Edit/review/approve và export DOCX hoạt động qua API phiên họp; draft có watermark, bản chính thức chỉ sinh từ revision đã duyệt và không tạo bản ghi trong module document.
9. Regression AI đạt ngưỡng.
10. Safe stop/restart và public E2E qua Nginx pass.
11. Permission helper bảo vệ cả endpoint AI và endpoint session nằm trên luồng mới; outsider/member/observer không thể nâng quyền.
12. Xóa phiên kích hoạt purge idempotent; dữ liệu Meeting Service hoàn tất không lỗi FK nội bộ và không để metadata/object mồ côi ngoài cơ chế retry đã định nghĩa.
13. State guard và transaction/regression suite pass đầy đủ.
14. Không module nghiệp vụ nào ngoài session façade và Meeting Service thay đổi schema/contract hoặc nhận dữ liệu do AI sinh ra.
15. Meeting Service build/deploy/migrate độc lập, có database ownership rõ ràng và không yêu cầu restart eCabinet khi chỉ thay đổi implementation nội bộ mà contract giữ nguyên.

---

## 16. Public deployment demo

### 16.1 Frontend

```text
npm ci
npm run build
→ copy dist/ lên /var/www/ecabinet hoặc release directory
→ đổi symlink/release atomically
```

Production frontend dùng same-origin API; không build với `http://localhost:8080`.

### 16.2 Nginx

```nginx
location /api/ {
    proxy_pass http://<WSL_TAILNET_IP>:8080/api/;
    proxy_http_version 1.1;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_set_header X-Real-IP $remote_addr;
}

location /meeting-runtime/socket.io/ {
    proxy_pass http://<WSL_TAILNET_IP>:8002/meeting-runtime/socket.io/;
    proxy_http_version 1.1;
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection "upgrade";
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_read_timeout 3600s;
}

location / {
    try_files $uri $uri/ /index.html;
}
```

- Router chỉ forward 80/443 vào home server.
- LiveKit tiếp tục ở `wss://livekit.simplething.id.vn` với media ports riêng.
- `/ws` hiện có của eCabinet giữ nguyên cho chức năng legacy; meeting realtime chỉ dùng path riêng ở trên.
- Không proxy Meeting Service REST `/internal`, port AI `8001`, database, Redis, MinIO hoặc Ollama `11434` ra Internet.

---

## 17. File changes dự kiến

### 17.1 Meeting AI mới/refactor

- `meeting_ai/**`
- `deploy/Dockerfile.meeting-ai`
- `deploy/Dockerfile.livekit-agent`
- `deploy/requirements.meeting-ai.lock.txt`
- `deploy/requirements.livekit-agent.lock.txt`
- `deploy/compose.meeting-platform.yml`
- `deploy/.env.ai.example`
- Compatibility wrapper: `ai_server.py`, `agent.py`, các import `backend/**` cần thiết.
- Tests cho core/application/API/container contract.

### 17.2 Meeting Service mới

- `meeting_service/app/main.py`, `config.py`
- `meeting_service/app/api/internal.py`, `socketio.py`
- `meeting_service/app/application/runtime_service.py`, `transcript_service.py`, `minutes_service.py`, `export_service.py`
- `meeting_service/app/domain/**`
- `meeting_service/app/infrastructure/database/**`, `redis/**`, `minio/**`, `ai_client.py`, `livekit.py`
- `meeting_service/app/templates/minutes_v1.docx`
- `meeting_service/alembic/**`
- `meeting_service/tests/**`
- `deploy/Dockerfile.meeting-service`
- `deploy/requirements.meeting-service.lock.txt`
- `deploy/.env.meeting.example`

### 17.3 eCabinet backend modify tối thiểu

- `app/core/config.py`
- `app/src/session/permissions.py`: helper permission tập trung thuộc domain phiên họp.
- `app/src/session/meeting_runtime_client.py`: adapter HTTP/BFF tới Meeting Service.
- `app/src/session/meeting_runtime_tokens.py`: signed actor/runtime claims.
- `app/src/session/routes.py`: chỉ thêm façade routes, permission và lời gọi purge idempotent khi xóa phiên.
- `requirements.txt`
- Compose demo override/network/env để eCabinet join `meeting_platform_internal`.

Không thêm AI ORM model/repository hoặc Alembic migration vào eCabinet. Nếu cần durable retry cho purge, ưu tiên bảng integration outbox dùng cơ chế chung hiện có; chỉ thêm migration tối thiểu sau khi xác nhận không có reusable outbox.

Không sửa model/schema/route của document, task, conclusion, voting, Văn bản chỉ đạo hoặc QLVB trong sprint này.

### 17.4 Frontend

- `src/features/meeting-ai/**`
- `src/pages/MeetingLive.jsx`
- `src/pages/MeetingBoard.jsx`
- `src/layouts/BoardLayout.jsx`
- Modify `src/App.jsx`, `src/api/index.js`, `src/pages/MeetingDetail.jsx`, `package.json`.

---

## 18. Rollback và kiểm soát rủi ro

- Feature flag `AI_MEETING_ENABLED` cho toàn bộ route/UI entry.
- Giữ entrypoint baseline cũ làm compatibility trong sprint.
- Không xóa Qdrant collection cũ; tạo collection version mới.
- Backup riêng database eCabinet và Meeting Service trước migration; mỗi migration có downgrade độc lập.
- Giữ frontend release trước để rollback symlink/static directory.
- Nếu refactor core làm regression ở ngày 2, façade gọi lại implementation cũ và tiếp tục tích hợp; không cố sửa thuật toán trong sprint.
- Nếu Qwen/Ollama không đạt readiness, transcript vẫn hoạt động và minutes job báo unavailable/retry; không chặn cuộc họp.
- Nếu AI mất kết nối, eCabinet core và các module khác vẫn hoạt động; UI hiển thị degraded state.

## 19. Hạng mục sau demo

1. Xóa compatibility wrappers sau một chu kỳ regression riêng.
2. Durable outbox/Redis Streams hoặc message broker.
3. Multi-room và worker scheduling.
4. Qdrant Server khi có nhiều worker.
5. Voting suggestion có grounded evidence và review flow.
6. Production secrets, rate limit, audit nâng cao và network policy.
7. Load/soak test, metrics, tracing và alerting.
8. Production LiveKit/HA và object lifecycle policy.
9. Phase 2 strangler: đánh giá chuyển lịch họp/participant/role khỏi eCabinet Core sang Meeting Service sau khi contract realtime ổn định; thực hiện bằng plan migration dữ liệu và compatibility API riêng, không gộp vào sprint MVP.
