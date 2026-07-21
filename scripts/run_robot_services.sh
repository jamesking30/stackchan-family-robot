#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TTS_PID=""
GPT_SOVITS_PID=""
ROBOT_PID=""

cleanup() {
  [[ -n "$ROBOT_PID" ]] && kill "$ROBOT_PID" 2>/dev/null || true
  [[ -n "$TTS_PID" ]] && kill "$TTS_PID" 2>/dev/null || true
  [[ -n "$GPT_SOVITS_PID" ]] && kill "$GPT_SOVITS_PID" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

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

start_qwen
start_gpt_sovits

for _ in {1..40}; do
  if curl --fail --silent http://127.0.0.1:8766/v1/models >/dev/null 2>&1; then
    break
  fi
  sleep 0.25
done

if ! curl --fail --silent http://127.0.0.1:8766/v1/models >/dev/null; then
  echo "Local TTS service did not become ready." >&2
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
  sleep 5
done

wait "$ROBOT_PID"
