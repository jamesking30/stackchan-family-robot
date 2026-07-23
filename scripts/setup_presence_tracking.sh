#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MODEL_DIR="$ROOT_DIR/var/models/mediapipe"
MODEL_PATH="$MODEL_DIR/blaze_face_short_range.tflite"
MODEL_URL="https://storage.googleapis.com/mediapipe-models/face_detector/blaze_face_short_range/float16/latest/blaze_face_short_range.tflite"
EXPECTED_SHA256="b4578f35940bf5a1a655214a1cce5cab13eba73c1297cd78e1a04c2380b0152f"

mkdir -p "$MODEL_DIR"
if [[ ! -f "$MODEL_PATH" ]]; then
  curl --fail --location --retry 3 "$MODEL_URL" --output "$MODEL_PATH"
fi

ACTUAL_SHA256="$(shasum -a 256 "$MODEL_PATH" | awk '{print $1}')"
if [[ "$ACTUAL_SHA256" != "$EXPECTED_SHA256" ]]; then
  echo "Face detector checksum mismatch: $MODEL_PATH" >&2
  exit 1
fi

echo "Presence face detector ready: $MODEL_PATH"
