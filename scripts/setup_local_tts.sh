#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_DIR="$ROOT_DIR/var/tts-venv"

python3 -m venv "$VENV_DIR"
"$VENV_DIR/bin/python" -m pip install --upgrade pip
"$VENV_DIR/bin/python" -m pip install \
  'mlx-audio==0.4.5' \
  'uvicorn>=0.51,<1' \
  'fastapi>=0.139,<1' \
  'webrtcvad==2.0.10' \
  'python-multipart>=0.0.22,<1'

echo "Local MLX TTS environment is ready: $VENV_DIR"
