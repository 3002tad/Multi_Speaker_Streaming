# ❓ Câu hỏi cần trả lời trước khi tạo Plan chi tiết

---

## 1. Phạm vi tích hợp (Scope)

### Q1. Vai trò của bạn trong dự án là gì?
Trong [`plan.md`](file:///D:/VNPT/Code/Multi_Speaker_Streaming/ecabinet/plan.md) dòng 122 có ghi:
> *"Lobby/admit + meeting service: NGƯỜI KHÁC làm. User chỉ làm ASR Core"*

→ Bạn chỉ phụ trách phần **AI/ASR core** hay phụ trách **toàn bộ** việc tích hợp (cả backend eCabinet + frontend)?

### Q2. Feature nào là must-have cho bản đầu tiên?
Baseline có rất nhiều feature. Bản tích hợp đầu tiên cần những gì?

- [ ] Realtime transcript (audio → text hiển thị live)
- [ ] Speaker identification (nhận diện ai nói)
- [ ] Speaker enrollment (đăng ký mẫu giọng nói trước)
- [ ] Phonetic Recovery (cứu thuật ngữ tiếng Việt)
- [ ] Crosstalk suppression (loại tiếng lọt)
- [ ] AI Minutes/Biên bản tự động (LLM)
- [ ] AI gợi ý biểu quyết (voting suggestions)
- [ ] AI tạo draft Tasks + Conclusions
- [ ] Export DOCX
- [ ] Topic Discovery (phát hiện chủ đề)

### Q3. Module `integration/qlvb` trong eCabinet là gì?
Có cần tích hợp AI meeting với hệ thống QLVB không?

---

## 2. Hạ tầng & Triển khai

### Q4. Server hiện có là gì?
- CPU hay có GPU? (WavLM, DPDFNet chạy nặng)
- RAM bao nhiêu?
- Chạy trên máy nào? (VM, bare metal, cloud?)

### Q5. LiveKit Server đã có chưa?
Trong baseline, LiveKit là thành phần quan trọng. Nó đã được deploy hay cần setup mới?
- URL hiện tại?
- Self-hosted hay LiveKit Cloud?

### Q6. Qdrant Vector DB đã có chưa?
Baseline dùng Qdrant lưu speaker embeddings. Đã deploy hay cần thêm vào docker-compose?

### Q7. Ollama (LLM local) đã có chưa?
Baseline dùng Ollama chạy Qwen2.5:3b. Đã cài đặt trên server chưa?

### Q8. Triển khai AI service như thế nào?
- **Cùng docker-compose** với eCabinet? (thêm service vào file hiện có)
- **Docker-compose riêng**? (2 stack tách biệt)
- **Chạy trực tiếp** trên host? (không container)

---

## 3. AI Models & Services

### Q9. Giữ nguyên hay thay đổi ASR engine?
Baseline dùng **Zipformer-30M** (sherpa-onnx, streaming, local). Giữ nguyên hay muốn đổi?
- Zipformer-30M (hiện tại, nhẹ, ~0.05s/1s audio trên CPU)
- PhoWhisper / Whisper (nặng hơn, có thể chính xác hơn)
- External STT API

### Q10. Giữ nguyên hay thay đổi LLM?
- Qwen2.5:3b local via Ollama (hiện tại)
- Model lớn hơn (Qwen2.5:7b/14b)?
- External API (OpenAI, vLLM)?

### Q11. Speaker Enrollment flow thế nào?
Baseline có endpoint `POST /api/enrollment` để đăng ký giọng nói. Trong eCabinet:
- User đăng ký giọng khi nào? (lúc tạo tài khoản? trước mỗi cuộc họp? lần đầu join?)
- Lưu embedding ở đâu? (Qdrant chung hay per-meeting?)

---

## 4. Kiến trúc giao tiếp

### Q12. AI service giao tiếp với eCabinet backend thế nào?
Có 2 hướng:

**Hướng A — eCabinet proxy tất cả:**
```
Browser ←→ eCabinet Backend ←→ AI Service
```
eCabinet backend nhận audio, forward tới AI service, nhận kết quả, broadcast cho client.

**Hướng B — Client kết nối trực tiếp AI service:**
```
Browser ←→ LiveKit Server ←→ agent.py ←→ AI Service
Browser ←→ eCabinet Backend (REST + Socket.IO)
```
Audio đi thẳng qua LiveKit, transcript events đi qua eCabinet Socket.IO.

→ Baseline hiện dùng **Hướng B**. Bạn muốn giữ hay đổi?

### Q13. Events từ AI service push lên eCabinet bằng cách nào?
Khi có transcript mới, AI service cần thông báo cho eCabinet backend để:
1. Lưu DB (PostgreSQL)
2. Broadcast cho tất cả client qua Socket.IO

Các option:
- **HTTP callback** (AI service POST tới eCabinet internal endpoint)
- **Redis pub/sub** (AI publish, eCabinet subscribe)
- **Shared Socket.IO** (cùng Socket.IO server)

### Q14. Authentication giữa services?
- AI service cần verify JWT token của eCabinet?
- Hay dùng internal API key (vì cùng mạng nội bộ)?

---

## 5. Dữ liệu & Database

### Q15. AI service dùng DB nào?
- **PostgreSQL chung** với eCabinet? (thêm bảng vào schema hiện có)
- **SQLite riêng** như baseline? (tách biệt data)
- **PostgreSQL riêng**? (microservice có DB riêng)

### Q16. Transcript lưu ở đâu?
- Trong PostgreSQL eCabinet (bảng `meeting_transcripts` theo implementation_plan)?
- Trong AI service DB?
- Cả hai (AI service lưu tạm, sync về eCabinet)?

### Q17. File export (DOCX biên bản) lưu ở đâu?
- MinIO của eCabinet? (tích hợp với document management)
- Local filesystem của AI service?

---

## 6. Frontend

### Q18. Frontend AI meeting nằm ở đâu?
- **Trong eCabinet React app** — thêm page `MeetingRoom.jsx` (theo implementation_plan)?
- **App riêng** — tách frontend AI ra app/page riêng?
- **Tích hợp vào `MeetingDetail.jsx`** hiện có (55KB) — thêm tab/panel AI?

### Q19. Board Display (màn hình lớn) có cần không?
[`plan.md`](file:///D:/VNPT/Code/Multi_Speaker_Streaming/ecabinet/plan.md) có nhắc tới Board Display cho phòng họp. Có cần làm trong phase đầu?

---

## 7. Quy trình & Timeline

### Q20. Có deadline hay milestone cụ thể không?
- Demo đầu tiên cần khi nào?
- Có phase nào cần ưu tiên?

---

> [!TIP]
> Không cần trả lời hết — chỉ cần trả lời những câu bạn đã có quyết định. Những câu chưa rõ tôi sẽ đề xuất default hợp lý trong plan.

---

## Câu trả lời đã xác nhận

### A1. Phạm vi tích hợp

Merge backend baseline là công việc chính. Đối với frontend, sử dụng layout và cấu trúc hiện có của eCabinet để chỉnh sửa, bổ sung giao diện cho phù hợp với toàn bộ hệ thống; không cần merge 100% frontend từ baseline.

### A2. Phạm vi feature của baseline

Merge toàn bộ feature của baseline nếu việc tích hợp không làm ảnh hưởng đến các module khác trong hệ thống eCabinet.

Nguyên tắc ưu tiên là hạn chế tối đa thay đổi kiến trúc của hệ thống chính: giữ nguyên cấu trúc module, contract API, cơ chế xác thực/phân quyền và quyền sở hữu dữ liệu của eCabinet. Phần AI được tích hợp theo hướng bổ sung adapter/service, endpoint và migration cần thiết; không viết lại các module hiện có chỉ để phù hợp với baseline.

### A3. Module `integration/qlvb`

Không cần quan tâm đến module `integration/qlvb` trong phạm vi tích hợp hiện tại. Đây chỉ là placeholder dành cho việc phát triển và tích hợp về sau.

### A4. Hạ tầng phát triển hiện tại

Các backend ứng dụng hiện chạy trực tiếp, chưa được container hóa, trên hai phần cứng chính:

- **Home server Xubuntu:** Intel N3700, RAM 8 GB, SSD 120 GB; đảm nhiệm đầu vào Nginx và phục vụ frontend.
- **Laptop Windows chạy Ubuntu WSL2:** Intel Core i5-12500H, được cấp 12 CPU và RAM 10 GB; đảm nhiệm core backend trong giai đoạn phát triển hiện tại.

### A5. LiveKit Server

LiveKit đã được self-host độc lập trên home server và được sử dụng qua endpoint:

```text
wss://livekit.simplething.id.vn
```

Trong giai đoạn development/demo, tiếp tục sử dụng LiveKit hiện có và xem đây là external infrastructure service, không đưa vào stack container của eCabinet trên WSL2. Browser và LiveKit AI Agent kết nối trực tiếp đến LiveKit; eCabinet backend chịu trách nhiệm cấp token và thông tin phòng.

URL, API key và API secret phải được truyền bằng biến môi trường, không hard-code trong source. Khi triển khai production có thể thay bằng LiveKit Server hoặc LiveKit Cloud của môi trường production mà không thay đổi contract API hay kiến trúc ứng dụng. Home server chỉ phục vụ development/demo và không được xem là hạ tầng production lâu dài.

### A6. Qdrant Vector DB

Hệ thống chính eCabinet hiện chưa sử dụng Qdrant Server. Baseline AI đang dùng Qdrant embedded/local để lưu speaker embeddings tại thư mục runtime riêng.

Trong giai đoạn tích hợp:

- Chưa thêm Qdrant Server vào Docker Compose chính của eCabinet.
- Meeting AI API tiếp tục sở hữu Qdrant embedded và mount dữ liệu bằng persistent volume.
- Chỉ một container/process AI được phép mở và ghi kho Qdrant embedded này.
- AI Core không ghi dữ liệu speaker trực tiếp vào PostgreSQL của eCabinet.
- Chỉ chuyển sang Qdrant Server độc lập khi cần chạy nhiều AI worker, scale ngang hoặc chia sẻ vector store giữa nhiều node.

### A7. Ollama và LLM local

Ollama hiện đã được cài trong Ubuntu WSL2 và baseline sử dụng model `qwen2.5:3b` để tổng hợp biên bản cuộc họp theo form.

Khi container hóa, Ollama được tách thành service container thuộc stack AI, không gộp vào eCabinet API hoặc Meeting AI API. Model cache được mount bằng persistent volume để không tải lại model sau mỗi lần build/recreate container. Ollama chỉ được truy cập qua Docker network nội bộ và không public port ra Internet.

### A8. Cách triển khai AI service

Trong giai đoạn tích hợp, chuyển các backend đang chạy trực tiếp sang container để đồng bộ với kiến trúc và quy trình vận hành của eCabinet. Các thành phần vẫn được tách theo đúng ranh giới microservice, không merge AI Core vào cùng process hoặc container với eCabinet backend:

- eCabinet API, Meeting AI API và LiveKit AI Agent là các service/container độc lập.
- PostgreSQL, Redis và MinIO tiếp tục sử dụng các service trong Docker Compose của eCabinet.
- Ưu tiên bổ sung AI bằng Compose override hoặc Compose riêng kết nối vào network hiện có, thay vì tái cấu trúc sâu file Compose và các service của eCabinet.
- Các service giao tiếp qua Docker network nội bộ; chỉ eCabinet API được Nginx public qua Tailnet.
- Model AI, Ollama cache, Qdrant speaker data và dữ liệu runtime được mount bằng bind mount hoặc volume, không đóng trực tiếp vào image.
- Frontend được build theo layout eCabinet rồi deploy dưới dạng static files trên Nginx của home server; không bắt buộc chạy bằng container tại runtime.
- LiveKit Server đang self-host trên home server được giữ độc lập với stack backend trong WSL2.

Trong giai đoạn phát triển có thể bật bind mount và hot reload. Cấu hình demo ổn định sẽ tắt hot reload, có healthcheck và restart policy cho từng service.

### A9. ASR engine

Giữ Zipformer-30M RNNT Streaming làm ASR chính trong phase tích hợp đầu tiên. Đây là model phù hợp nhất với baseline hiện tại về độ chính xác, độ trễ và khả năng chạy CPU-only; chưa đưa PhoWhisper, FunASR, Qwen3-ASR hoặc ASR candidate khác vào critical path.

Zipformer phải được bọc sau một interface ASR nội bộ để có thể thay model hoặc bổ sung decoder trong tương lai mà không thay đổi contract API của eCabinet. DPDFNet/DSP, hotword và phonetic recovery tiếp tục là chi tiết triển khai bên trong AI Core.

### A10. LLM

Giữ Qwen2.5:3b chạy qua Ollama. Không dùng LLM để refine từng dòng transcript realtime:

- Transcript realtime lấy từ Zipformer và chỉ được chuẩn hóa chữ hoa/chữ thường, khoảng trắng, dấu câu bằng logic deterministic.
- Qwen chạy bất đồng bộ sau global turn hoặc theo nhóm turn để tạo và cập nhật biên bản theo form.
- Qwen phụ trách tóm tắt, nhóm nội dung theo chủ đề, xác định đề xuất, quyết định, nhiệm vụ, người phụ trách và thời hạn.
- Nếu Ollama lỗi hoặc timeout, transcript vẫn phải được lưu; job tạo biên bản có thể retry mà không làm gián đoạn ASR realtime.

### A11. Speaker enrollment

Speaker enrollment là tùy chọn đối với việc tham gia cuộc họp, nhưng là điều kiện để nhận diện ổn định người nói khi họ di chuyển và phát biểu qua microphone khác.

Luồng enrollment:

1. Người dùng thực hiện đăng ký giọng nói từ hồ sơ eCabinet.
2. Giao diện hiển thị đoạn văn mẫu tương đương 20–30 giây nói liên tục.
3. Audio được gửi tới Meeting AI API để kiểm tra chất lượng, chạy VAD, loại silence/clipping và chia thành nhiều cửa sổ hợp lệ.
4. WavLM tạo nhiều embedding; AI Core loại outlier và xây dựng voice profile ổn định.
5. Voice profile được lưu trong Qdrant theo `ecabinet_user_id`, không sử dụng tên hiển thị làm khóa định danh.
6. Profile được dùng lại giữa các cuộc họp; chỉ đăng ký lại khi profile không đạt chất lượng hoặc người dùng chủ động cập nhật.

Trong cuộc họp, chỉ gán người nói theo voice profile khi kết quả đạt cả confidence và margin theo cơ chế open-set recognition. Nếu không đủ điều kiện, hệ thống phải từ chối speaker gần nhất và fallback về danh tính của nguồn mic/LiveKit participant. Vì vậy người chưa enroll vẫn có thể tham gia và có transcript bình thường nhưng không bị gán nhầm thành một người đã enroll. Khách không có tài khoản chỉ sử dụng danh tính meeting-scoped hoặc tên nguồn mic.

### A12. Giao tiếp giữa frontend, eCabinet và AI Core

Chọn hướng client truyền audio trực tiếp qua LiveKit, trong khi eCabinet vẫn giữ vai trò control plane và data plane của ứng dụng:

```text
Browser ── audio ──> LiveKit <── audio ── LiveKit AI Agent
   │
   └── REST + Socket.IO ──> eCabinet Backend
                                │
                                └── internal API ──> Meeting AI API
```

- Frontend chỉ gọi API nghiệp vụ và Socket.IO của eCabinet, không kết nối trực tiếp `ai_server.py`.
- Audio không được proxy qua eCabinet backend; browser và AI Agent kết nối trực tiếp tới LiveKit.
- eCabinet kiểm tra quyền, quản lý vòng đời phiên AI và cấp LiveKit token.
- Transcript, biên bản và trạng thái AI phải đi qua eCabinet trước khi được hiển thị cho frontend hoặc lưu lâu dài.

### A13. Truyền sự kiện từ AI Core về eCabinet

Trong phase tích hợp đầu tiên, sử dụng HTTP callback vì baseline đã có cơ chế tương tự và hướng này yêu cầu ít thay đổi nhất đối với eCabinet:

```text
Meeting AI
  -> POST /api/internal/v1/meeting-ai/events
  -> eCabinet kiểm tra và xử lý event
  -> lưu PostgreSQL khi cần
  -> emit Socket.IO vào room meeting:{meeting_id}
```

- `transcript.partial` chỉ cần broadcast và không bắt buộc lưu DB.
- `transcript.final` phải được lưu trước khi broadcast.
- `minutes.updated` phải tạo hoặc cập nhật revision biên bản trước khi broadcast.
- Mỗi event phải có `event_id`, `meeting_id`, `segment_id` khi phù hợp và `revision` để hỗ trợ idempotency, chống ghi trùng hoặc ghi đè dữ liệu mới bằng event cũ.
- Callback chạy ngoài critical path của audio, có timeout và retry; lỗi callback không được làm dừng ASR.
- Chưa sử dụng shared Socket.IO hoặc Redis Pub/Sub làm kênh sự kiện chính trong phase đầu.
- Redis có thể được bổ sung làm Socket.IO message manager khi eCabinet cần chạy nhiều API instance.

### A14. Authentication giữa client và các service

Tách xác thực người dùng khỏi xác thực service-to-service:

- Browser gọi REST API và Socket.IO bằng session key hiện có của eCabinet.
- eCabinet xác thực người dùng, kiểm tra quyền tham gia/chủ trì cuộc họp rồi mới gọi Meeting AI.
- eCabinet và Meeting AI xác thực lẫn nhau bằng internal API key đủ mạnh, được truyền qua environment/secret và không đưa xuống frontend.
- AI Core không trực tiếp xác minh JWT hoặc session người dùng; nó chỉ nhận `meeting_id`, `ecabinet_user_id`, vai trò và display name đã được eCabinet xác minh.
- Browser kết nối LiveKit bằng access token ngắn hạn do backend cấp.
- Các endpoint nội bộ của AI Core không được Nginx public ra Internet.

Socket.IO hiện tại của eCabinet cần được bổ sung xác thực session khi handshake, cơ chế join room `meeting:{meeting_id}` và kiểm tra quyền thành viên. CORS production phải giới hạn theo domain cấu hình thay vì cho phép mọi origin. Đây là thay đổi bổ sung tại lớp realtime/integration, không thay đổi contract của các module nghiệp vụ hiện có.

### A15. Database của AI service

Meeting AI không dùng chung kết nối PostgreSQL với eCabinet và chưa cần một PostgreSQL riêng. Quyền sở hữu dữ liệu được phân tách như sau:

- PostgreSQL eCabinet là nguồn dữ liệu chính thức cho cuộc họp, transcript final, biên bản và revision, tasks, conclusions và metadata tài liệu.
- Meeting AI sở hữu Qdrant embedded cho voice profiles, model/cache và dữ liệu runtime của pipeline.
- AI Core không truy cập trực tiếp PostgreSQL của eCabinet; kết quả được gửi về bằng HTTP callback để eCabinet kiểm tra và lưu.

SQLite `meeting.db` của baseline không còn là nguồn dữ liệu chính thức và không được frontend đọc trực tiếp sau tích hợp. Có thể giữ SQLite hoặc một local persistent queue có giới hạn chỉ làm retry spool/outbox khi callback tới eCabinet thất bại; event phải được tự xóa sau khi eCabinet xác nhận thành công.

### A16. Nơi lưu transcript và biên bản

Transcript final được lưu duy nhất trong PostgreSQL eCabinet theo bảng `meeting_transcripts` đã được dự kiến trong thiết kế. Partial transcript chỉ được broadcast qua Socket.IO và không bắt buộc lưu DB.

Bảng transcript cần hỗ trợ tối thiểu các thông tin: `event_id`, `session_id`, `segment_id`, nguồn mic/LiveKit participant, `speaker_user_id` nullable, nhãn người nói, phương thức định danh, raw text, content text, thời gian bắt đầu/kết thúc, confidence, revision, trạng thái và pipeline metadata. `event_id` phải unique; revision chỉ được cập nhật khi mới hơn. Đoạn bị loại do crosstalk cần được đánh dấu `RETRACTED` hoặc xóa mềm để có thể truy vết.

Biên bản AI được lưu trong `meeting_summaries` theo thiết kế hiện có thay vì tạo một module biên bản song song. AI chỉ tạo draft; dữ liệu chỉ được chuyển thành bản ghi trong `meeting_tasks` và `meeting_conclusions` hiện có sau khi người có quyền xác nhận.

### A17. Lưu và xuất DOCX

DOCX được tạo bởi eCabinet backend từ structured minutes, sau đó lưu qua `StorageService` hiện có vào MinIO và tạo metadata trong bảng `files`. AI Core không trực tiếp tạo bản ghi tài liệu, truy cập MinIO hoặc cung cấp endpoint download riêng.

Luồng xử lý:

```text
Structured minutes
  -> DOCX exporter của eCabinet
  -> StorageService/MinIO hiện có
  -> bản ghi files gắn session_id và revision
  -> API ACL/preview/download hiện có
```

Bổ sung `python-docx`, template biên bản và một exporter độc lập. Mỗi revision nên tạo file mới thay vì ghi đè file cũ. Việc export phải kiểm tra quyền, có idempotency theo `meeting_id + minutes_revision`, dọn object MinIO nếu transaction DB thất bại và trả error code rõ ràng cho lỗi quyền, dữ liệu, xung đột revision hoặc storage không khả dụng.

Nguyên tắc chung của A15–A17 là tích hợp theo hướng additive và hạn chế xâm lấn các module hiện có. Ưu tiên tạo module/service mới và tái sử dụng public abstraction của eCabinet; không thay đổi contract các route document, upload, preview, download, task hoặc conclusion hiện tại nếu không thật sự cần thiết.

### A18. Frontend AI Meeting

Frontend AI Meeting được tích hợp trong React app của eCabinet. UI hiện có của eCabinet là layout và design system chuẩn để chỉnh sửa, bổ sung giao diện họp; không merge nguyên trạng hoặc sao chép 100% frontend của baseline.

Để hạn chế ảnh hưởng tới `MeetingDetail.jsx` hiện có, giao diện họp realtime nên được tách thành page/feature riêng, ví dụ route `/meetings/:id/live`, nhưng vẫn tái sử dụng theme, typography, component, navigation và quy tắc responsive của eCabinet. `MeetingDetail` chỉ cần bổ sung entry point như nút “Vào phòng họp” và khu vực xem dữ liệu sau họp khi phù hợp.

Các thành phần audio controls, participant panel, realtime transcript, minutes timeline/editor và trạng thái pipeline được đặt trong feature frontend riêng để không làm tăng coupling với các màn hình chương trình họp, tài liệu và kết luận hiện có.

### A19. Board Display

Board Display được triển khai theo UI/layout mẫu của eCabinet để đồng bộ với hệ thống chính, không sử dụng nguyên giao diện baseline. Bản đầu tiên là màn hình read-only tối thiểu, có thể dùng route `/meetings/:id/board`, hiển thị người đang nói, transcript realtime và các thông tin cuộc họp cần thiết.

Board Display sử dụng cùng API và Socket.IO room `meeting:{meeting_id}` với Meeting Workspace; không tạo backend hoặc event contract riêng. Màn hình có thể dùng layout toàn màn hình phù hợp máy chiếu nhưng vẫn phải tái sử dụng design tokens và component chung của eCabinet. Các chức năng điều khiển, chỉnh sửa và nghiệp vụ nâng cao không nằm trong Board Display của bản demo đầu tiên.

### A20. Deadline và milestone

Mục tiêu là hoàn thành bản demo tích hợp đầu tiên trong 5–6 ngày làm việc. Khoảng thời gian này cho phép refactor sâu theo ranh giới microservice nhưng chỉ thay đổi cấu trúc, contract và cách triển khai; không đồng thời thay đổi thuật toán ASR, enhancement, speaker identification, phonetic recovery hoặc Minutes Composer đã được kiểm thử. Hệ thống vẫn giới hạn một phiên AI hoạt động tại một thời điểm trên phần cứng hiện tại.

Phạm vi ưu tiên trong 5–6 ngày:

1. Freeze baseline và kết quả regression trước khi di chuyển code.
2. Tách AI Core theo kiến trúc API/application/core/infrastructure bằng compatibility wrapper để entrypoint và test cũ tiếp tục chạy.
3. Container hóa Meeting AI API, LiveKit AI Agent và Ollama với model/data volume, warm-up, healthcheck và graceful shutdown.
4. Bổ sung integration adapter, callback, models/migration, LiveKit token và Socket.IO meeting room có xác thực vào eCabinet.
5. Tạo Meeting Workspace, Voice Enrollment và Board Display tối thiểu theo UI eCabinet.
6. Chạy được luồng LiveKit audio -> transcript partial/final -> speaker identity/fallback -> lưu PostgreSQL -> hiển thị realtime.
7. Tạo, xem, chỉnh sửa và quản lý revision draft biên bản; export DOCX qua module document/MinIO hiện có.
8. Chạy regression, E2E bằng Edge qua Nginx home server và hoàn thiện tài liệu khởi động/dừng/rollback.

Mốc 5–6 ngày chưa bao gồm mức hoàn thiện production như multi-room concurrency, scale ngang, high availability, load test dài, message broker/outbox bền vững hoàn chỉnh, hardening bảo mật toàn diện, feature AI voting suggestion mới hoặc tối ưu lại độ chính xác ASR/LLM. Các hạng mục này được tách thành phase tiếp theo sau khi demo E2E ổn định.
