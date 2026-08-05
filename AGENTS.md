# Project instructions

## Runtime

- Source code nằm trên Windows tại `D:\VNPT\Code\Multi_Speaker_Streaming`.
- Chạy Python trong WSL bằng:
  `/home/ntd/meeting_runtime/venv_linux/bin/python`.
- Khi chạy từ WSL, thư mục project là:
  `/mnt/d/VNPT/Code/Multi_Speaker_Streaming`.
- Không tạo virtualenv hoặc tải model vào thư mục source code.
- Model, cache và runtime data phải nằm dưới:
  `/home/ntd/meeting_runtime`.

## Architecture boundaries

- eCabinet Core sở hữu lịch họp, thành viên, role và authentication.
- Meeting Service sở hữu runtime session, transcript, biên bản và export metadata.
- Meeting AI chỉ xử lý AI; không truy cập database hoặc MinIO của eCabinet.
- Không tạo foreign key hoặc query trực tiếp xuyên database/service.
- Không sửa các module document, task, conclusion, voting, Văn bản chỉ đạo
  hoặc integration/qlvb nếu không có yêu cầu rõ ràng.
- Việc tích hợp phải additive và hạn chế xâm lấn eCabinet.

## Baseline protection

- Baseline đã khóa trong `baseline/manifest.json`.
- Không thay thuật toán, tuning ASR hoặc ngưỡng speaker identification trong
  cùng commit với refactor kiến trúc.
- DPDFNet và GTCRN không active mặc định trong baseline đã khóa.
- Giữ `ai_server.py` và `agent.py` làm compatibility wrapper trong giai đoạn refactor.
- WER và CER không được kém baseline quá 1 điểm phần trăm tuyệt đối.

## Required verification

- Sau thay đổi Python, chạy toàn bộ unit test.
- Sau thay đổi audio, LiveKit, ASR hoặc Agent, chạy streaming regression.
- Sau thay đổi contract, chạy `tests/test_contracts.py`.
- Báo rõ test nào đã chạy, kết quả và test nào chưa thể chạy.
- Không đánh dấu hoàn thành nếu regression chưa đạt.

## Git and safety

- Không tự động commit hoặc push nếu người dùng chưa yêu cầu.
- Không dùng `git reset --hard`, xóa recursive hoặc ghi đè thay đổi của người dùng.
- Không sửa hoặc commit file `.env`, key, token, model cache và runtime output.
- Trước khi sửa, kiểm tra `git status` và bảo toàn thay đổi không liên quan.
- Dùng `apply_patch` để chỉnh sửa file thủ công.