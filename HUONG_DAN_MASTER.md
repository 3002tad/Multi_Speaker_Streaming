# Hướng dẫn master — chạy manual test Meeting Platform + eCabinet

Tài liệu này mô tả cách chạy hệ thống demo hiện tại từ hai source độc lập.

```text
D:\VNPT\Code\Multi_Speaker_Streaming\
├── meeting_ai/                  # Meeting AI Core
├── meeting_service/             # Meeting Service
├── tests/                       # unit/contract/regression tests
└── ecabinet/                    # repository eCabinet độc lập
    ├── backend/
    └── frontend/
```

eCabinet là hệ thống core. Meeting Service sở hữu runtime/transcript/minutes;
không dùng database chung với eCabinet.

## 1. Điều kiện môi trường

- Windows + Docker Desktop, đã bật WSL2 Integration.
- Ubuntu WSL2 đang truy cập được Docker daemon.
- Python runtime chính: `/home/ntd/meeting_runtime/venv_linux/bin/python`.
- Node.js Linux user-local (nếu cần build ngoài Docker):
  `/home/ntd/meeting_runtime/node/bin`.

Kiểm tra:

```bash
wsl.exe docker version
wsl.exe docker compose version
wsl.exe /home/ntd/meeting_runtime/venv_linux/bin/python --version
```

Không đặt `.env`, model, cache, Qdrant data hoặc runtime output vào Git.

## 2. Khởi chạy eCabinet core

```bash
cd /mnt/d/VNPT/Code/Multi_Speaker_Streaming/ecabinet/backend
docker compose build api
docker compose up -d postgres redis minio
docker compose run --rm --no-deps migration
docker compose run --rm --no-deps init_data
docker compose up -d api
```

Khi chạy cùng Meeting Platform LAN, phải truyền cùng private env để BFF eCabinet
gọi được Meeting Service; nếu bỏ qua bước này, endpoint ghi danh sẽ trả `401`:

```bash
cd /mnt/d/VNPT/Code/Multi_Speaker_Streaming
bash scripts/validate_meeting_env.sh /home/ntd/meeting_runtime/meeting-platform.lan.env

cd /mnt/d/VNPT/Code/Multi_Speaker_Streaming/ecabinet/backend
docker compose --env-file /home/ntd/meeting_runtime/meeting-platform.lan.env \
  -p ecabinet_backend -f docker-compose.yml up -d --force-recreate api
```

Quy tắc bàn giao: `MEETING_SERVICE_KEY` và `INTERNAL_API_KEY` trong **một** file
`/home/ntd/meeting_runtime/meeting-platform.lan.env` phải giống hệt nhau;
`MEETING_RUNTIME_TOKEN_SECRET` là khóa khác, dùng chung giữa eCabinet Backend
và Meeting Service. Không dùng `ecabinet/backend/.env` riêng lẻ cho stack tích
hợp nếu chưa đồng bộ bằng file này. Script chỉ in độ dài/hash rút gọn, không in
giá trị bí mật.

Healthcheck:

```bash
curl -fsS http://127.0.0.1:8080/api/heartbeat
docker compose ps
```

Tài khoản demo local được tạo bởi `init_data`:

```text
username: admin
password: admin@123
```

## 3. Khởi chạy Meeting Platform containerized

Meeting Service, Meeting AI Core, LiveKit Agent và Ollama chạy cùng Compose;
PostgreSQL/Redis/MinIO vẫn là dữ liệu riêng của Meeting Service. Không xóa
volume `backend_*`.

```bash
cd /mnt/d/VNPT/Code/Multi_Speaker_Streaming
set -a
source /home/ntd/meeting_runtime/.env.meeting
set +a
export MEETING_SERVICE_ENV_FILE=/home/ntd/meeting_runtime/.env.meeting
export MEETING_AI_ENV_FILE=/home/ntd/meeting_runtime/.env.ai

docker compose --env-file "$MEETING_SERVICE_ENV_FILE" \
  -f meeting_service/docker-compose.yml \
  -f deploy/compose.meeting-platform.yml up -d --build
```

Healthcheck:

```bash
curl -fsS http://127.0.0.1:8002/health/live
curl -fsS http://127.0.0.1:8002/health/ready
docker compose --env-file "$MEETING_SERVICE_ENV_FILE" \
  -f meeting_service/docker-compose.yml \
  -f deploy/compose.meeting-platform.yml ps
```

Chi tiết mount runtime/model và lệnh pull Qwen lần đầu nằm ở
[`deploy/README.md`](deploy/README.md). Không chạy `ai_server.py` hoặc
`agent.py` native khi stack Compose đang hoạt động.

### E2E audio đồng bộ (khuyến nghị thay cho chạy thủ công từng service)

Khi cần kiểm thử một WAV qua LiveKit → Agent → AI → transcript → Qwen minutes,
dùng runner duy nhất sau. Nó đọc secret từ `/home/ntd/meeting_runtime/.env`,
không tạo hay commit file `.env` mới.

```bash
cd /mnt/d/VNPT/Code/Multi_Speaker_Streaming
bash scripts/run_e2e_streaming.sh --audio audio/thayDung_noi.wav
```

Runner từ chối chạy nếu Meeting Service stack đang tồn tại để tránh tắt nhầm
container đang được dùng. Dừng stack hiện tại trước, hoặc dùng `--keep` khi cần
giữ stack sau khi probe đạt. Chi tiết về profile nằm tại
`configs/e2e/README.md`.

Nếu backend eCabinet cần gọi Meeting Service trong local Docker demo, nối
container API vào network runtime:

```bash
docker network connect meeting_platform_internal backend-api-1
```

Lệnh trên chỉ thay đổi network runtime, không sửa cấu hình core eCabinet.

## 4. Khởi chạy frontend

### Cách khuyến nghị: Docker

```bash
cd /mnt/d/VNPT/Code/Multi_Speaker_Streaming/ecabinet/frontend
docker compose up -d --build
curl -I http://127.0.0.1:3000
```

### Build bằng Node.js trong WSL

```bash
cd /mnt/d/VNPT/Code/Multi_Speaker_Streaming/ecabinet/frontend
export PATH=/home/ntd/meeting_runtime/node/bin:/usr/bin:/bin
npm ci
npm run build
```

`node_modules/` và `dist/` là artifact sinh ra, không commit.

## 5. Manual test UI hiện tại

Mở:

```text
http://127.0.0.1:3000
```

Luồng kiểm tra:

1. Đăng nhập bằng tài khoản demo.
2. Mở **Phiên họp**.
3. Chọn **Đăng ký lịch họp**.
4. Nhập tiêu đề, thời gian bắt đầu/kết thúc, phòng họp.
5. Chọn ít nhất một người chủ trì.
6. Lưu phiên họp.
7. Mở chi tiết phiên họp.
8. Bấm **Bắt đầu cuộc họp**.
9. Kiểm tra runtime chuyển sang Meeting Workspace.
10. Kiểm tra transcript nguồn và biên bản.
11. Bấm **Tạo biên bản từ transcript** lần đầu để tạo bản nháp và bật auto-update.
12. Nói thêm một đoạn mới; sau khoảng 5 giây yên lặng, biên bản DRAFT sẽ cập nhật.
13. Bấm **Tắt tự cập nhật** nếu muốn giữ bản hiện tại; transcript realtime vẫn tiếp tục.
14. Sửa JSON biên bản và bấm **Lưu revision**.

URL workspace:

```text
/meetings/{meeting_id}/workspace
```

REST smoke test Meeting Service:

```bash
curl http://127.0.0.1:8002/internal/v1/meetings/{meeting_id}/transcript
curl http://127.0.0.1:8002/internal/v1/meetings/{meeting_id}/minutes
```

## 6. Phạm vi test hiện tại

Đã có thể test:

- eCabinet login/list/detail phiên họp.
- Runtime start/status/stop REST.
- Transcript/minutes REST contract.
- Meeting Workspace và lưu revision in-memory.
- Frontend/backend/Meeting Service healthcheck.

Chưa hoàn thiện cho manual voice realtime:

- LiveKit token và browser audio publish.
- Agent join phòng LiveKit.
- Audio Agent → Meeting AI Core.
- Transcript callback realtime qua Socket.IO trên UI.
- Enrollment, playback, multi-mic/crosstalk.

Do đó không dùng manual UI hiện tại để kết luận voice pipeline đã E2E pass.

## 7. Chạy unit/contract test

```bash
cd /mnt/d/VNPT/Code/Multi_Speaker_Streaming
wsl.exe --cd /mnt/d/VNPT/Code/Multi_Speaker_Streaming \
  /home/ntd/meeting_runtime/venv_linux/bin/python -m unittest \
  tests.test_meeting_service_skeleton tests.test_runtime_token
```

Compile nhanh:

```bash
wsl.exe --cd /mnt/d/VNPT/Code/Multi_Speaker_Streaming \
  /home/ntd/meeting_runtime/venv_linux/bin/python -m compileall -q meeting_service
```

## 8. Dừng hệ thống an toàn

```bash
cd /mnt/d/VNPT/Code/Multi_Speaker_Streaming/ecabinet/frontend
docker compose down

cd /mnt/d/VNPT/Code/Multi_Speaker_Streaming/ecabinet/backend
docker compose down

cd /mnt/d/VNPT/Code/Multi_Speaker_Streaming
docker compose --env-file "$MEETING_SERVICE_ENV_FILE" \
  -f meeting_service/docker-compose.yml \
  -f deploy/compose.meeting-platform.yml down --remove-orphans
```

Các lệnh trên chỉ dừng/xóa container và network của compose, không xóa volume.

Không chạy `docker compose down -v` trên eCabinet nếu chưa có backup và xác nhận
rõ, vì sẽ xóa dữ liệu core (user, phiên họp, tài liệu, MinIO metadata).

## 9. Reset Meeting Service demo

Chỉ khi muốn làm sạch dữ liệu Meeting Service, được phép xóa các volume riêng:

```text
meeting_service_meeting_postgres_data
meeting_service_meeting_redis_data
```

Không xóa:

```text
backend_pg_data
backend_redis_data
backend_minio_data
backend_upload_data
```

## 10. Public/demo qua Nginx

Trong local, dùng các port `3000`, `8080`, `8002`. Khi public:

- Nginx proxy `/api` tới eCabinet BFF.
- Nginx proxy `/meeting-runtime/socket.io` tới Meeting Service.
- Không public PostgreSQL, Redis, MinIO, Ollama hoặc AI API.
- LiveKit dùng domain/media port riêng.

Kiểm tra public chỉ sau khi local healthcheck và E2E pass.

## 11. Quy tắc bàn giao

Đây là hai Git repository độc lập. Không push repository eCabinet.

Khi đóng gói ZIP, loại bỏ:

- `.git/`
- `.env`
- `node_modules/`, `dist/`, `__pycache__/`
- model/cache/Hugging Face/Ollama/Qdrant data
- Docker volume và runtime output
# Chạy demo LAN (không public)

Chế độ LAN chạy toàn bộ Meeting Platform, gồm LiveKit, trên laptop Docker/WSL.
Không dùng DDNS, Tailscale, port-forward hay LiveKit public.

1. Xác định IPv4 LAN của laptop host và đặt cố định/reserve DHCP.
2. Sao chép [meeting-platform.lan.env.example](deploy/meeting-platform.lan.env.example)
   ra ngoài source, ví dụ `/home/ntd/meeting_runtime/meeting-platform.lan.env`;
   thay toàn bộ giá trị `replace-with-...` bằng secret ngẫu nhiên.
3. Đặt `MEETING_LAN_HOST_IP`, `MEETING_LAN_TLS_HOST=<LAN-IP>` và
   `LIVEKIT_PUBLIC_URL=wss://<LAN-IP>:7880` (hoặc hostname LAN trỏ về IP đó).
4. Khởi động:

```bash
cd /mnt/d/VNPT/Code/Multi_Speaker_Streaming/meeting_service
docker compose --env-file /home/ntd/meeting_runtime/meeting-platform.lan.env \
  -p meeting_platform -f docker-compose.yml \
  -f ../deploy/compose.meeting-platform.yml \
  -f ../deploy/compose.meeting-platform.lan.yml up -d --build
```

5. Cài CA nội bộ do `livekit-lan-tls` tạo cho từng laptop/điện thoại demo,
   sau đó mở UI eCabinet tại `https://<LAN-IP>:3443`. CA nằm trong volume
   Caddy tại `/data/caddy/pki/authorities/local/root.crt`; có thể xuất bằng
   `docker cp` ra file `.crt`. Không dùng UI HTTP cho microphone: trình duyệt
   sẽ từ chối `getUserMedia` trên origin không an toàn.

Media LAN dùng `7882/UDP`; `7881/TCP` chỉ là fallback. Chỉ mở `3443/TCP`,
`7880/TCP`, `7881/TCP`, `7882/UDP` trong firewall của laptop host; không expose Meeting Service,
AI, Ollama, PostgreSQL, Redis hoặc MinIO ra LAN.
