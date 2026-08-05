# Meeting Platform contracts

Contract version hiện tại: **1.0.0**. Các file trong thư mục này là ranh giới cố định cho phase refactor microservice đầu tiên.

## Ownership

| Producer | Consumer | Contract |
|---|---|---|
| eCabinet Core | Meeting Service | `openapi/meeting-service.openapi.yaml`, `schemas/meeting-snapshot.schema.json` |
| Meeting Service | Meeting AI Core | `openapi/meeting-ai.openapi.yaml`, `schemas/ai-session.schema.json` |
| Meeting AI Core | Meeting Service | `schemas/ai-event.schema.json` |
| Meeting AI Core/Meeting Service | Minutes UI/export | `schemas/minutes-document.schema.json` |

Quy tắc bắt buộc:

- `meeting_id`, `runtime_session_id` và user IDs là UUID external; không suy ra FK hoặc quyền truy cập database chéo service.
- Mọi request/event có `schema_version=1`.
- Unknown field bị từ chối ở các payload nghiệp vụ đã khóa.
- Thời gian xuyên service dùng ISO-8601 UTC; adapter compatibility chịu trách nhiệm đổi Unix timestamp của baseline.
- Meeting AI không đọc database. Analyze request phải mang evidence snapshot.
- eCabinet giữ public REST façade; Meeting Service chỉ public Socket.IO path qua Nginx.
- Thay đổi breaking phải tạo schema/OpenAPI version mới, không sửa âm thầm version 1.

`examples/` là fixtures executable. `tests/test_contracts.py` kiểm tra schema, OpenAPI path và negative cases mà không yêu cầu khởi động model.
