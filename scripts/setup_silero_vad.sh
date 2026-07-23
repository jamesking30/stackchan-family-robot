#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TARGET="$ROOT_DIR/var/models/silero_vad.onnx"
URL="https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/silero_vad.onnx"
EXPECTED_SHA256="9e2449e1087496d8d4caba907f23e0bd3f78d91fa552479bb9c23ac09cbb1fd6"

mkdir -p "$(dirname "$TARGET")"
curl --fail --location --retry 3 "$URL" --output "$TARGET.tmp"
ACTUAL_SHA256="$(shasum -a 256 "$TARGET.tmp" | awk '{print $1}')"
if [[ "$ACTUAL_SHA256" != "$EXPECTED_SHA256" ]]; then
  rm -f "$TARGET.tmp"
  echo "Silero VAD checksum mismatch." >&2
  exit 1
fi
mv "$TARGET.tmp" "$TARGET"
echo "Silero VAD is ready: $TARGET"
