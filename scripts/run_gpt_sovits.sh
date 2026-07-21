#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
UPSTREAM="$ROOT_DIR/var/gpt-sovits/GPT-SoVITS"
PYTHON="$ROOT_DIR/var/gpt-sovits/venv/bin/python"
CONFIG="$ROOT_DIR/var/gpt-sovits/elysia-v2.yaml"

if [[ ! -x "$PYTHON" || ! -f "$UPSTREAM/api_v2.py" ]]; then
  echo "GPT-SoVITS is not installed. Run setup_gpt_sovits.sh first." >&2
  exit 1
fi

"$ROOT_DIR/scripts/generate_gpt_sovits_config.py" >/dev/null
cd "$UPSTREAM"
exec "$PYTHON" api_v2.py -a 127.0.0.1 -p 9880 -c "$CONFIG"
