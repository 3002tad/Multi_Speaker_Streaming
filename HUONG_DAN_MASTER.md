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

## 3. Khởi chạy Meeting Service

Meeting Service dùng PostgreSQL/Redis riêng. Không xóa volume `backend_*`.

```bash
cd /mnt/d/VNPT/Code/Multi_Speaker_Streaming/meeting_service
docker compose -p meeting_service up -d --build
```

Healthcheck:

```bash
curl -fsS http://127.0.0.1:8002/health/live
curl -fsS http://127.0.0.1:8002/health/ready
docker compose -p meeting_service ps
```

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
11. Sửa JSON biên bản và bấm **Lưu revision**.

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

cd /mnt/d/VNPT/Code/Multi_Speaker_Streaming/meeting_service
docker compose -p meeting_service down
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

