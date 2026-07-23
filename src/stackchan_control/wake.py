from __future__ import annotations

import sys
import time
import audioop
from array import array
from pathlib import Path
from typing import Protocol

import sherpa_onnx

from .settings import Settings


class WakeWordDetector(Protocol):
    last_frame_latency_ms: float
    last_input_rms: int
    last_applied_gain: float
    processed_frames: int

    def accept_pcm(self, pcm: bytes) -> str | None: ...

    def reset(self) -> None: ...


class SherpaWakeWordDetector:
    """Streaming, open-vocabulary Chinese/English keyword spotter."""

    def __init__(self, settings: Settings) -> None:
        model_dir = settings.voice_kws_model_dir
        files = {
            "tokens": model_dir / "tokens.txt",
            "encoder": model_dir / "encoder-epoch-13-avg-2-chunk-8-left-64.int8.onnx",
            "decoder": model_dir / "decoder-epoch-13-avg-2-chunk-8-left-64.onnx",
            "joiner": model_dir / "joiner-epoch-13-avg-2-chunk-8-left-64.int8.onnx",
            "keywords_file": settings.voice_kws_keywords_file,
        }
        missing = [str(path) for path in files.values() if not Path(path).is_file()]
        if missing:
            raise FileNotFoundError(
                "Sherpa wake-word model files are missing: " + ", ".join(missing)
            )
        self._spotter = sherpa_onnx.KeywordSpotter(
            **{name: str(path) for name, path in files.items()},
            num_threads=2,
            sample_rate=16000,
            max_active_paths=4,
            keywords_score=settings.voice_kws_score,
            keywords_threshold=settings.voice_kws_threshold,
            provider="cpu",
        )
        self._stream = self._spotter.create_stream()
        self.last_frame_latency_ms = 0.0
        self.last_input_rms = 0
        self.last_applied_gain = 1.0
        self.processed_frames = 0
        self._target_rms = settings.voice_kws_target_rms
        self._max_gain = settings.voice_kws_max_gain
        self._min_gain_rms = settings.voice_kws_min_gain_rms

    def accept_pcm(self, pcm: bytes) -> str | None:
        if not pcm:
            return None
        started = time.perf_counter()
        self.processed_frames += 1
        self.last_input_rms = audioop.rms(pcm, 2)
        self.last_applied_gain = 1.0
        if self._min_gain_rms <= self.last_input_rms < self._target_rms:
            self.last_applied_gain = min(
                self._max_gain, self._target_rms / self.last_input_rms
            )
            pcm = audioop.mul(pcm, 2, self.last_applied_gain)
        samples = array("h")
        samples.frombytes(pcm)
        if sys.byteorder != "little":
            samples.byteswap()
        normalized = [sample / 32768.0 for sample in samples]
        self._stream.accept_waveform(16000, normalized)
        keyword: str | None = None
        while self._spotter.is_ready(self._stream):
            self._spotter.decode_stream(self._stream)
            result = self._spotter.get_result(self._stream)
            if result:
                keyword = str(result)
                self.reset()
                break
        self.last_frame_latency_ms = round(
            (time.perf_counter() - started) * 1000,
            1,
        )
        return keyword

    def reset(self) -> None:
        self._stream = self._spotter.create_stream()
