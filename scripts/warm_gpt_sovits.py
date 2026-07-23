#!/usr/bin/env python3
"""Prime model and reference-audio caches before the first conversation."""

from __future__ import annotations

import json
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REFERENCE = ROOT / "var/models/gpt-sovits/elysia/reference-happy.wav"
ACK_CACHE = ROOT / "var/cache/voice/wake-ack-v2.wav"
ACK_MANIFEST = ROOT / "var/cache/voice/wake-ack-v2.json"


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

    payload = json.dumps(
        {
            "text": "我在。",
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
    try:
        with urllib.request.urlopen(request, timeout=180) as response:
            wav_audio = response.read()
    except (OSError, urllib.error.URLError):
        return 1
    ACK_CACHE.parent.mkdir(parents=True, exist_ok=True)
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
        return 1
    ACK_CACHE.write_bytes(conversion.stdout)
    ACK_MANIFEST.write_text(
        json.dumps(
            {
                "version": 2,
                "sample_rate": 24000,
                "channels": 1,
                "sample_width_bytes": 2,
                "target_loudness_lufs": -18,
                "true_peak_dbtp": -2,
                "voice_model": "elysia-v2",
                "text": "我在。",
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
