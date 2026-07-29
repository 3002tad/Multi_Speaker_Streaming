#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUNTIME_DIR="${MEETING_RUNTIME_DIR:-/home/ntd/meeting_runtime}"
export MEETING_RUNTIME_DIR="$RUNTIME_DIR"
export HF_HOME="${HF_HOME:-$RUNTIME_DIR/huggingface}"
export TRANSFORMERS_CACHE="${TRANSFORMERS_CACHE:-$HF_HOME}"
PYTHON_BIN="$RUNTIME_DIR/venv_linux/bin/python"
SITE_PACKAGES="$RUNTIME_DIR/venv_linux/lib/python3.12/site-packages"
PYTHON_CACHE="/tmp/paperless-python-cache"
TRANSFORMERS_CACHE_VERSION_FILE="$PYTHON_CACHE/.transformers-version"

if [[ ! -x "$PYTHON_BIN" ]]; then
  echo "Không tìm thấy venv_linux tại: $PYTHON_BIN"
  exit 1
fi

if [[ ! -f "$PROJECT_DIR/.env" && ! -f "$RUNTIME_DIR/.env" ]]; then
  echo "Thiếu .env. Hãy sao chép .env.example thành .env và điền LiveKit key."
  exit 1
fi

cd "$PROJECT_DIR"

"$PYTHON_BIN" -c \
  "from backend.config import settings; settings.validate_runtime()"

# Importing Transformers directly from /mnt/d is very slow on WSL DrvFS.
# Keep using venv_linux, but mirror this pure-Python package onto Linux tmpfs.
# Rebuild when pip changes the installed Transformers version; otherwise a
# stale cache can be incompatible with Optimum/ONNX extensions.
TRANSFORMERS_VERSION="$("$PYTHON_BIN" -c \
  "import transformers; print(transformers.__version__)")"
if [[ ! -f "$PYTHON_CACHE/transformers/__init__.py" ]] \
  || [[ ! -f "$TRANSFORMERS_CACHE_VERSION_FILE" ]] \
  || [[ "$(<"$TRANSFORMERS_CACHE_VERSION_FILE")" != "$TRANSFORMERS_VERSION" ]]; then
  echo "Làm mới cache Transformers $TRANSFORMERS_VERSION trên filesystem Linux..."
  rm -rf -- "$PYTHON_CACHE/transformers"
  mkdir -p "$PYTHON_CACHE"
  tar -C "$SITE_PACKAGES" -cf /tmp/paperless-transformers.tar transformers
  tar -C "$PYTHON_CACHE" -xf /tmp/paperless-transformers.tar
  printf '%s\n' "$TRANSFORMERS_VERSION" > "$TRANSFORMERS_CACHE_VERSION_FILE"
fi
export PYTHONPATH="$PYTHON_CACHE${PYTHONPATH:+:$PYTHONPATH}"

echo "Warm-up mô hình refinement để loại bỏ độ trễ cold-start..."
OLLAMA_MODEL="$("$PYTHON_BIN" -c \
  "from backend.config import settings; print(settings.sailor_model if settings.refinement_backend == 'sailor_candidate' else settings.ollama_model)")"
if ! curl --silent --fail --max-time 120 \
  http://127.0.0.1:11434/api/chat \
  -H "Content-Type: application/json" \
  -d "{\"model\":\"$OLLAMA_MODEL\",\"messages\":[{\"role\":\"system\",\"content\":\"Sửa chính tả và dấu câu tiếng Việt cho transcript cuộc họp. Không tóm tắt, không thêm ý. Chỉ trả về văn bản đã sửa.\"},{\"role\":\"user\",\"content\":\"Văn bản gốc: hệ thống đang kiểm tra biên bản cuộc họp và độ trễ xử lý trong quá trình nhiều người cùng phát biểu tại phòng họp\"}],\"stream\":false,\"keep_alive\":\"30m\",\"options\":{\"temperature\":0,\"num_predict\":64,\"num_thread\":2}}" \
  >/dev/null; then
  echo "Ollama/refinement model không sẵn sàng; dừng để tránh biên bản không refinement."
  exit 1
fi

pids=()
process_names=()
cleanup_started=0

start_process() {
  local name="$1"
  shift
  "$@" &
  pids+=("$!")
  process_names+=("$name")
}

cleanup() {
  local exit_code="${1:-$?}"
  local alive=0

  if (( cleanup_started )); then
    return
  fi
  cleanup_started=1
  trap - EXIT INT TERM

  echo
  echo "[stop] Bắt đầu dừng hệ thống..."
  for index in "${!pids[@]}"; do
    pid="${pids[$index]}"
    name="${process_names[$index]}"
    if kill -0 "$pid" 2>/dev/null; then
      echo "[stop] Gửi tín hiệu dừng tới $name (PID $pid)..."
      kill -TERM "$pid" 2>/dev/null || true
    else
      echo "[stop] $name đã dừng."
    fi
  done

  # Give Python/LiveKit enough time to close sockets and flush final output.
  for _ in $(seq 1 75); do
    alive=0
    for pid in "${pids[@]}"; do
      if kill -0 "$pid" 2>/dev/null; then
        alive=1
        break
      fi
    done
    (( alive == 0 )) && break
    sleep 0.2
  done

  for index in "${!pids[@]}"; do
    pid="${pids[$index]}"
    name="${process_names[$index]}"
    if kill -0 "$pid" 2>/dev/null; then
      echo "[stop] $name quá thời hạn; buộc kết thúc (PID $pid)."
      kill -KILL "$pid" 2>/dev/null || true
    fi
  done

  for pid in "${pids[@]}"; do
    wait "$pid" 2>/dev/null || true
  done

  echo "[stop] Đã dừng toàn bộ backend. Terminal đã sẵn sàng."
  return "$exit_code"
}
on_signal() {
  cleanup 130
  exit 130
}
trap 'cleanup $?' EXIT
trap on_signal INT TERM

start_process "Web/API" \
  "$PYTHON_BIN" -m uvicorn backend.api.main:app \
  --host 0.0.0.0 --port 8000

start_process "AI pipeline" "$PYTHON_BIN" -u ai_server.py

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
  local agent_pid=""
  local stopping=0

  forward_stop() {
    stopping=1
    if [[ -n "$agent_pid" ]] && kill -0 "$agent_pid" 2>/dev/null; then
      kill -TERM "$agent_pid" 2>/dev/null || true
    fi
  }
  trap forward_stop INT TERM

  while (( ! stopping )); do
    "$PYTHON_BIN" -u agent.py &
    agent_pid="$!"
    set +e
    wait "$agent_pid"
    agent_status="$?"
    set -e
    agent_pid=""
    (( stopping )) && break
    echo "LiveKit worker đã dừng (mã $agent_status); thử kết nối lại sau 2 giây..."
    sleep 2
  done
}
start_process "LiveKit worker" run_agent

echo
echo "Demo đã chạy:"
echo "  Web/API : http://127.0.0.1:8000"
echo "  AI      : http://127.0.0.1:8001 (chỉ local)"
echo "  LiveKit : cấu hình theo LIVEKIT_URL"
echo "Nhấn Ctrl+C để dừng toàn bộ."

wait
