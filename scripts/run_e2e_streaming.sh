#!/usr/bin/env bash
# One repeatable local E2E profile with Meeting AI and Agent in Compose.
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUNTIME_DIR="${MEETING_RUNTIME_DIR:-/home/ntd/meeting_runtime}"
BASE_ENV="${MEETING_E2E_BASE_ENV:-$RUNTIME_DIR/.env}"
PYTHON_BIN="$RUNTIME_DIR/venv_linux/bin/python"
COMPOSE=(docker compose -f "$PROJECT_DIR/meeting_service/docker-compose.yml" -f "$PROJECT_DIR/deploy/compose.meeting-platform.yml")
AUDIO_FILE="$PROJECT_DIR/audio/thayDung_noi.wav"
KEEP_RUNNING=0

usage() {
  cat <<'EOF'
Usage: bash scripts/run_e2e_streaming.sh [--audio PATH] [--keep]

Reads one private WSL env file (MEETING_E2E_BASE_ENV or
/home/ntd/meeting_runtime/.env), starts the containerized Meeting AI/Agent and
Meeting Service stack, streams one 16 kHz WAV through LiveKit, validates
transcript and Qwen minutes, then stops only containers created by this run.
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

required=(LIVEKIT_URL LIVEKIT_API_KEY LIVEKIT_API_SECRET INTERNAL_API_KEY OLLAMA_URL MEETING_RUNTIME_TOKEN_SECRET)
for name in "${required[@]}"; do
  [[ -n "${!name:-}" ]] || { echo "Missing $name in $BASE_ENV" >&2; exit 2; }
done
(( ${#INTERNAL_API_KEY} >= 24 )) || { echo "INTERNAL_API_KEY is too short" >&2; exit 2; }
(( ${#MEETING_RUNTIME_TOKEN_SECRET} >= 32 )) || { echo "MEETING_RUNTIME_TOKEN_SECRET is too short" >&2; exit 2; }

# Make all native and Docker internal callers use exactly the same service key.
export MEETING_SERVICE_KEY="$INTERNAL_API_KEY"
export MEETING_AI_ENABLED=true
export MEETING_AI_BASE_URL=http://meeting-ai-api:8001
export MEETING_AI_CALLBACK_URL=http://meeting-service:8002/internal/v1/ai-events
export MEETING_RUNTIME_DIR="$RUNTIME_DIR"
export MEETING_RUNTIME_HOST_PATH="${MEETING_RUNTIME_HOST_PATH:-$RUNTIME_DIR}"
export MEETING_PLATFORM_ENV_FILE="$BASE_ENV"
export MEETING_SERVICE_ENV_FILE="${MEETING_SERVICE_ENV_FILE:-$BASE_ENV}"
export MEETING_AI_ENV_FILE="${MEETING_AI_ENV_FILE:-$BASE_ENV}"

if [[ -n "$("${COMPOSE[@]}" ps -q 2>/dev/null)" ]]; then
  echo "Meeting Service stack is already running; stop it or inspect it manually before this isolated E2E run." >&2
  exit 2
fi

cleanup() {
  status="$?"
  trap - EXIT INT TERM
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

wait_container_http() {
  local service="$1"
  local url="$2"
  local label="$3"
  for _ in $(seq 1 180); do
    if "${COMPOSE[@]}" exec -T "$service" python -c \
      "from urllib.request import urlopen; urlopen('$url', timeout=3).read()" \
      >/dev/null 2>&1; then
      return 0
    fi
    sleep 1
  done
  echo "Timed out waiting for $label: $url" >&2
  return 1
}

ensure_ollama_model() {
  local model="${MINUTES_COMPOSER_MODEL:-qwen2.5:3b}"
  if "${COMPOSE[@]}" exec -T ollama ollama show "$model" >/dev/null 2>&1; then
    return 0
  fi
  echo "Missing Ollama model '$model' in the container runtime." >&2
  echo "Run: ${COMPOSE[*]} exec ollama ollama pull $model" >&2
  return 2
}

cd "$PROJECT_DIR"
echo "[e2e] Starting containerized Meeting AI, Agent and Meeting Service..."
"${COMPOSE[@]}" up -d --build
wait_http http://127.0.0.1:8002/health/ready "Meeting Service"
wait_container_http meeting-ai-api http://127.0.0.1:8001/health/ready "Meeting AI"
ensure_ollama_model

echo "[e2e] Streaming fixture: $AUDIO_FILE"
"$PYTHON_BIN" -u tests/livekit_e2e_probe.py --audio "$AUDIO_FILE"
echo "[e2e] PASS (containerized runtime)"
