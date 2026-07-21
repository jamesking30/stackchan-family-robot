#!/usr/bin/env python3
"""Generate the ignored GPT-SoVITS v2 inference configuration."""

from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
UPSTREAM = ROOT / "var/gpt-sovits/GPT-SoVITS"
MODEL_DIR = ROOT / "var/models/gpt-sovits/elysia"
OUTPUT = ROOT / "var/gpt-sovits/elysia-v2.yaml"


def require(path: Path) -> Path:
    if not path.exists():
        raise SystemExit(f"Required GPT-SoVITS file is missing: {path}")
    return path.resolve()


def main() -> int:
    values = {
        "bert": require(
            UPSTREAM
            / "GPT_SoVITS/pretrained_models/chinese-roberta-wwm-ext-large"
        ),
        "hubert": require(
            UPSTREAM / "GPT_SoVITS/pretrained_models/chinese-hubert-base"
        ),
        "gpt": require(MODEL_DIR / "elysia-gpt-e20.ckpt"),
        "sovits": require(MODEL_DIR / "elysia-sovits-e24.pth"),
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(
        "custom:\n"
        f"  bert_base_path: {values['bert']}\n"
        f"  cnhuhbert_base_path: {values['hubert']}\n"
        "  device: cpu\n"
        "  is_half: false\n"
        f"  t2s_weights_path: {values['gpt']}\n"
        "  version: v2\n"
        f"  vits_weights_path: {values['sovits']}\n",
        encoding="utf-8",
    )
    print(OUTPUT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
