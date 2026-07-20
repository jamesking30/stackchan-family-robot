#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TTS_PID=""
ROBOT_PID=""

cleanup() {
  [[ -n "$ROBOT_PID" ]] && kill "$ROBOT_PID" 2>/dev/null || true
  [[ -n "$TTS_PID" ]] && kill "$TTS_PID" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

"$ROOT_DIR/scripts/run_local_tts.sh" &
TTS_PID=$!

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
wait "$ROBOT_PID"
