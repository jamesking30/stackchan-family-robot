#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MODEL_NAME="sherpa-onnx-kws-zipformer-zh-en-3M-2025-12-20"
MODEL_ROOT="$ROOT_DIR/var/models/sherpa"
MODEL_DIR="$MODEL_ROOT/$MODEL_NAME"
ARCHIVE="$MODEL_ROOT/$MODEL_NAME.tar.bz2"
URL="https://github.com/k2-fsa/sherpa-onnx/releases/download/kws-models/$MODEL_NAME.tar.bz2"
EXPECTED_SHA256="68447f4fbc67e70eee3a93961f36e81e98f47aef73ce7e7ca00885c6cd3616a6"

mkdir -p "$MODEL_ROOT"
if [[ ! -f "$MODEL_DIR/encoder-epoch-13-avg-2-chunk-8-left-64.int8.onnx" ]]; then
  curl --fail --location --retry 3 --output "$ARCHIVE" "$URL"
  printf '%s  %s\n' "$EXPECTED_SHA256" "$ARCHIVE" | shasum -a 256 --check
  tar -xjf "$ARCHIVE" -C "$MODEL_ROOT"
fi

"$ROOT_DIR/.venv/bin/sherpa-onnx-cli" text2token \
  --tokens "$MODEL_DIR/tokens.txt" \
  --tokens-type phone+ppinyin \
  --lexicon "$MODEL_DIR/en.phone" \
  "$ROOT_DIR/config/wake_keywords_raw.txt" \
  "$MODEL_DIR/keywords.txt"

echo "Wake-word model ready: $MODEL_DIR"
