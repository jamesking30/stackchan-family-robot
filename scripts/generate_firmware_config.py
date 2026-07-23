#!/usr/bin/env python3
"""Generate git-ignored StackChan product firmware configuration."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import socket
import subprocess
from ipaddress import ip_address
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT_DIR = ROOT / "var" / "firmware-config"
DEVICE_ID_PATTERN = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,63}$")
LOCAL_HOST_PATTERN = re.compile(
    r"^[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,62}\.)*local$",
    re.IGNORECASE,
)


def discover_lan_host() -> str:
    for interface in ("en0", "en1"):
        result = subprocess.run(
            ["ipconfig", "getifaddr", interface],
            check=False,
            capture_output=True,
            text=True,
        )
        candidate = result.stdout.strip()
        if candidate and is_lan_host(candidate):
            return candidate

    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
        try:
            probe.connect(("1.1.1.1", 80))
            candidate = probe.getsockname()[0]
        except OSError:
            candidate = ""
    if candidate and is_lan_host(candidate):
        return candidate
    raise ValueError("could not discover a private LAN address; pass --gateway-host")


def is_lan_host(value: str) -> bool:
    if LOCAL_HOST_PATTERN.fullmatch(value):
        return True
    try:
        address = ip_address(value)
    except ValueError:
        return False
    return (
        address.version == 4
        and address.is_private
        and not address.is_loopback
        and not address.is_link_local
    )


def validate_token(value: str) -> None:
    if len(value) < 24:
        raise ValueError("ROBOT_DEVICE_API_KEY must contain at least 24 characters")
    if len(value) > 200 or not value.isascii() or any(
        not 33 <= ord(character) <= 126 or character in {'"', "\\"}
        for character in value
    ):
        raise ValueError("ROBOT_DEVICE_API_KEY must be printable ASCII without spaces or quotes")
    if value == "hi-stack-chan":
        raise ValueError("the upstream default device key is not allowed")


def read_device_token() -> str:
    token = os.environ.get("ROBOT_DEVICE_API_KEY", "")
    if token:
        return token
    env_path = ROOT / ".env"
    if not env_path.is_file():
        return ""
    for line in env_path.read_text(encoding="utf-8").splitlines():
        key, separator, value = line.partition("=")
        if separator and key.strip() == "ROBOT_DEVICE_API_KEY":
            return value.strip().strip('"').strip("'")
    return ""


def write_private(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(0o600)


def generate(args: argparse.Namespace) -> dict[str, object]:
    token = read_device_token()
    validate_token(token)
    host = args.gateway_host or discover_lan_host()
    if not is_lan_host(host):
        raise ValueError("gateway host must be a private IPv4 address or a .local name")
    if not DEVICE_ID_PATTERN.fullmatch(args.device_id):
        raise ValueError("device id contains unsupported characters")
    if not 1 <= args.gateway_port <= 65535:
        raise ValueError("gateway port must be between 1 and 65535")

    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    output_dir.chmod(0o700)
    gateway_url = f"http://{host}:{args.gateway_port}"
    ota_url = f"{gateway_url}/v1/device/ota/check"
    token_sha256 = hashlib.sha256(token.encode()).hexdigest()

    sdkconfig = (
        f'CONFIG_STACKCHAN_SERVER_URL="{gateway_url}"\n'
        f'CONFIG_OTA_URL="{ota_url}"\n'
        "# CONFIG_SR_NSN_WEBRTC is not set\n"
        "CONFIG_SR_NSN_NSNET2=y\n"
        "# CONFIG_SR_VADN_WEBRTC is not set\n"
        "CONFIG_SR_VADN_VADNET1_MEDIUM=y\n"
    )
    header = (
        "#pragma once\n\n"
        f'#define STACKCHAN_DEVICE_TOKEN "{token}"\n'
        f'#define STACKCHAN_DEVICE_ID "{args.device_id}"\n'
    )
    metadata: dict[str, object] = {
        "schema_version": 1,
        "device_id": args.device_id,
        "gateway_url": gateway_url,
        "ota_url": ota_url,
        "device_auth": {
            "mode": "generated-build-secret",
            "token_sha256": token_sha256,
        },
    }
    write_private(output_dir / "sdkconfig.defaults.local", sdkconfig)
    write_private(output_dir / "device_auth_config.h", header)
    write_private(
        output_dir / "product-config.json",
        json.dumps(metadata, indent=2, ensure_ascii=False) + "\n",
    )
    return metadata


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--gateway-host")
    parser.add_argument("--gateway-port", type=int, default=8765)
    parser.add_argument("--device-id", default="stackchan-home-01")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    old_umask = os.umask(0o077)
    try:
        metadata = generate(args)
    finally:
        os.umask(old_umask)
    auth_metadata = metadata["device_auth"]
    assert isinstance(auth_metadata, dict)
    fingerprint = str(auth_metadata["token_sha256"])[:12]
    print(f"Firmware configuration generated in {args.output_dir.resolve()}")
    print(f"Gateway: {metadata['gateway_url']}")
    print(f"Device key fingerprint: {fingerprint}")
    print("The device key was not printed and the generated files are ignored by Git.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ValueError as exc:
        raise SystemExit(f"Firmware configuration failed: {exc}")
