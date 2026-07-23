#!/usr/bin/env python3
"""Check host services, firmware gateway address and robot connection state."""

from __future__ import annotations

import json
import subprocess
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse


ROOT = Path(__file__).resolve().parents[1]


def env_value(name: str) -> str:
    path = ROOT / ".env"
    if not path.is_file():
        return ""
    for line in path.read_text(encoding="utf-8").splitlines():
        key, separator, value = line.partition("=")
        if separator and key.strip() == name:
            return value.strip().strip('"').strip("'")
    return ""


def request_json(path: str, *, admin: bool = False) -> dict:
    headers = {}
    if admin and (key := env_value("ROBOT_ADMIN_API_KEY")):
        headers["X-Robot-Admin-Key"] = key
    request = urllib.request.Request(
        f"http://127.0.0.1:8765{path}", headers=headers
    )
    with urllib.request.urlopen(request, timeout=3) as response:
        return json.loads(response.read().decode("utf-8"))


def current_lan_ip() -> str:
    for interface in ("en0", "en1"):
        result = subprocess.run(
            ["ipconfig", "getifaddr", interface],
            check=False,
            capture_output=True,
            text=True,
        )
        if result.stdout.strip():
            return result.stdout.strip()
    return ""


def configured_gateway_host() -> str:
    path = ROOT / "var/firmware-config/product-config.json"
    if not path.is_file():
        return ""
    value = json.loads(path.read_text(encoding="utf-8")).get("gateway_url", "")
    return urlparse(str(value)).hostname or ""


def main() -> int:
    result: dict[str, object] = {
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "host_ip": current_lan_ip(),
        "firmware_gateway_host": configured_gateway_host(),
        "control_service": False,
        "gpt_sovits_service": False,
        "qwen_fallback_service": False,
        "robot_online": False,
        "voice_ready": False,
        "wake_word_ready": False,
        "voice_mode": "unknown",
        "deployment_path_valid": False,
        "missing_paths": [],
        "problems": [],
    }
    problems = result["problems"]
    assert isinstance(problems, list)

    try:
        health = request_json("/health")
        result["control_service"] = bool(health.get("ok"))
        result["voice_ready"] = bool(health.get("voice_ready"))
        result["wake_word_ready"] = bool(health.get("wake_word_ready"))
        result["deployment_path_valid"] = bool(
            health.get("deployment_path_valid")
        )
        result["missing_paths"] = health.get("missing_paths", [])
        state = request_json("/v1/device/state", admin=True)
        result["robot_online"] = bool(state.get("online"))
        voice_state = request_json("/v1/voice/state", admin=True)
        result["voice_mode"] = str(voice_state.get("mode", "unknown"))
    except (OSError, ValueError, urllib.error.URLError) as exc:
        problems.append(f"control service unavailable: {exc}")

    for name, url in (
        ("gpt_sovits_service", "http://127.0.0.1:9880/docs"),
        ("qwen_fallback_service", "http://127.0.0.1:8766/v1/models"),
    ):
        try:
            with urllib.request.urlopen(url, timeout=3) as response:
                result[name] = response.status == 200
        except (OSError, urllib.error.URLError):
            pass

    if result["firmware_gateway_host"] and (
        result["firmware_gateway_host"] != result["host_ip"]
    ):
        problems.append(
            "Mac LAN address differs from the address embedded in the firmware"
        )
    if not result["robot_online"]:
        problems.append("robot websocket is offline")
    if not result["gpt_sovits_service"]:
        problems.append("GPT-SoVITS primary voice is unavailable")
    if not result["voice_ready"]:
        problems.append("voice session is not ready")
    if not result["wake_word_ready"]:
        problems.append("wake-word detector is not ready")
    if not result["deployment_path_valid"]:
        problems.append("deployment path or runtime assets are missing")

    output = ROOT / "var/health/latest.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
