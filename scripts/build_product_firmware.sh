#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# shellcheck disable=SC1091
source "$SCRIPT_DIR/firmware_env.sh"
"$SCRIPT_DIR/prepare_firmware.sh"

CONFIG_DIR="${STACKCHAN_FIRMWARE_CONFIG_DIR:-$PROJECT_ROOT/var/firmware-config}"
PRODUCT_BUILD_DIR="${STACKCHAN_PRODUCT_BUILD_DIR:-$PROJECT_ROOT/var/firmware-build/product-m5stack-stack-chan-b72b3ede}"
export STACKCHAN_GENERATED_CONFIG_DIR="$CONFIG_DIR"

python "$SCRIPT_DIR/generate_firmware_config.py" --output-dir "$CONFIG_DIR" "$@"
mkdir -p "$PRODUCT_BUILD_DIR"
rm -f "$PRODUCT_BUILD_DIR/sdkconfig"

cd "$STACKCHAN_FIRMWARE_DIR"
idf.py \
  -B "$PRODUCT_BUILD_DIR" \
  -D "SDKCONFIG=$PRODUCT_BUILD_DIR/sdkconfig" \
  -D "SDKCONFIG_DEFAULTS=$STACKCHAN_FIRMWARE_DIR/sdkconfig.defaults;$CONFIG_DIR/sdkconfig.defaults.local" \
  -D "EXTRA_COMPONENT_DIRS=$PROJECT_ROOT/firmware/product_overlay" \
  build

cp "$CONFIG_DIR/product-config.json" "$PRODUCT_BUILD_DIR/product-config.json"
STACKCHAN_BUILD_DIR="$PRODUCT_BUILD_DIR" python "$SCRIPT_DIR/verify_firmware_build.py"

echo "Product firmware build completed: $PRODUCT_BUILD_DIR"
echo "No device was flashed."
