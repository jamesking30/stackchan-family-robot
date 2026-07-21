#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LABEL="com.stackchan.family-robot"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
LOG_DIR="$ROOT_DIR/var/logs"
UID_VALUE="$(id -u)"

mkdir -p "$HOME/Library/LaunchAgents" "$LOG_DIR"
python3 - "$PLIST" "$ROOT_DIR" "$LOG_DIR" <<'PY'
from pathlib import Path
from plistlib import dump
import sys

plist = Path(sys.argv[1])
root = Path(sys.argv[2])
logs = Path(sys.argv[3])
payload = {
    "Label": "com.stackchan.family-robot",
    "ProgramArguments": [
        "/usr/bin/caffeinate",
        "-s",
        str(root / "scripts/run_robot_services.sh"),
    ],
    "WorkingDirectory": str(root),
    "RunAtLoad": True,
    "KeepAlive": True,
    "ThrottleInterval": 10,
    "StandardOutPath": str(logs / "services.log"),
    "StandardErrorPath": str(logs / "services-error.log"),
    "ProcessType": "Interactive",
}
with plist.open("wb") as handle:
    dump(payload, handle)
PY

launchctl bootout "gui/$UID_VALUE/$LABEL" >/dev/null 2>&1 || true
launchctl bootstrap "gui/$UID_VALUE" "$PLIST"
launchctl enable "gui/$UID_VALUE/$LABEL"
launchctl kickstart -k "gui/$UID_VALUE/$LABEL"
echo "StackChan services now start automatically at login."
