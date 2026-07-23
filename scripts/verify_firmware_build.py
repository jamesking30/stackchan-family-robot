#!/usr/bin/env python3
"""Validate the StackChan baseline build without touching a device."""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
from ipaddress import ip_address
from pathlib import Path
from urllib.parse import urlparse


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BUILD_DIR = ROOT / "var" / "firmware-build" / "m5stack-stack-chan-b72b3ede"
EXPECTED_FLASH_FILES = {
    "0x0": "bootloader/bootloader.bin",
    "0x8000": "partition_table/partition-table.bin",
    "0xd000": "ota_data_initial.bin",
    "0x20000": "stack-chan.bin",
    "0xa00000": "generated_assets.bin",
}
EXPECTED_CONFIG = {
    'CONFIG_IDF_TARGET="esp32s3"',
    "CONFIG_BOARD_TYPE_M5STACK_STACK_CHAN=y",
    "CONFIG_ESPTOOLPY_FLASHMODE_QIO=y",
    "CONFIG_ESPTOOLPY_FLASH_MODE_AUTO_DETECT=y",
    'CONFIG_ESPTOOLPY_FLASHSIZE="16MB"',
    "CONFIG_PARTITION_TABLE_CUSTOM=y",
    'CONFIG_PARTITION_TABLE_FILENAME="partitions.csv"',
    "CONFIG_SPIRAM=y",
    "CONFIG_SPIRAM_SPEED_80M=y",
}
EXPECTED_PARTITION_TABLE_SHA256 = (
    "48da0866f56d9d8eb1fe786412984d8f1a4a01b3f13f965621fcb916761e1a4b"
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def verify_image(path: Path) -> None:
    result = subprocess.run(
        [sys.executable, "-m", "esptool", "image_info", str(path)],
        check=False,
        text=True,
        capture_output=True,
    )
    require(result.returncode == 0, f"esptool rejected {path.name}: {result.stderr.strip()}")
    require("Detected image type: ESP32-S3" in result.stdout, f"{path.name} is not ESP32-S3")
    output_lower = result.stdout.lower()
    require(
        "validation hash:" in output_lower and "(valid)" in output_lower,
        f"{path.name} hash is invalid",
    )


def main() -> int:
    build_dir = Path(os.environ.get("STACKCHAN_BUILD_DIR", DEFAULT_BUILD_DIR)).resolve()
    flash_args_path = build_dir / "flasher_args.json"
    project_path = build_dir / "project_description.json"
    sdkconfig_path = build_dir / "sdkconfig"
    for path in (flash_args_path, project_path, sdkconfig_path):
        require(path.is_file(), f"missing build metadata: {path}")

    flash_args = json.loads(flash_args_path.read_text(encoding="utf-8"))
    project = json.loads(project_path.read_text(encoding="utf-8"))
    require(project["project_name"] == "stack-chan", "unexpected project name")
    require(project["project_version"] == "1.4.3", "unexpected public baseline version")
    require(project["target"] == "esp32s3", "unexpected build target")
    require(flash_args["flash_files"] == EXPECTED_FLASH_FILES, "flash offsets or artifacts changed")
    require(flash_args["flash_settings"]["flash_size"] == "16MB", "flash size is not 16MB")
    require(flash_args["flash_settings"]["flash_freq"] == "80m", "flash frequency is not 80MHz")
    require(flash_args["extra_esptool_args"]["chip"] == "esp32s3", "flash target is not ESP32-S3")

    config_lines = set(sdkconfig_path.read_text(encoding="utf-8").splitlines())
    missing_config = sorted(EXPECTED_CONFIG - config_lines)
    require(not missing_config, f"required board configuration is missing: {missing_config}")

    artifacts: dict[str, dict[str, int | str]] = {}
    for offset, relative_name in EXPECTED_FLASH_FILES.items():
        artifact = build_dir / relative_name
        require(artifact.is_file(), f"missing artifact: {artifact}")
        artifacts[offset] = {
            "file": relative_name,
            "size": artifact.stat().st_size,
            "sha256": sha256(artifact),
        }

    app = build_dir / "stack-chan.bin"
    assets = build_dir / "generated_assets.bin"
    partition_table = build_dir / "partition_table" / "partition-table.bin"
    require(app.stat().st_size <= 0x4F0000, "application exceeds the OTA partition")
    require(assets.stat().st_size <= 0x400000, "assets exceed the assets partition")
    require(
        sha256(partition_table) == EXPECTED_PARTITION_TABLE_SHA256,
        "generated partition table no longer matches the verified device layout",
    )
    verify_image(build_dir / "bootloader" / "bootloader.bin")
    verify_image(app)

    server_line = next(
        (line for line in config_lines if line.startswith('CONFIG_STACKCHAN_SERVER_URL="')),
        "",
    )
    server_url = server_line.partition("=")[2].strip('"')
    server_host = urlparse(server_url).hostname or ""
    parsed_server_url = urlparse(server_url)
    try:
        server_is_lan = (
            ip_address(server_host).is_private
            and not ip_address(server_host).is_loopback
        )
    except ValueError:
        server_is_lan = server_host.endswith(".local")
    server_is_remote = (
        parsed_server_url.scheme == "https"
        and not server_is_lan
        and "." in server_host
    )

    deployment_blockers = []
    if not server_is_lan and not server_is_remote:
        deployment_blockers.append(
            "server URL must be a LAN gateway or an HTTPS remote gateway"
        )

    product_config_path = build_dir / "product-config.json"
    device_auth: dict[str, str] | None = None
    if not product_config_path.is_file():
        deployment_blockers.append("device authentication override is not part of this baseline build")
    else:
        product_config = json.loads(product_config_path.read_text(encoding="utf-8"))
        require(product_config.get("schema_version") == 1, "unsupported product config schema")
        require(product_config.get("gateway_url") == server_url, "product gateway URL differs from sdkconfig")
        expected_mode = "remote" if server_is_remote else "lan"
        require(
            product_config.get("gateway_mode") == expected_mode,
            "product gateway mode differs from server URL",
        )
        ota_line = next(
            (line for line in config_lines if line.startswith('CONFIG_OTA_URL="')),
            "",
        )
        ota_url = ota_line.partition("=")[2].strip('"')
        require(product_config.get("ota_url") == ota_url, "product OTA URL differs from sdkconfig")
        auth = product_config.get("device_auth")
        require(isinstance(auth, dict), "product device auth metadata is missing")
        require(auth.get("mode") == "generated-build-secret", "unexpected device auth mode")
        token_sha256 = auth.get("token_sha256", "")
        require(
            isinstance(token_sha256, str) and re.fullmatch(r"[0-9a-f]{64}", token_sha256),
            "invalid device key fingerprint",
        )
        require(
            "product_overlay" in project.get("build_components", []),
            "device authentication override component was not linked",
        )
        require(bool(product_config.get("device_id")), "product device id is missing")
        device_auth = {"mode": auth["mode"], "token_sha256": token_sha256}

    manifest = {
        "schema_version": 2,
        "board": "m5stack-stack-chan",
        "target": "esp32s3",
        "public_baseline_version": project["project_version"],
        "deployment_ready": not deployment_blockers,
        "deployment_blockers": deployment_blockers,
        "gateway_host": server_host,
        "gateway_mode": "remote" if server_is_remote else "lan",
        "device_auth": device_auth,
        "flash_settings": flash_args["flash_settings"],
        "artifacts": artifacts,
    }
    manifest_path = build_dir / "firmware-manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    print(f"Firmware build verified: {build_dir}")
    print(f"Manifest: {manifest_path}")
    if deployment_blockers:
        print("Baseline is build-valid but not deployment-ready:")
        for blocker in deployment_blockers:
            print(f"- {blocker}")
    print("No device was accessed or flashed.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (KeyError, RuntimeError, ValueError) as exc:
        print(f"Firmware build verification failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
