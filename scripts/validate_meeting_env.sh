#!/usr/bin/env bash
# Validate the one private env file used by the integrated LAN stack.
# Never print secret values; only lengths and short hashes are reported.
set -euo pipefail

ENV_FILE="${1:-${MEETING_PLATFORM_ENV_FILE:-/home/ntd/meeting_runtime/meeting-platform.lan.env}}"

if [[ ! -r "$ENV_FILE" ]]; then
  echo "Missing private meeting env: $ENV_FILE" >&2
  exit 2
fi

value_for() {
  local name="$1"
  awk -F= -v key="$name" '
    $1 == key {
      sub(/^[^=]*=/, "")
      sub(/\r$/, "")
      gsub(/^[ \t]+|[ \t]+$/, "")
      if (length($0) >= 2 && ((substr($0, 1, 1) == "\"" && substr($0, length($0), 1) == "\"") || (substr($0, 1, 1) == "\x27" && substr($0, length($0), 1) == "\x27"))) {
        $0 = substr($0, 2, length($0) - 2)
      }
      print
      exit
    }
  ' "$ENV_FILE"
}

service_key="$(value_for MEETING_SERVICE_KEY)"
internal_key="$(value_for INTERNAL_API_KEY)"
runtime_secret="$(value_for MEETING_RUNTIME_TOKEN_SECRET)"

for pair in \
  "MEETING_SERVICE_KEY:$service_key" \
  "INTERNAL_API_KEY:$internal_key" \
  "MEETING_RUNTIME_TOKEN_SECRET:$runtime_secret"; do
  name="${pair%%:*}"
  value="${pair#*:}"
  if [[ -z "$value" ]]; then
    echo "Missing $name in $ENV_FILE" >&2
    exit 2
  fi
done

if [[ "$service_key" != "$internal_key" ]]; then
  echo "MEETING_SERVICE_KEY and INTERNAL_API_KEY must be identical" >&2
  exit 2
fi

if (( ${#service_key} < 24 )); then
  echo "Shared service key must contain at least 24 characters" >&2
  exit 2
fi
if (( ${#runtime_secret} < 32 )); then
  echo "MEETING_RUNTIME_TOKEN_SECRET must contain at least 32 characters" >&2
  exit 2
fi

short_hash() {
  printf %s "$1" | sha256sum | cut -c1-16
}

printf 'meeting env ok: service_key_len=%d service_key_sha=%s runtime_secret_len=%d runtime_secret_sha=%s\n' \
  "${#service_key}" "$(short_hash "$service_key")" \
  "${#runtime_secret}" "$(short_hash "$runtime_secret")"
