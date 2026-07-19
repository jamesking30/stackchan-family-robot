from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
from pathlib import Path

from dotenv import dotenv_values


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "generate_firmware_config.py"
BOOTSTRAP_SCRIPT = ROOT / "scripts" / "bootstrap_local_env.py"


def run_generator(output_dir: Path, token: str):
    environment = os.environ.copy()
    environment["ROBOT_DEVICE_API_KEY"] = token
    return subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--gateway-host",
            "192.168.31.65",
            "--output-dir",
            str(output_dir),
        ],
        check=False,
        capture_output=True,
        text=True,
        env=environment,
    )


def test_firmware_config_keeps_raw_device_key_out_of_metadata(tmp_path: Path):
    token = "local-test-device-key-123456789"
    output_dir = tmp_path / "config"
    result = run_generator(output_dir, token)
    assert result.returncode == 0, result.stderr
    assert token not in result.stdout

    metadata_text = (output_dir / "product-config.json").read_text()
    assert token not in metadata_text
    metadata = json.loads(metadata_text)
    assert metadata["gateway_url"] == "http://192.168.31.65:8765"
    assert len(metadata["device_auth"]["token_sha256"]) == 64

    sdkconfig = (output_dir / "sdkconfig.defaults.local").read_text()
    assert 'CONFIG_STACKCHAN_SERVER_URL="http://192.168.31.65:8765"' in sdkconfig
    assert 'CONFIG_OTA_URL="http://192.168.31.65:8765/v1/device/ota/check"' in sdkconfig
    assert token in (output_dir / "device_auth_config.h").read_text()
    assert stat.S_IMODE(output_dir.stat().st_mode) == 0o700
    for path in output_dir.iterdir():
        assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_firmware_config_rejects_weak_key_and_non_lan_host(tmp_path: Path):
    weak = run_generator(tmp_path / "weak", "too-short")
    assert weak.returncode != 0
    assert "at least 24 characters" in weak.stderr

    environment = os.environ.copy()
    environment["ROBOT_DEVICE_API_KEY"] = "local-test-device-key-123456789"
    public = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--gateway-host",
            "8.8.8.8",
            "--output-dir",
            str(tmp_path / "public"),
        ],
        check=False,
        capture_output=True,
        text=True,
        env=environment,
    )
    assert public.returncode != 0
    assert "private IPv4" in public.stderr


def test_bootstrap_creates_distinct_private_keys_without_printing_them(tmp_path: Path):
    env_path = tmp_path / ".env"
    result = subprocess.run(
        [
            sys.executable,
            str(BOOTSTRAP_SCRIPT),
            "--gateway-host",
            "192.168.31.65",
            "--output",
            str(env_path),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    values = dotenv_values(env_path)
    admin_key = values["ROBOT_ADMIN_API_KEY"]
    device_key = values["ROBOT_DEVICE_API_KEY"]
    assert admin_key and device_key and admin_key != device_key
    assert len(admin_key) >= 24 and len(device_key) >= 24
    assert admin_key not in result.stdout and device_key not in result.stdout
    assert values["ROBOT_HOST"] == "0.0.0.0"
    assert values["STACKCHAN_HOST"] == "192.168.31.65"
    assert stat.S_IMODE(env_path.stat().st_mode) == 0o600
