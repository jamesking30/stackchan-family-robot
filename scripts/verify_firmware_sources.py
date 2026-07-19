#!/usr/bin/env python3
"""Verify every firmware checkout against the repository source lock."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LOCK_PATH = ROOT / "firmware" / "source-lock.json"
UPSTREAM_ROOT = ROOT / "firmware" / "upstream" / "stackchan"
FIRMWARE_ROOT = UPSTREAM_ROOT / "firmware"


def git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=check,
        text=True,
        capture_output=True,
    )


def require_revision(repo: Path, expected: str, label: str) -> None:
    if not repo.exists():
        raise RuntimeError(f"{label} is missing: {repo}")
    actual = git(repo, "rev-parse", "HEAD").stdout.strip()
    if actual != expected:
        raise RuntimeError(f"{label} revision mismatch: expected {expected}, got {actual}")


def verify_clean(repo: Path, label: str) -> None:
    status = git(repo, "status", "--porcelain", "--untracked-files=no").stdout.strip()
    if status:
        raise RuntimeError(f"{label} has unexpected tracked changes:\n{status}")


def verify_patch(repo: Path, patch: Path, expected_sha256: str, label: str) -> None:
    patch_digest = hashlib.sha256(patch.read_bytes()).hexdigest()
    if patch_digest != expected_sha256:
        raise RuntimeError(f"{label} patch file hash mismatch: {patch_digest}")
    result = git(repo, "apply", "--reverse", "--check", str(patch), check=False)
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip()
        raise RuntimeError(f"{label} expected patch is not applied: {detail}")
    current_diff = git(repo, "diff", "--no-ext-diff", "--binary").stdout.encode()
    diff_digest = hashlib.sha256(current_diff).hexdigest()
    if diff_digest != expected_sha256:
        raise RuntimeError(f"{label} working tree differs from the locked patch: {diff_digest}")


def main() -> int:
    lock = json.loads(LOCK_PATH.read_text(encoding="utf-8"))
    require_revision(UPSTREAM_ROOT, lock["stackchan"]["revision"], "m5stack/StackChan")
    verify_clean(UPSTREAM_ROOT, "m5stack/StackChan")

    idf_path = os.environ.get("IDF_PATH")
    if not idf_path:
        raise RuntimeError("IDF_PATH is not set; source scripts/firmware_env.sh first")
    idf_root = Path(idf_path)
    require_revision(idf_root, lock["esp_idf"]["revision"], "ESP-IDF")
    verify_clean(idf_root, "ESP-IDF")

    for item in lock["repositories"]:
        repo = FIRMWARE_ROOT / item["path"]
        label = item["path"]
        require_revision(repo, item["revision"], label)
        if patch_name := item.get("patch"):
            verify_patch(repo, FIRMWARE_ROOT / patch_name, item["patch_sha256"], label)
        else:
            verify_clean(repo, label)

    print("Firmware source lock verified.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (RuntimeError, subprocess.CalledProcessError) as exc:
        print(f"Firmware source verification failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
