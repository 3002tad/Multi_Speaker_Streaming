# Meeting Platform Compose

P1-05 chạy Meeting Service, Meeting AI API, LiveKit Agent và Ollama trong cùng
network `meeting_platform_internal`. PostgreSQL, Redis và MinIO vẫn do
`meeting_service/docker-compose.yml` sở hữu. eCabinet không bị sửa compose,
database hoặc module hiện có.

## Runtime ngoài source

Chuẩn bị một env file private ở ngoài repository, ví dụ
`/home/ntd/meeting_runtime/meeting-platform.env`, dựa trên
`meeting-platform.env.example`. File này phải có LiveKit credential và cùng
`MEETING_SERVICE_KEY`/`INTERNAL_API_KEY` ngẫu nhiên 24+ ký tự.

`MEETING_RUNTIME_HOST_PATH` mặc định `/home/ntd/meeting_runtime` được mount
vào `/runtime` cho duy nhất `meeting-ai-api`. Nó giữ Zipformer, WavLM Hugging
Face cache, G2P model, Qdrant profile, glossary và log. Agent không mount model
hay Qdrant. Ollama giữ model ở `${MEETING_RUNTIME_HOST_PATH}/ollama`.

## Start

```bash
cd /mnt/d/VNPT/Code/Multi_Speaker_Streaming
set -a
source /home/ntd/meeting_runtime/meeting-platform.env
set +a
export MEETING_PLATFORM_ENV_FILE=/home/ntd/meeting_runtime/meeting-platform.env

docker compose \
  --env-file "$MEETING_PLATFORM_ENV_FILE" \
  -f meeting_service/docker-compose.yml \
  -f deploy/compose.meeting-platform.yml \
  up -d --build
```

Lần đầu, pull model minutes vào volume runtime của Ollama:

```bash
docker compose \
  --env-file "$MEETING_PLATFORM_ENV_FILE" \
  -f meeting_service/docker-compose.yml \
  -f deploy/compose.meeting-platform.yml \
  exec ollama ollama pull qwen2.5:3b
```

## Health và E2E

```bash
curl -fsS http://127.0.0.1:8002/health/ready
docker compose -f meeting_service/docker-compose.yml -f deploy/compose.meeting-platform.yml \
  exec -T meeting-ai-api python -c \
  "from urllib.request import urlopen; print(urlopen('http://127.0.0.1:8001/health/ready').read().decode())"

bash scripts/run_e2e_streaming.sh --audio audio/thayDung_noi.wav
```

`run_e2e_streaming.sh` dùng AI/Agent containerized, không khởi động
`run_demo.sh`, `ai_server.py` hoặc `agent.py` native. Runner từ chối chạy nếu
model Ollama chưa được pull, để không phát sinh download lớn âm thầm trong test.

## Stop

```bash
docker compose \
  --env-file "$MEETING_PLATFORM_ENV_FILE" \
  -f meeting_service/docker-compose.yml \
  -f deploy/compose.meeting-platform.yml \
  down --remove-orphans
```

Không dùng `down -v`: sẽ xóa volume PostgreSQL/Redis/MinIO của Meeting Service.
Không public port `8001`, Ollama, PostgreSQL, Redis hoặc MinIO. Chỉ Meeting
Service bind loopback `127.0.0.1:8002`; Nginx/eCabinet truy cập qua network
nội bộ hoặc proxy đã được cấu hình ở phase public.
