#!/usr/bin/env bash
# One repeatable local E2E profile while AI Core/Agent are native WSL processes.
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUNTIME_DIR="${MEETING_RUNTIME_DIR:-/home/ntd/meeting_runtime}"
BASE_ENV="${MEETING_E2E_BASE_ENV:-$RUNTIME_DIR/.env}"
PYTHON_BIN="$RUNTIME_DIR/venv_linux/bin/python"
COMPOSE=(docker compose -f "$PROJECT_DIR/meeting_service/docker-compose.yml" -f "$PROJECT_DIR/meeting_service/docker-compose.e2e.yml")
AUDIO_FILE="$PROJECT_DIR/audio/thayDung_noi.wav"
KEEP_RUNNING=0
RUN_DEMO_PID=""

usage() {
  cat <<'EOF'
Usage: bash scripts/run_e2e_streaming.sh [--audio PATH] [--keep]

Reads one private WSL env file (MEETING_E2E_BASE_ENV or
/home/ntd/meeting_runtime/.env), starts the native AI/Agent plus the Meeting
Service stack, streams one 16 kHz WAV through LiveKit, validates transcript and
Qwen minutes, then stops only processes created by this run.
EOF
}

while (($#)); do
  case "$1" in
    --audio) AUDIO_FILE="$2"; shift 2 ;;
    --keep) KEEP_RUNNING=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage >&2; exit 2 ;;
  esac
done

[[ -f "$BASE_ENV" ]] || { echo "Missing private E2E env: $BASE_ENV" >&2; exit 2; }
[[ -x "$PYTHON_BIN" ]] || { echo "Missing WSL runtime Python: $PYTHON_BIN" >&2; exit 2; }
[[ -f "$AUDIO_FILE" ]] || { echo "Missing audio fixture: $AUDIO_FILE" >&2; exit 2; }

# This is the single secret source. The file is user-owned and ignored by Git.
set -a
# shellcheck disable=SC1090
source "$BASE_ENV"
set +a

required=(LIVEKIT_URL LIVEKIT_API_KEY LIVEKIT_API_SECRET INTERNAL_API_KEY OLLAMA_URL)
for name in "${required[@]}"; do
  [[ -n "${!name:-}" ]] || { echo "Missing $name in $BASE_ENV" >&2; exit 2; }
done
(( ${#INTERNAL_API_KEY} >= 24 )) || { echo "INTERNAL_API_KEY is too short" >&2; exit 2; }

# Make all native and Docker internal callers use exactly the same service key.
export MEETING_SERVICE_KEY="$INTERNAL_API_KEY"
export MEETING_AI_ENABLED=true
export MEETING_AI_BASE_URL=http://host.docker.internal:8001
export MEETING_AI_CALLBACK_URL=http://127.0.0.1:8002/internal/v1/ai-events
export MEETING_RUNTIME_DIR="$RUNTIME_DIR"

if [[ -n "$("${COMPOSE[@]}" ps -q 2>/dev/null)" ]]; then
  echo "Meeting Service stack is already running; stop it or inspect it manually before this isolated E2E run." >&2
  exit 2
fi

cleanup() {
  status="$?"
  trap - EXIT INT TERM
  if [[ -n "$RUN_DEMO_PID" ]] && kill -0 "$RUN_DEMO_PID" 2>/dev/null; then
    kill -TERM "$RUN_DEMO_PID" 2>/dev/null || true
    wait "$RUN_DEMO_PID" 2>/dev/null || true
  fi
  if (( ! KEEP_RUNNING )); then
    "${COMPOSE[@]}" down --remove-orphans >/dev/null || true
  fi
  exit "$status"
}
trap cleanup EXIT INT TERM

wait_http() {
  local url="$1"
  local label="$2"
  for _ in $(seq 1 180); do
    if curl --silent --fail --max-time 3 "$url" >/dev/null; then
      return 0
    fi
    sleep 1
  done
  echo "Timed out waiting for $label: $url" >&2
  return 1
}

mkdir -p "$RUNTIME_DIR/logs"
LOG_FILE="$RUNTIME_DIR/logs/e2e-streaming-$(date +%Y%m%d-%H%M%S).log"

cd "$PROJECT_DIR"
echo "[e2e] Starting native API, AI Core and Agent..."
bash scripts/run_demo.sh >"$LOG_FILE" 2>&1 &
RUN_DEMO_PID="$!"
wait_http http://127.0.0.1:8000/api/health "baseline API"
wait_http http://127.0.0.1:8001/health/ready "Meeting AI"

echo "[e2e] Starting Meeting Service Docker stack..."
"${COMPOSE[@]}" up -d --build
wait_http http://127.0.0.1:8002/health/ready "Meeting Service"

echo "[e2e] Streaming fixture: $AUDIO_FILE"
"$PYTHON_BIN" -u tests/livekit_e2e_probe.py --audio "$AUDIO_FILE"
echo "[e2e] PASS (native log: $LOG_FILE)"
