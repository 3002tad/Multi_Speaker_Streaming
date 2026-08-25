# Hướng dẫn cấu hình và chạy toàn bộ demo

Hướng dẫn này dành cho gói ZIP bàn giao. Chạy trong Ubuntu WSL2 hoặc Linux có
Docker Compose v2. Không chạy `ai_server.py` hay `agent.py` native khi stack
container đang hoạt động.

## 1. Điều kiện

- Docker Engine/Docker Desktop đã chạy; WSL2 Integration đã bật nếu dùng Windows.
- Máy trong LAN truy cập được IP của máy host chạy Docker.
- Python/model/runtime đặt ngoài source tại `/home/<user>/meeting_runtime`.
- Có sẵn Zipformer model, model speaker-ID và các file model phụ trợ theo cấu hình.

Giải nén ZIP vào một thư mục, ví dụ:

```bash
cd /mnt/d/VNPT/Code
unzip PaperlessMeeting_Demo.zip -d PaperlessMeeting_Demo
cd PaperlessMeeting_Demo
```

## 2. Chuẩn bị runtime và cấu hình private

```bash
export MEETING_RUNTIME_DIR=/home/$USER/meeting_runtime
mkdir -p "$MEETING_RUNTIME_DIR"/{data,models,huggingface,ollama,python_cache}
cp meeting_service/deploy/meeting-platform.lan.env.example \
  "$MEETING_RUNTIME_DIR/meeting-platform.lan.env"
chmod 600 "$MEETING_RUNTIME_DIR/meeting-platform.lan.env"
```

Mở file env private và điền tối thiểu:

```dotenv
MEETING_RUNTIME_HOST_PATH=/home/<user>/meeting_runtime
MEETING_LAN_HOST_IP=<IPv4-LAN-của-máy-chạy-Docker>
MEETING_LAN_TLS_HOST=<hostname-LAN-hoặc-IP-theo-hướng-dẫn-cert>
LIVEKIT_PUBLIC_URL=wss://<hostname-LAN-hoặc-IP>:7880
LIVEKIT_API_KEY=<khóa-LiveKit>
LIVEKIT_API_SECRET=<secret-LiveKit>
MEETING_SERVICE_KEY=<chuỗi-ngẫu-nhiên-tối-thiểu-32-ký-tự>
INTERNAL_API_KEY=<giống-MEETING_SERVICE_KEY>
MEETING_RUNTIME_TOKEN_SECRET=<chuỗi-ngẫu-nhiên-khác>
MEETING_DB_PASSWORD=<mật-khẩu-database>
```

`MEETING_SERVICE_KEY` và `INTERNAL_API_KEY` phải giống hệt nhau.
`MEETING_RUNTIME_TOKEN_SECRET` phải khác hai khóa trên, nhưng dùng chung giữa
eCabinet Backend và Meeting Service. Không đưa file env vào source hoặc ZIP.

Kiểm tra an toàn (script chỉ in độ dài/hash rút gọn):

```bash
bash meeting_service/scripts/validate_meeting_env.sh \
  "$MEETING_RUNTIME_DIR/meeting-platform.lan.env"
```

Sao chép các model ASR/speaker-ID cần thiết vào `$MEETING_RUNTIME_DIR` theo các
đường dẫn trong env/Compose. Model Ollama không nằm trong ZIP và sẽ được pull ở
bước khởi động.

## 3. Khởi động eCabinet

```bash
cd ecabinet/backend
docker compose --env-file "$MEETING_RUNTIME_DIR/meeting-platform.lan.env" \
  -p ecabinet_backend up -d --build postgres redis minio api

cd ../frontend
docker compose -p ecabinet_frontend up -d --build
```

Lần đầu cần migration/init data theo README của eCabinet trước khi chạy `api`.
Sau đó kiểm tra:

```bash
curl -fsS http://127.0.0.1:8080/api/heartbeat
curl -I http://127.0.0.1:3000
```

## 4. Khởi động Meeting Platform LAN

Tại thư mục gốc của gói:

```bash
docker compose -p meeting_platform \
  --env-file "$MEETING_RUNTIME_DIR/meeting-platform.lan.env" \
  -f meeting_service/docker-compose.yml \
  -f meeting_service/deploy/compose.meeting-platform.yml \
  -f meeting_service/deploy/compose.meeting-platform.lan.yml \
  up -d --build
```

Pull model minutes một lần:

```bash
docker compose -p meeting_platform \
  --env-file "$MEETING_RUNTIME_DIR/meeting-platform.lan.env" \
  -f meeting_service/docker-compose.yml \
  -f meeting_service/deploy/compose.meeting-platform.yml \
  -f meeting_service/deploy/compose.meeting-platform.lan.yml \
  exec ollama ollama pull qwen2.5:3b
```

Healthcheck:

```bash
curl -fsS http://127.0.0.1:8002/health/ready
docker compose -p meeting_platform \
  --env-file "$MEETING_RUNTIME_DIR/meeting-platform.lan.env" \
  -f meeting_service/docker-compose.yml \
  -f meeting_service/deploy/compose.meeting-platform.yml \
  -f meeting_service/deploy/compose.meeting-platform.lan.yml ps
```

Tất cả container Meeting phải mang tiền tố `meeting_platform-`. Không trộn với
project name `meeting_service`, vì sẽ tạo image/container khác.

## 5. Test UI

1. Mở `http://<LAN-IP>:3000` hoặc `http://127.0.0.1:3000` trên máy host.
2. Đăng nhập eCabinet, tạo/chọn phiên họp và gán Chair/Member/Observer.
3. Chair bấm **Bắt đầu phòng họp**; Member bấm **Tham gia**; Observer chỉ nghe.
4. Cho phép microphone; kiểm tra roster, transcript final và speaker name.
5. Bấm **Tạo biên bản từ transcript** một lần để bật auto-update.
6. Nói thêm một đoạn final, chờ cửa sổ debounce; kiểm tra biên bản có evidence.
7. Bấm **Kết thúc họp**; UI phải về trạng thái COMPLETED và mở được tab Kết luận.

Khuyến nghị đeo tai nghe nếu bật playback để tránh audio loop/crosstalk.

## 6. Dừng an toàn

```bash
cd <thu-mục-gốc-gói>
docker compose -p meeting_platform \
  --env-file "$MEETING_RUNTIME_DIR/meeting-platform.lan.env" \
  -f meeting_service/docker-compose.yml \
  -f meeting_service/deploy/compose.meeting-platform.yml \
  -f meeting_service/deploy/compose.meeting-platform.lan.yml \
  down --remove-orphans

cd ecabinet/frontend && docker compose -p ecabinet_frontend down
cd ../backend && docker compose -p ecabinet_backend down
```

Không dùng `down -v` nếu chưa backup và xác nhận xóa dữ liệu. Lệnh đó có thể xóa
PostgreSQL/Redis/MinIO volume của demo.

## 7. Lỗi thường gặp

- `401` từ Meeting Service: kiểm tra ba khóa nội bộ theo bước 2 và recreate API
  eCabinet bằng cùng file env.
- LiveKit chưa sẵn sàng: kiểm tra `MEETING_LAN_HOST_IP`, URL WSS, certificate và
  firewall TCP 7880/7881, UDP 7882 trong LAN.
- Mic không publish: kiểm tra role Member/Chair, quyền browser microphone và
  HTTPS/certificate nếu truy cập từ điện thoại.
- Biên bản chậm: Qwen chạy CPU, minutes xử lý hậu kỳ; transcript realtime không
  phải chờ Qwen. Kiểm tra `docker compose ... logs meeting-ai-api ollama`.
