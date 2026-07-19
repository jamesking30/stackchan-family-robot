#!/usr/bin/env python3
"""Validate the StackChan baseline build without touching a device."""

from __future__ import annotations

import hashlib
import json
import os
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
    require("Validation Hash:" in result.stdout and "(valid)" in result.stdout, f"{path.name} hash is invalid")


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
    try:
        server_is_lan = ip_address(server_host).is_private and not ip_address(server_host).is_loopback
    except ValueError:
        server_is_lan = server_host.endswith(".local")

    deployment_blockers = []
    if not server_is_lan:
        deployment_blockers.append("upstream server URL has not been replaced by the local Mac gateway")

    manifest = {
        "schema_version": 1,
        "board": "m5stack-stack-chan",
        "target": "esp32s3",
        "public_baseline_version": project["project_version"],
        "deployment_ready": not deployment_blockers,
        "deployment_blockers": deployment_blockers,
        "gateway_host": server_host,
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
