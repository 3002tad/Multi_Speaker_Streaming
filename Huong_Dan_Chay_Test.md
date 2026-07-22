# 🚀 HƯỚNG DẪN CHẠY TEST HỆ THỐNG NHẬN DIỆN GIỌNG NÓI ĐA LUỒNG (LIVEKIT + AI)

Tài liệu này hướng dẫn cách vận hành hệ thống AI phân biệt giọng nói song song trên môi trường thuần Linux (Ubuntu WSL2).

---

## 1. Yêu Cầu & Cấu Trúc Hệ Thống
Đảm bảo bạn đang đứng ở thư mục gốc của dự án trong WSL2:
```bash
cd /mnt/d/VNPT/Code/Multi_Speaker_Streaming
```
**Cấu trúc thư mục bắt buộc:**
- `agent.py`: Chứa logic AI cốt lõi (LiveKit Agent, VAD, ASR Zipformer, WavLM, LLM Qwen).
- `test_client.py`: Mô phỏng 2 Micro đẩy âm thanh vào phòng họp.
- `audio/`: Chứa 4 file `.wav` (`_goc.wav` để ghi danh, `_noi.wav` để test).
- `Zipformer-30M-RNNT-Streaming-6000h/`: Thư mục chứa mô hình ASR.
- `venv_linux/`: Thư mục môi trường ảo Python.

---

## 2. Quy Trình Vận Hành (Kịch Bản 3 Tab Terminal)

Để theo dõi hệ thống mượt mà nhất, hãy chia màn hình của bạn ra làm 3 cửa sổ (Tab) Terminal khác nhau:

### 🖥️ TAB 1: Các Dịch Vụ Nền (Hạ tầng mạng & LLM)
Mở Terminal 1 và chạy 2 lệnh sau để khởi động máy chủ WebRTC và máy chủ LLM:
```bash
# 1. Bật LiveKit Server (Thêm dấu & để nó tự chạy ngầm)
livekit-server --dev > livekit.log 2>&1 &

# 2. Bật LLM Qwen 1.5B
ollama run qwen2.5:1.5b
```

### 🧠 TAB 2: Khởi Động Trái Tim AI (Backend Agent)
Mở Terminal 2, kích hoạt môi trường ảo và chạy Agent:
```bash
# Kích hoạt môi trường Python
source venv_linux/bin/activate

# Khởi động Agent ở chế độ Hot-Reload
python agent.py dev
```
👉 *Chờ khoảng vài giây để AI nạp các mô hình vào RAM. Khi thấy thông báo: **`[🚀 Agent] Đã kết nối vào phòng họp thành công và đang trực chiến!`** thì chuyển sang bước tiếp theo.*

### 🎙️ TAB 3: Bơm Dữ Liệu (Mô Phỏng Cuộc Họp)
Mở Terminal 3, kích hoạt môi trường ảo và đẩy Audio vào phòng:
```bash
# Kích hoạt môi trường Python
source venv_linux/bin/activate

# Kích hoạt 2 Micro đẩy âm thanh (Mic B sẽ phát trễ hơn 2 giây để test cãi nhau)
python test_client.py
```

---

## 3. Quan Sát Chéo (Cách Nghiệm Thu Kết Quả)

Ngay khi bạn gõ lệnh ở **TAB 3**, hãy nhanh mắt chuyển góc nhìn sang **TAB 2**. Bạn sẽ thấy sự vi diệu của kiến trúc đa luồng:

1. **Hoạt động của VAD:** Bạn sẽ thấy Log `[🎙️ VAD] Mic_A BẮT ĐẦU NÓI...`
2. **Dịch theo thời gian thực:** ASR Zipformer sẽ liên tục nổ các dòng chữ `[Nháp] ...` lên màn hình.
3. **Phân luồng độc lập:** Đúng 2 giây sau, `Mic_B` sẽ nhảy vào. Hệ thống sẽ báo `[+] Đã cấp phát luồng VAD & ASR riêng cho: Mic_B`. Lúc này 2 luồng nháp sẽ nhảy chữ đan xen nhau mà không hề giẫm chân lên nhau.
4. **Nhận diện giọng nói:** Khi ngắt câu, Qdrant sẽ đối chiếu âm thanh với file `_goc` để đoán ra "Thầy Dũng" hay "Thầy Phước".
5. **Đóng gói JSON Biên bản:** Kết quả cuối cùng được Qwen "gọt dũa" chính tả và in ra dưới dạng một gói dữ liệu `[📦 BIÊN BẢN]` kèm theo nhãn thời gian `start_time` và `end_time` cực kỳ chuẩn xác.

*(Giao diện Web Frontend sau này chỉ việc dựa vào cái `start_time` đó để sắp xếp thứ tự câu nói là xong!)*
