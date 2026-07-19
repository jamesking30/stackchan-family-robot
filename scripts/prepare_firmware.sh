#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# shellcheck disable=SC1091
source "$SCRIPT_DIR/firmware_env.sh"

git -C "$PROJECT_ROOT" submodule update --init --recursive firmware/upstream/stackchan

if python "$SCRIPT_DIR/verify_firmware_sources.py"; then
  echo "Locked firmware dependencies are already present."
else
  echo "Fetching locked firmware dependencies..."
  python "$STACKCHAN_FIRMWARE_DIR/fetch_repos.py"
  python "$SCRIPT_DIR/verify_firmware_sources.py"
fi

echo "Firmware dependencies are ready."
