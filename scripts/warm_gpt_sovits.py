#!/usr/bin/env python3
"""Prime model and reference-audio caches before the first conversation."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REFERENCE = ROOT / "var/models/gpt-sovits/elysia/reference-happy.wav"


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
            "text": "爱莉准备好了！",
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
            response.read()
    except (OSError, urllib.error.URLError):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
