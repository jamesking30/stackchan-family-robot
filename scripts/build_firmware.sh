#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# shellcheck disable=SC1091
source "$SCRIPT_DIR/firmware_env.sh"
"$SCRIPT_DIR/prepare_firmware.sh"

mkdir -p "$STACKCHAN_BUILD_DIR"

cd "$STACKCHAN_FIRMWARE_DIR"
idf.py \
  -B "$STACKCHAN_BUILD_DIR" \
  -D "SDKCONFIG=$STACKCHAN_BUILD_DIR/sdkconfig" \
  build

python "$SCRIPT_DIR/verify_firmware_build.py"

echo "Firmware build completed: $STACKCHAN_BUILD_DIR"
echo "No device was flashed."
