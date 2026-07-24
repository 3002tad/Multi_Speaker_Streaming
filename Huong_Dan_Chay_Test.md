# Hướng dẫn chạy demo và kiểm thử phòng họp không giấy

Tài liệu này áp dụng cho kiến trúc hiện tại:

```text
Laptop người dùng
    → HTTPS/WSS Nginx trên home server
    → LiveKit public
    → Tailscale
    → Web/API và AI pipeline trong Ubuntu WSL
```

Tất cả lệnh Python của project phải sử dụng `venv_linux`.

## 1. Chuẩn bị `.env`

Trong Ubuntu WSL:

```bash
cd /mnt/d/VNPT/Code/Multi_Speaker_Streaming
cp .env.example .env
```

Điền các giá trị tương ứng với LiveKit trên home server:

```dotenv
LIVEKIT_URL=wss://livekit.simplething.id.vn
LIVEKIT_API_KEY=<API key>
LIVEKIT_API_SECRET=<API secret>
MEETING_ROOM=paperless-demo
MEETING_CODE=DEMO-001
INTERNAL_API_KEY=<chuỗi ngẫu nhiên dài tối thiểu 24 ký tự>
OLLAMA_MODEL=qwen2.5:0.5b
ENABLE_LLM_REFINEMENT=true
```

Không lưu mật khẩu `sudo` trong `.env`. File `.env` đã được Git ignore.

Kiểm tra model Ollama:

```bash
ollama list
```

Nếu chưa có model:

```bash
ollama pull qwen2.5:0.5b
```

## 2. Cấu hình home server

Hai domain public:

- Web: `https://meet.simplething.id.vn`
- LiveKit: `wss://livekit.simplething.id.vn`

Nginx của domain Web phải:

- Phục vụ frontend từ `/var/www/meeting`.
- Proxy `/api/` và `/ws/` về backend WSL qua Tailscale:
  `http://100.64.0.4:8000`.
- Cho phép WAV enrollment 20–30 giây bằng:

```nginx
client_max_body_size 10m;
```

Cấu hình mẫu nằm tại:

```text
deploy/nginx/meet.simplething.id.vn
```

Sau khi stage file lên server:

```bash
sudo cp /home/ntd/meeting-deploy/meet.simplething.id.vn \
  /etc/nginx/sites-available/meet.simplething.id.vn

sudo cp /home/ntd/meeting-deploy/app.js \
  /var/www/meeting/app.js

sudo nginx -t
sudo systemctl reload nginx
```

## 3. Khởi động pipeline trong WSL

```bash
cd /mnt/d/VNPT/Code/Multi_Speaker_Streaming
chmod +x scripts/run_demo.sh
bash scripts/run_demo.sh
```

Script thực hiện:

1. Kiểm tra `.env`.
2. Warm-up Qwen theo đúng prompt refinement.
3. Chạy Web/API tại `0.0.0.0:8000`.
4. Chạy AI pipeline tại `127.0.0.1:8001`.
5. Chạy LiveKit worker và tự reconnect khi kết nối bị gián đoạn.

Chờ log:

```text
[worker] Sẵn sàng nhận các luồng microphone.
```

Kiểm tra từ WSL:

```bash
curl http://127.0.0.1:8000/api/health
```

Kiểm tra từ home server:

```bash
curl http://100.64.0.4:8000/api/health
```

## 4. Enrollment người nói

### Enrollment từ Web

1. Mở `https://meet.simplething.id.vn`.
2. Nhập đúng tên hiển thị.
3. Mở chức năng enrollment.
4. Đọc liên tục đoạn văn mẫu trong 20–30 giây.
5. Dừng ghi và chờ AI tạo vân tay giọng nói.

Đọc bằng một giọng duy nhất, giữ khoảng cách tới mic ổn định, tránh tiếng
người khác và không để âm lượng bị vỡ. Pipeline yêu cầu tối thiểu 6 giây
giọng sạch, loại cửa sổ quá nhỏ/quá lớn hoặc không nhất quán, sau đó lưu
nhiều prototype thay vì chỉ một vector trung bình.

Sau khi nâng cấp thuật toán speaker profile lên phiên bản 2, cần enrollment
lại từng người để profile cũ được thay thế bằng bộ prototype mới.

Speaker profile được lưu persistent tại:

```text
data/qdrant_speakers/
```

Profile không mất khi restart AI process.

### Enrollment bằng file mẫu

```bash
curl -F speaker_name=Thay_Dung \
  -F file=@audio/thayDung_goc.wav \
  http://127.0.0.1:8000/api/enrollment

curl -F speaker_name=Thay_Phuoc \
  -F file=@audio/thayPhuoc_goc.wav \
  http://127.0.0.1:8000/api/enrollment
```

Nếu trình duyệt báo máy chủ trả HTML thay vì JSON hoặc HTTP 413, kiểm tra
`client_max_body_size 10m` trong Nginx.

## 5. Kịch bản demo ba laptop

1. Mở `https://meet.simplething.id.vn` trên ba laptop.
2. Chủ trì nhập tên và bấm **Tạo phòng**.
3. Hai thành viên nhập mã `DEMO-001` và bấm **Tham gia**.
4. Playback mặc định tắt. Khi bật playback phải đeo tai nghe để tránh vọng.
5. Mỗi người nói khoảng 15 giây.
6. Theo dõi transcript nháp realtime và biên bản theo timeline.
7. Đổi vị trí laptop/micro để xác nhận hệ thống định danh theo giọng nói,
   không cố định người nói theo vị trí mic.

Biên bản được phát theo hai bước:

1. Bản provisional xuất trong ngân sách realtime.
2. Qwen hoàn tất refinement và cập nhật revision mới trên cùng
   `segment_id`, không tạo dòng biên bản trùng.

## 6. Test hai micro có lọt âm

Hai file `*_noi.wav` là hai bản ghi đồng bộ trong cùng không gian. Khi một
người nói gần mic A, mic B vẫn thu được tiếng lọt và ngược lại.

Đảm bảo pipeline đang chạy và hai người đã enrollment, sau đó:

```bash
cd /mnt/d/VNPT/Code/Multi_Speaker_Streaming
venv_linux/bin/python -B test_client.py
```

Test tạo hai participant LiveKit độc lập và publish đồng thời:

- `thayDung_noi.wav` qua participant `Mic A`.
- `thayPhuoc_noi.wav` qua participant `Mic B`.

Luồng kiểm thử:

```text
WAV → LiveKit → agent → VAD/Zipformer → cross-mic arbiter
    → WavLM → Qwen → WebSocket/SQLite
```

Kiểm tra transcript đã lưu:

```bash
curl http://127.0.0.1:8000/api/transcripts
```

Xóa dữ liệu transcript trước lượt test mới:

```bash
curl -X DELETE http://127.0.0.1:8000/api/transcripts
```

## 7. Tiêu chí nghiệm thu

- Ba laptop join được cùng phòng `paperless-demo`.
- Mỗi laptop publish được một microphone track.
- Transcript nháp cập nhật realtime.
- Lượt nói dài được chia thành segment xấp xỉ 15 giây.
- WavLM gán đúng người đã enrollment dù đổi vị trí mic.
- Chỉ nhận tên theo voice profile khi nhiều cửa sổ giọng nói cùng đạt ngưỡng
  điểm, margin và consensus.
- Hai mic thu cùng một câu chỉ tạo một bản final; arbiter giữ nguồn có RMS
  tốt hơn.
- Nội dung không bị mất khi WavLM chưa đủ chắc chắn; khi đó dùng tên đăng
  nhập của mic với `identity_method=mic_fallback`.
- Bản provisional xuất trong khoảng 5–7 giây.
- LLM refinement cập nhật cùng `segment_id` với `revision=2`.
- Hallucination guard loại kết quả Qwen làm thay đổi quá nhiều nội dung gốc.
- Speaker profile vẫn tồn tại sau khi restart.

## 8. Chạy test backend

```bash
cd /mnt/d/VNPT/Code/Multi_Speaker_Streaming
venv_linux/bin/python -B -m unittest discover -s tests -p 'test_*.py'
```

Smoke tests kiểm tra:

- Health và metadata phòng.
- Tạo/join phòng và LiveKit token.
- Bảo vệ internal API.
- Lưu transcript vào SQLite.
- Cập nhật LLM revision trên cùng segment.
- Loại final trùng giữa hai mic và giữ nguồn mạnh hơn.

## 9. Dừng hệ thống an toàn

Thứ tự an toàn là:

```text
Người tham gia rời phòng
    → dừng pipeline WSL
    → dừng LiveKit/Nginx nếu bảo trì home server
    → tắt máy vật lý
```

### 9.1. Kết thúc một buổi demo thông thường

1. Yêu cầu các laptop rời phòng hoặc tắt microphone.
2. Chờ tối thiểu 20 giây để segment cuối và LLM revision đang chờ được ghi
   vào SQLite.
3. Có thể kiểm tra lần cuối:

```bash
curl http://127.0.0.1:8000/api/transcripts
```

4. Tại terminal WSL đang chạy `run_demo.sh`, nhấn:

```text
Ctrl+C
```

5. Chờ terminal trả lại dấu nhắc. Script sẽ gửi tín hiệu dừng cho:

   - Backend Uvicorn cổng `8000`.
   - AI server cổng `8001`.
   - LiveKit worker `agent.py`.

6. Xác nhận không còn tiến trình của demo:

```bash
pgrep -af 'run_demo.sh|backend.api.main|ai_server.py|agent.py'
ss -ltnp | grep -E ':(8000|8001)\b'
```

Hai lệnh trên không nên trả về process/cổng của pipeline. Nếu một process vẫn
còn, lấy đúng PID từ `pgrep`, gửi `kill -TERM <PID>` và chờ process kết thúc.
Chỉ dùng `kill -KILL` khi `TERM` không có tác dụng sau một khoảng chờ hợp lý.

Không cần dừng Nginx, LiveKit, Headscale hoặc Tailscale trên home server khi
chỉ kết thúc một buổi demo. Giữ các dịch vụ này chạy giúp lần demo sau khởi
động nhanh hơn.

### 9.2. Dừng hoàn toàn để bảo trì home server

Thực hiện mục 9.1 để dừng WSL trước. Sau đó SSH vào server:

```bash
ssh ntdserver
```

Dừng LiveKit bằng đúng Docker Compose project:

```bash
cd /opt/livekit
docker compose stop livekit
docker compose ps
```

`docker compose ps` phải cho thấy LiveKit đã dừng. Không dùng
`docker compose down -v`, vì tùy chọn `-v` có thể xóa volume/dữ liệu.

Nếu cần bảo trì Nginx:

```bash
sudo nginx -t
sudo systemctl stop nginx
```

Không dừng `headscale.service` hoặc `tailscaled.service` trong một phiên bảo
trì thông thường. Chúng đang cung cấp đường kết nối giữa home server và WSL;
dừng chúng có thể làm mất kết nối quản trị từ xa.

### 9.3. Tắt nguồn home server

Sau khi WSL, LiveKit và các tác vụ ghi dữ liệu đã dừng:

```bash
sudo shutdown -h now
```

Nếu bắt buộc phải dừng Headscale/Tailscale để bảo trì chính các dịch vụ này,
thực hiện chúng cuối cùng và chỉ khi còn đường truy cập LAN/console:

```bash
sudo systemctl stop headscale
sudo systemctl stop tailscaled
```

Không rút nguồn trực tiếp khi Docker, SQLite hoặc Qdrant đang ghi dữ liệu.

### 9.4. Tắt hẳn WSL trên Windows

Chỉ thực hiện sau khi `run_demo.sh` đã dừng và terminal WSL đã trả lại dấu
nhắc. Trong PowerShell:

```powershell
wsl.exe --shutdown
```

Lệnh này dừng tất cả distro WSL đang chạy trên máy, không chỉ distro của
project.

### 9.5. Khởi động lại sau bảo trì

Trên home server:

```bash
cd /opt/livekit
docker compose up -d livekit
sudo systemctl start nginx
docker compose ps
curl -I https://meet.simplething.id.vn
```

Headscale, Tailscale, Docker và Nginx thường tự khởi động cùng hệ điều hành,
nhưng vẫn nên kiểm tra trạng thái:

```bash
systemctl is-active docker nginx headscale tailscaled
```

Sau đó chạy lại pipeline trong WSL:

```bash
cd /mnt/d/VNPT/Code/Multi_Speaker_Streaming
bash scripts/run_demo.sh
```
