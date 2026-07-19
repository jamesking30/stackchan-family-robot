#!/usr/bin/env bash

set -euo pipefail

EXPECTED_IDF_REVISION="735507283d5b2f9fb363a1901172dbd9e847945d"
IDF_ROOT="${STACKCHAN_IDF_ROOT:-$HOME/.espressif/frameworks/esp-idf-v5.5.4}"

if ! command -v brew >/dev/null 2>&1; then
  echo "Homebrew is required to install CMake and Ninja on this Mac." >&2
  exit 1
fi

missing_packages=()
command -v cmake >/dev/null 2>&1 || missing_packages+=(cmake)
command -v ninja >/dev/null 2>&1 || missing_packages+=(ninja)
if ((${#missing_packages[@]})); then
  HOMEBREW_NO_AUTO_UPDATE=1 brew install "${missing_packages[@]}"
fi

if [[ ! -d "$IDF_ROOT/.git" ]]; then
  mkdir -p "$(dirname "$IDF_ROOT")"
  git clone --branch v5.5.4 --recursive https://github.com/espressif/esp-idf.git "$IDF_ROOT"
fi

actual_revision="$(git -C "$IDF_ROOT" rev-parse HEAD)"
if [[ "$actual_revision" != "$EXPECTED_IDF_REVISION" ]]; then
  echo "ESP-IDF checkout mismatch: expected $EXPECTED_IDF_REVISION, got $actual_revision" >&2
  exit 1
fi

git -C "$IDF_ROOT" submodule update --init --recursive

if [[ -z "${SSL_CERT_FILE:-}" && -f /etc/ssl/cert.pem ]]; then
  export SSL_CERT_FILE=/etc/ssl/cert.pem
fi

"$IDF_ROOT/install.sh" esp32s3

echo "ESP-IDF 5.5.4 toolchain installed."
echo "Run ./scripts/build_firmware.sh to build the verified baseline."
