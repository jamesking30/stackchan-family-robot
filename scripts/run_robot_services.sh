#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TTS_PID=""
GPT_SOVITS_PID=""
WHISPER_PID=""
ROBOT_PID=""
MDNS_PID=""

cleanup() {
  [[ -n "$ROBOT_PID" ]] && kill "$ROBOT_PID" 2>/dev/null || true
  [[ -n "$TTS_PID" ]] && kill "$TTS_PID" 2>/dev/null || true
  [[ -n "$GPT_SOVITS_PID" ]] && kill "$GPT_SOVITS_PID" 2>/dev/null || true
  [[ -n "$WHISPER_PID" ]] && kill "$WHISPER_PID" 2>/dev/null || true
  [[ -n "$MDNS_PID" ]] && kill "$MDNS_PID" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

mkdir -p "$ROOT_DIR/var/logs" "$ROOT_DIR/var/health"

required_files=(
  "$ROOT_DIR/.env"
  "$ROOT_DIR/.venv/bin/stackchan-control"
  "$ROOT_DIR/var/models/ggml-small.bin"
  "$ROOT_DIR/config/seed_character/manifest.yaml"
)
for required_file in "${required_files[@]}"; do
  if [[ ! -s "$required_file" ]]; then
    echo "Required runtime file is missing: $required_file" >&2
    exit 1
  fi
done

if [[ ! -x /opt/homebrew/bin/whisper-server ]]; then
  echo "Whisper server is missing: /opt/homebrew/bin/whisper-server" >&2
  exit 1
fi

start_qwen() {
  if curl --fail --silent --max-time 1 http://127.0.0.1:8766/v1/models >/dev/null 2>&1; then
    TTS_PID=""
    return
  fi
  "$ROOT_DIR/scripts/run_local_tts.sh" &
  TTS_PID=$!
}

start_gpt_sovits() {
  if curl --fail --silent --max-time 1 http://127.0.0.1:9880/docs >/dev/null 2>&1; then
    GPT_SOVITS_PID=""
    return
  fi
  if [[ -x "$ROOT_DIR/var/gpt-sovits/venv/bin/python" ]]; then
    "$ROOT_DIR/scripts/run_gpt_sovits.sh" &
    GPT_SOVITS_PID=$!
    "$ROOT_DIR/scripts/warm_gpt_sovits.py" &
  fi
}

start_whisper() {
  if curl --fail --silent --max-time 1 http://127.0.0.1:8767/ >/dev/null 2>&1; then
    WHISPER_PID=""
    return
  fi
  /opt/homebrew/bin/whisper-server \
    -m "$ROOT_DIR/var/models/ggml-small.bin" \
    --host 127.0.0.1 \
    --port 8767 \
    -l auto \
    -t 6 &
  WHISPER_PID=$!
}

start_mdns() {
  local enabled
  enabled="$(sed -n 's/^ROBOT_MDNS_ENABLED=//p' "$ROOT_DIR/.env" | tail -1)"
  if [[ "${enabled:-true}" != "true" ]] || ! command -v dns-sd >/dev/null; then
    return
  fi
  local lan_ip
  lan_ip="$(ipconfig getifaddr en0 2>/dev/null || ipconfig getifaddr en1 2>/dev/null || true)"
  if [[ -z "$lan_ip" ]]; then
    return
  fi
  dns-sd -P StackChan-Family _stackchan._tcp local 8765 \
    stackchan-family.local "$lan_ip" \
    "path=/stackChan/ws" "device=${STACKCHAN_DEVICE_ID:-stackchan-home-01}" \
    >"$ROOT_DIR/var/logs/mdns.log" 2>&1 &
  MDNS_PID=$!
}

start_qwen
start_gpt_sovits
start_whisper
start_mdns

for _ in {1..40}; do
  if curl --fail --silent http://127.0.0.1:8766/v1/models >/dev/null 2>&1; then
    break
  fi
  sleep 0.25
done

for _ in {1..80}; do
  if curl --fail --silent --max-time 1 http://127.0.0.1:8767/ >/dev/null 2>&1; then
    break
  fi
  sleep 0.25
done

if ! curl --fail --silent http://127.0.0.1:8766/v1/models >/dev/null; then
  echo "Local TTS service did not become ready." >&2
  exit 1
fi

if ! curl --fail --silent http://127.0.0.1:8767/ >/dev/null; then
  echo "Persistent Whisper service did not become ready." >&2
  exit 1
fi

"$ROOT_DIR/.venv/bin/stackchan-control" &
ROBOT_PID=$!

while kill -0 "$ROBOT_PID" 2>/dev/null; do
  if [[ -n "$TTS_PID" ]] && ! kill -0 "$TTS_PID" 2>/dev/null; then
    start_qwen
  elif [[ -z "$TTS_PID" ]] && ! curl --fail --silent --max-time 1 \
    http://127.0.0.1:8766/v1/models >/dev/null 2>&1; then
    start_qwen
  fi
  if [[ -n "$GPT_SOVITS_PID" ]] && ! kill -0 "$GPT_SOVITS_PID" 2>/dev/null; then
    start_gpt_sovits
  elif [[ -z "$GPT_SOVITS_PID" ]] && ! curl --fail --silent --max-time 1 \
    http://127.0.0.1:9880/docs >/dev/null 2>&1; then
    start_gpt_sovits
  fi
  if [[ -n "$WHISPER_PID" ]] && ! kill -0 "$WHISPER_PID" 2>/dev/null; then
    start_whisper
  elif [[ -z "$WHISPER_PID" ]] && ! curl --fail --silent --max-time 1 \
    http://127.0.0.1:8767/ >/dev/null 2>&1; then
    start_whisper
  fi
  sleep 5
done

wait "$ROBOT_PID"
