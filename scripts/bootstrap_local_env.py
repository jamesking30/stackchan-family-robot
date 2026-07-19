#!/usr/bin/env python3
"""Create a private first-run configuration for the Mac gateway."""

from __future__ import annotations

import argparse
import hashlib
import os
import secrets
from pathlib import Path

from generate_firmware_config import ROOT, discover_lan_host, is_lan_host


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--gateway-host")
    parser.add_argument("--output", type=Path, default=ROOT / ".env")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    output = args.output.resolve()
    if output.exists():
        raise ValueError(f"{output} already exists; it was not changed")
    host = args.gateway_host or discover_lan_host()
    if not is_lan_host(host):
        raise ValueError("gateway host must be a private IPv4 address or a .local name")

    admin_key = secrets.token_urlsafe(32)
    device_key = secrets.token_urlsafe(32)
    template = (ROOT / ".env.example").read_text(encoding="utf-8")
    replacements = {
        "ROBOT_ADMIN_API_KEY": admin_key,
        "ROBOT_DEVICE_API_KEY": device_key,
        "ROBOT_HOST": "0.0.0.0",
        "STACKCHAN_HOST": host,
    }
    lines = []
    for line in template.splitlines():
        key, separator, _ = line.partition("=")
        if separator and key in replacements:
            line = f"{key}={replacements[key]}"
        lines.append(line)

    old_umask = os.umask(0o077)
    try:
        output.write_text("\n".join(lines) + "\n", encoding="utf-8")
        output.chmod(0o600)
    finally:
        os.umask(old_umask)

    admin_fingerprint = hashlib.sha256(admin_key.encode()).hexdigest()[:12]
    device_fingerprint = hashlib.sha256(device_key.encode()).hexdigest()[:12]
    print(f"Private Mac gateway configuration created: {output}")
    print(f"Gateway address: {host}:8765")
    print(f"Admin key fingerprint: {admin_fingerprint}")
    print(f"Device key fingerprint: {device_fingerprint}")
    print("The keys were not printed and .env is ignored by Git.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ValueError as exc:
        raise SystemExit(f"Bootstrap failed: {exc}")
