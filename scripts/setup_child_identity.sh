#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MODEL_DIR="$ROOT_DIR/var/models/insightface"
MODEL_PATH="$MODEL_DIR/genderage.onnx"
ARCHIVE_PATH="$MODEL_DIR/buffalo_l.zip"
MODEL_URL="https://github.com/deepinsight/insightface/releases/download/v0.7/buffalo_l.zip"
EXPECTED_SHA256="4fde69b1c810857b88c64a335084f1c3fe8f01246c9a191b48c7bb756d6652fb"

mkdir -p "$MODEL_DIR"
if [[ ! -f "$MODEL_PATH" ]]; then
  curl --fail --location --retry 3 "$MODEL_URL" --output "$ARCHIVE_PATH"
  unzip -jo "$ARCHIVE_PATH" "genderage.onnx" -d "$MODEL_DIR"
  rm -f "$ARCHIVE_PATH"
fi

ACTUAL_SHA256="$(shasum -a 256 "$MODEL_PATH" | awk '{print $1}')"
if [[ "$ACTUAL_SHA256" != "$EXPECTED_SHA256" ]]; then
  echo "Age model checksum mismatch: $MODEL_PATH" >&2
  exit 1
fi

echo "Child identity age model ready: $MODEL_PATH"
