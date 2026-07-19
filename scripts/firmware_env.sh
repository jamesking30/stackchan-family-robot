#!/usr/bin/env bash

# Source this file before using ESP-IDF directly. The build scripts source it
# automatically, so normal development only needs scripts/build_firmware.sh.

STACKCHAN_PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STACKCHAN_IDF_ROOT="${STACKCHAN_IDF_ROOT:-$HOME/.espressif/frameworks/esp-idf-v5.5.4}"

if [[ ! -f "$STACKCHAN_IDF_ROOT/export.sh" ]]; then
  echo "ESP-IDF 5.5.4 was not found at $STACKCHAN_IDF_ROOT" >&2
  echo "Install it there or set STACKCHAN_IDF_ROOT to the ESP-IDF checkout." >&2
  return 1 2>/dev/null || exit 1
fi

export IDF_TOOLS_PATH="${IDF_TOOLS_PATH:-$HOME/.espressif}"

# ESP-IDF's export script sets IDF_PATH, Python, compiler and tool paths.
# shellcheck disable=SC1090
if ! source "$STACKCHAN_IDF_ROOT/export.sh" >/dev/null; then
  echo "Failed to activate ESP-IDF from $STACKCHAN_IDF_ROOT" >&2
  return 1 2>/dev/null || exit 1
fi

if [[ "$(idf.py --version)" != "ESP-IDF v5.5.4" ]]; then
  echo "Expected ESP-IDF v5.5.4, got: $(idf.py --version)" >&2
  return 1 2>/dev/null || exit 1
fi

# Some macOS Python installations do not expose a system CA bundle. Reuse the
# certifi bundle already installed in ESP-IDF's isolated Python environment.
if [[ -z "${SSL_CERT_FILE:-}" ]]; then
  if ! STACKCHAN_CERT_FILE="$(python -c 'import certifi; print(certifi.where())')"; then
    echo "ESP-IDF Python environment does not provide a CA certificate bundle." >&2
    return 1 2>/dev/null || exit 1
  fi
  export SSL_CERT_FILE="$STACKCHAN_CERT_FILE"
fi

export STACKCHAN_PROJECT_ROOT
export STACKCHAN_IDF_ROOT
export STACKCHAN_FIRMWARE_DIR="$STACKCHAN_PROJECT_ROOT/firmware/upstream/stackchan/firmware"
export STACKCHAN_BUILD_DIR="${STACKCHAN_BUILD_DIR:-$STACKCHAN_PROJECT_ROOT/var/firmware-build/m5stack-stack-chan-b72b3ede}"
