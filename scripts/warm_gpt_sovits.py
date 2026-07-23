#!/usr/bin/env python3
"""Prime GPT-SoVITS and build low-latency wake acknowledgement variants."""

from __future__ import annotations

import json
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REFERENCE = ROOT / "var/models/gpt-sovits/elysia/reference-happy.wav"
ACK_DIR = ROOT / "var/cache/voice/wake-acks-v1"
ACK_MANIFEST = ACK_DIR / "manifest.json"
ACK_VARIANTS = (
    ("wo-zai-ya", "我在呀。"),
    ("lai-la", "来啦。"),
    ("zen-me-la", "怎么啦？"),
    ("hai-wo-zai", "嗨，我在。"),
)


def synthesize(text: str) -> bytes:
    payload = json.dumps(
        {
            "text": text,
            "text_lang": "zh",
            "ref_audio_path": str(REFERENCE),
            "prompt_text": "所以你今天就来见我了吗？哇，真令人开心呢。",
            "prompt_lang": "zh",
            "text_split_method": "cut5",
            "batch_size": 1,
            "speed_factor": 1.08,
            "media_type": "wav",
            "streaming_mode": False,
            "parallel_infer": True,
            "repetition_penalty": 1.35,
        },
        ensure_ascii=False,
    ).encode("utf-8")
    request = urllib.request.Request(
        "http://127.0.0.1:9880/tts",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=180) as response:
        wav_audio = response.read()
    conversion = subprocess.run(
        [
            "/opt/homebrew/bin/ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            "pipe:0",
            "-af",
            "loudnorm=I=-18:TP=-2:LRA=7",
            "-f",
            "wav",
            "-acodec",
            "pcm_s16le",
            "-ac",
            "1",
            "-ar",
            "24000",
            "pipe:1",
        ],
        input=wav_audio,
        capture_output=True,
        check=False,
    )
    if conversion.returncode != 0 or not conversion.stdout:
        raise RuntimeError("wake acknowledgement conversion failed")
    return conversion.stdout


def main() -> int:
    if not REFERENCE.is_file():
        return 1
    for _ in range(120):
        try:
            urllib.request.urlopen("http://127.0.0.1:9880/docs", timeout=1).close()
            break
        except (OSError, urllib.error.URLError):
            time.sleep(1)
    else:
        return 1

    ACK_DIR.mkdir(parents=True, exist_ok=True)
    generated = []
    try:
        for slug, text in ACK_VARIANTS:
            path = ACK_DIR / f"{slug}.wav"
            if not path.is_file():
                path.write_bytes(synthesize(text))
            generated.append({"slug": slug, "text": text, "file": path.name})

        # Existing caches make startup cheap, but one short inference still
        # primes model execution before the first real wake.
        if all((ACK_DIR / f"{slug}.wav").is_file() for slug, _ in ACK_VARIANTS):
            synthesize("我在。")
    except (OSError, RuntimeError, urllib.error.URLError):
        return 1

    ACK_MANIFEST.write_text(
        json.dumps(
            {
                "version": 1,
                "sample_rate": 24000,
                "channels": 1,
                "sample_width_bytes": 2,
                "target_loudness_lufs": -18,
                "true_peak_dbtp": -2,
                "voice_model": "elysia-v2",
                "variants": generated,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
