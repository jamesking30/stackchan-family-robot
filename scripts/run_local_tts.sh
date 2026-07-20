#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON="$ROOT_DIR/var/tts-venv/bin/python"

if [[ ! -x "$PYTHON" ]]; then
  echo "Local TTS is not installed. Run ./scripts/setup_local_tts.sh first." >&2
  exit 1
fi

exec "$PYTHON" -m mlx_audio.server --host 127.0.0.1 --port 8766
