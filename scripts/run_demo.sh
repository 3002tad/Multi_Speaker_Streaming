#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="$PROJECT_DIR/venv_linux/bin/python"
SITE_PACKAGES="$PROJECT_DIR/venv_linux/lib/python3.12/site-packages"
PYTHON_CACHE="/tmp/paperless-python-cache"

if [[ ! -x "$PYTHON_BIN" ]]; then
  echo "Không tìm thấy venv_linux tại: $PYTHON_BIN"
  exit 1
fi

if [[ ! -f "$PROJECT_DIR/.env" ]]; then
  echo "Thiếu .env. Hãy sao chép .env.example thành .env và điền LiveKit key."
  exit 1
fi

cd "$PROJECT_DIR"

"$PYTHON_BIN" -c \
  "from backend.config import settings; settings.validate_runtime()"

# Importing Transformers directly from /mnt/d is very slow on WSL DrvFS.
# Keep using venv_linux, but mirror this pure-Python package onto Linux tmpfs.
if [[ ! -f "$PYTHON_CACHE/transformers/__init__.py" ]]; then
  echo "Tạo cache Transformers trên filesystem Linux (chỉ lần đầu)..."
  mkdir -p "$PYTHON_CACHE"
  tar -C "$SITE_PACKAGES" -cf /tmp/paperless-transformers.tar transformers
  tar -C "$PYTHON_CACHE" -xf /tmp/paperless-transformers.tar
fi
export PYTHONPATH="$PYTHON_CACHE${PYTHONPATH:+:$PYTHONPATH}"

echo "Warm-up Qwen để loại bỏ độ trễ cold-start..."
OLLAMA_MODEL="$("$PYTHON_BIN" -c \
  "from backend.config import settings; print(settings.ollama_model)")"
if ! curl --silent --fail --max-time 120 \
  http://127.0.0.1:11434/api/chat \
  -H "Content-Type: application/json" \
  -d "{\"model\":\"$OLLAMA_MODEL\",\"messages\":[{\"role\":\"system\",\"content\":\"Sửa lỗi chính tả tiếng Việt cuộc họp. Chuẩn hóa từ ASR nghe nhầm: 'lồng quét' -> 'làm web', 'aptoris' -> 'Architecture', 'hpase' -> 'HBase', 'hd' -> 'HD'. KHÔNG tóm tắt. CHỈ trả về đúng văn bản đã sửa.\"},{\"role\":\"user\",\"content\":\"Văn bản gốc: lồng quét này nọ làm hệ thống web lồng quét aptoris gồm hpase\"},{\"role\":\"assistant\",\"content\":\"Làm Web này nọ làm hệ thống web, làm web Architecture gồm HBase.\"},{\"role\":\"user\",\"content\":\"Văn bản gốc: hệ thống đang kiểm tra biên bản cuộc họp và độ trễ xử lý trong quá trình nhiều người cùng phát biểu tại phòng họp\"}],\"stream\":false,\"keep_alive\":\"30m\",\"options\":{\"temperature\":0,\"num_predict\":96,\"num_thread\":2}}" \
  >/dev/null; then
  echo "Ollama/Qwen không sẵn sàng; dừng để tránh biên bản không refinement."
  exit 1
fi

pids=()
cleanup() {
  for pid in "${pids[@]:-}"; do
    kill "$pid" 2>/dev/null || true
  done
}
on_signal() {
  cleanup
  exit 130
}
trap cleanup EXIT
trap on_signal INT TERM

"$PYTHON_BIN" -m uvicorn backend.api.main:app \
  --host 0.0.0.0 --port 8000 &
pids+=("$!")

"$PYTHON_BIN" -u ai_server.py &
pids+=("$!")

echo "Đang chờ AI pipeline nạp model..."
for _ in $(seq 1 300); do
  if curl --silent --fail http://127.0.0.1:8001/ >/dev/null; then
    break
  fi
  sleep 1
done

if ! curl --silent --fail http://127.0.0.1:8001/ >/dev/null; then
  echo "AI pipeline không sẵn sàng sau 300 giây."
  exit 1
fi

run_agent() {
  while true; do
    "$PYTHON_BIN" -u agent.py
    echo "LiveKit worker đã dừng; thử kết nối lại sau 2 giây..."
    sleep 2
  done
}
run_agent &
pids+=("$!")

echo
echo "Demo đã chạy:"
echo "  Web/API : http://127.0.0.1:8000"
echo "  AI      : http://127.0.0.1:8001 (chỉ local)"
echo "  LiveKit : cấu hình theo LIVEKIT_URL"
echo "Nhấn Ctrl+C để dừng toàn bộ."

wait
