#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SOURCE_DIR="${1:-}"
RUNTIME="$ROOT_DIR/var/gpt-sovits"
UPSTREAM="$RUNTIME/GPT-SoVITS"
VENV="$RUNTIME/venv"
MODEL_DIR="$ROOT_DIR/var/models/gpt-sovits/elysia"
UPSTREAM_COMMIT="be6a4f1e9d8a22d41b7d42c22df9d7ef36f225d2"

if [[ -z "$SOURCE_DIR" || ! -d "$SOURCE_DIR" ]]; then
  echo "Usage: $0 /path/to/【GPT-SoVITS 2.0】爱莉希雅" >&2
  exit 2
fi

mkdir -p "$RUNTIME" "$MODEL_DIR"
if [[ ! -d "$UPSTREAM/.git" ]]; then
  git clone https://github.com/RVC-Boss/GPT-SoVITS.git "$UPSTREAM"
fi
git -C "$UPSTREAM" fetch --depth 1 origin "$UPSTREAM_COMMIT"
git -C "$UPSTREAM" checkout --detach "$UPSTREAM_COMMIT"

python3 -m venv "$VENV"
"$VENV/bin/python" -m pip install --upgrade pip setuptools wheel
"$VENV/bin/python" -m pip install 'torch==2.5.1' 'torchaudio==2.5.1'
"$VENV/bin/python" -m pip install -r "$UPSTREAM/extra-req.txt" --no-deps
"$VENV/bin/python" -m pip install -r "$UPSTREAM/requirements.txt"

BASE_URL="https://huggingface.co/XXXXRT/GPT-SoVITS-Pretrained/resolve/main"
download_asset() {
  local relative_path="$1"
  local destination="$UPSTREAM/GPT_SoVITS/$relative_path"
  [[ -s "$destination" ]] && return
  mkdir -p "$(dirname "$destination")"
  curl -fL --retry 5 --retry-delay 2 \
    "$BASE_URL/$relative_path" -o "$destination.part"
  mv "$destination.part" "$destination"
}

download_asset pretrained_models/chinese-hubert-base/config.json
download_asset pretrained_models/chinese-hubert-base/preprocessor_config.json
download_asset pretrained_models/chinese-hubert-base/pytorch_model.bin
download_asset pretrained_models/chinese-roberta-wwm-ext-large/config.json
download_asset pretrained_models/chinese-roberta-wwm-ext-large/pytorch_model.bin
download_asset pretrained_models/chinese-roberta-wwm-ext-large/tokenizer.json

if [[ ! -d "$UPSTREAM/GPT_SoVITS/text/G2PWModel" ]]; then
  curl -fL --retry 5 --retry-delay 2 "$BASE_URL/G2PWModel.zip" \
    -o "$UPSTREAM/G2PWModel.zip"
  unzip -q -o "$UPSTREAM/G2PWModel.zip" -d "$UPSTREAM/GPT_SoVITS/text"
  rm "$UPSTREAM/G2PWModel.zip"
fi

if [[ ! -d "$VENV/nltk_data" ]]; then
  curl -fL --retry 5 --retry-delay 2 "$BASE_URL/nltk_data.zip" \
    -o "$UPSTREAM/nltk_data.zip"
  unzip -q -o "$UPSTREAM/nltk_data.zip" -d "$VENV"
  rm "$UPSTREAM/nltk_data.zip"
fi

LANG_ID_DIR="$UPSTREAM/GPT_SoVITS/pretrained_models/fast_langdetect"
if [[ ! -s "$LANG_ID_DIR/lid.176.bin" ]]; then
  mkdir -p "$LANG_ID_DIR"
  curl -fL --retry 5 --retry-delay 2 \
    "https://dl.fbaipublicfiles.com/fasttext/supervised-models/lid.176.bin" \
    -o "$LANG_ID_DIR/lid.176.bin"
fi

cp -f "$SOURCE_DIR/GPT_weights_v2/【GPT2.0】Elysia-e20.ckpt" \
  "$MODEL_DIR/elysia-gpt-e20.ckpt"
cp -f "$SOURCE_DIR/SoVITS_weights_v2/【GPT2.0】Elysia_e24_s13080.pth" \
  "$MODEL_DIR/elysia-sovits-e24.pth"
cp -f "$SOURCE_DIR/参考音频/【开心】所以你今天就来见我了吗？哇，真令人开心呢。.wav" \
  "$MODEL_DIR/reference-happy.wav"

"$ROOT_DIR/scripts/generate_gpt_sovits_config.py" >/dev/null
echo "GPT-SoVITS and the Elysia voice model are ready."
