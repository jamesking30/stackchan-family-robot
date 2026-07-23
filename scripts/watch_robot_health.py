#!/usr/bin/env python3
"""Restart the robot service after repeated functional health failures."""

from __future__ import annotations

import json
import subprocess
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
STATE_PATH = ROOT / "var/health/watchdog-state.json"
SERVICE = "gui/{uid}/com.stackchan.family-robot"
FAILURE_LIMIT = 3


def fetch_json(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=5) as response:
        return json.loads(response.read().decode("utf-8"))


def load_state() -> dict:
    if not STATE_PATH.is_file():
        return {"consecutive_failures": 0, "restart_count": 0}
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"consecutive_failures": 0, "restart_count": 0}


def save_state(state: dict) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(
        json.dumps(state, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def main() -> int:
    state = load_state()
    problems: list[str] = []
    try:
        health = fetch_json("http://127.0.0.1:8765/health")
        if not health.get("deployment_path_valid"):
            problems.append("deployment path or runtime assets are missing")
        if not health.get("voice_configured"):
            problems.append("voice dependencies are not configured")
    except (OSError, ValueError, urllib.error.URLError) as exc:
        problems.append(f"control service unavailable: {exc}")

    for name, url in (
        ("whisper", "http://127.0.0.1:8767/health"),
        ("gpt-sovits", "http://127.0.0.1:9880/docs"),
    ):
        try:
            with urllib.request.urlopen(url, timeout=5) as response:
                if response.status != 200:
                    problems.append(f"{name} returned HTTP {response.status}")
        except (OSError, urllib.error.URLError) as exc:
            problems.append(f"{name} unavailable: {exc}")

    state["checked_at"] = datetime.now(timezone.utc).isoformat()
    state["problems"] = problems
    if not problems:
        state["consecutive_failures"] = 0
        save_state(state)
        return 0

    state["consecutive_failures"] = int(state.get("consecutive_failures", 0)) + 1
    if state["consecutive_failures"] >= FAILURE_LIMIT:
        target = SERVICE.format(uid=subprocess.check_output(["id", "-u"], text=True).strip())
        result = subprocess.run(
            ["launchctl", "kickstart", "-k", target],
            check=False,
            capture_output=True,
            text=True,
        )
        state["last_restart_at"] = datetime.now(timezone.utc).isoformat()
        state["last_restart_exit_code"] = result.returncode
        state["restart_count"] = int(state.get("restart_count", 0)) + 1
        state["consecutive_failures"] = 0
    save_state(state)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
