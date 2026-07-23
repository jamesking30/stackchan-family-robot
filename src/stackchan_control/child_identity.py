from __future__ import annotations

import audioop
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np


@dataclass(frozen=True)
class ChildVoiceEvidence:
    is_child: bool
    score: float
    median_pitch_hz: float | None
    voiced_ratio: float
    analyzed_ms: int


@dataclass(frozen=True)
class ChildFaceEvidence:
    is_child: bool
    score: float
    estimated_age: int | None


@dataclass(frozen=True)
class WakeIdentityEvidence:
    voice: ChildVoiceEvidence
    face: ChildFaceEvidence | None

    @property
    def confirmed_child(self) -> bool:
        return self.voice.is_child and bool(self.face and self.face.is_child)

    @property
    def confidence(self) -> float:
        if self.face is None:
            return 0.0
        return round(min(self.voice.score, self.face.score), 3)


class ChildVoiceClassifier:
    """Conservative, local pitch-based evidence from the wake-word audio."""

    def __init__(
        self,
        *,
        sample_rate: int = 16000,
        minimum_pitch_hz: float = 260.0,
        minimum_voiced_ratio: float = 0.22,
    ) -> None:
        self.sample_rate = sample_rate
        self.minimum_pitch_hz = minimum_pitch_hz
        self.minimum_voiced_ratio = minimum_voiced_ratio

    def classify(self, pcm: bytes) -> ChildVoiceEvidence:
        samples = np.frombuffer(pcm, dtype="<i2").astype(np.float32)
        analyzed_ms = round(len(samples) * 1000 / self.sample_rate)
        if len(samples) < self.sample_rate // 5 or audioop.rms(pcm, 2) < 180:
            return ChildVoiceEvidence(False, 0.0, None, 0.0, analyzed_ms)

        frame_size = round(self.sample_rate * 0.03)
        hop = round(self.sample_rate * 0.015)
        minimum_lag = max(1, round(self.sample_rate / 500.0))
        maximum_lag = round(self.sample_rate / 70.0)
        pitches: list[float] = []
        total_frames = 0
        window = np.hanning(frame_size).astype(np.float32)
        for start in range(0, len(samples) - frame_size + 1, hop):
            frame = samples[start : start + frame_size]
            if float(np.sqrt(np.mean(frame * frame))) < 220.0:
                continue
            total_frames += 1
            frame = (frame - float(frame.mean())) * window
            correlation = np.correlate(frame, frame, mode="full")[frame_size - 1 :]
            base = float(correlation[0])
            if base <= 0:
                continue
            search = correlation[minimum_lag : maximum_lag + 1]
            lag = int(np.argmax(search)) + minimum_lag
            strength = float(correlation[lag] / base)
            if strength >= 0.42:
                pitches.append(self.sample_rate / lag)

        voiced_ratio = len(pitches) / max(1, total_frames)
        if not pitches:
            return ChildVoiceEvidence(False, 0.0, None, 0.0, analyzed_ms)
        median_pitch = float(np.median(pitches))
        pitch_score = float(
            np.clip((median_pitch - 220.0) / 100.0, 0.0, 1.0)
        )
        voiced_score = float(np.clip(voiced_ratio / 0.45, 0.0, 1.0))
        score = round(pitch_score * voiced_score, 3)
        is_child = (
            median_pitch >= self.minimum_pitch_hz
            and voiced_ratio >= self.minimum_voiced_ratio
            and score >= 0.55
        )
        return ChildVoiceEvidence(
            is_child,
            score,
            round(median_pitch, 1),
            round(voiced_ratio, 3),
            analyzed_ms,
        )


class InsightFaceAgeClassifier:
    """Run InsightFace's gender/age ONNX model on one in-memory face crop."""

    def __init__(self, model_path: Path, maximum_child_age: int = 11) -> None:
        if not model_path.is_file():
            raise FileNotFoundError(f"age model is missing: {model_path}")
        try:
            import onnxruntime as ort
        except ImportError as exc:
            raise RuntimeError("onnxruntime is required for age estimation") from exc
        self.maximum_child_age = maximum_child_age
        self._session = ort.InferenceSession(
            str(model_path), providers=["CPUExecutionProvider"]
        )
        input_config = self._session.get_inputs()[0]
        self._input_name = input_config.name
        self._input_size = tuple(input_config.shape[2:4][::-1])
        self._output_names = [
            output.name for output in self._session.get_outputs()
        ]

    def classify(
        self,
        jpeg: bytes,
        *,
        center_x: float,
        center_y: float,
        width: float,
        height: float,
    ) -> ChildFaceEvidence:
        encoded = np.frombuffer(jpeg, dtype=np.uint8)
        image = cv2.imdecode(encoded, cv2.IMREAD_COLOR)
        if image is None or image.size == 0:
            return ChildFaceEvidence(False, 0.0, None)
        image_height, image_width = image.shape[:2]
        face_size = max(width * image_width, height * image_height) * 1.5
        x1 = max(0, round(center_x * image_width - face_size / 2))
        y1 = max(0, round(center_y * image_height - face_size / 2))
        x2 = min(image_width, round(center_x * image_width + face_size / 2))
        y2 = min(image_height, round(center_y * image_height + face_size / 2))
        crop = image[y1:y2, x1:x2]
        if crop.size == 0:
            return ChildFaceEvidence(False, 0.0, None)
        crop = cv2.resize(crop, self._input_size)
        blob = cv2.dnn.blobFromImage(
            crop,
            1.0 / 128.0,
            self._input_size,
            (127.5, 127.5, 127.5),
            swapRB=True,
        )
        prediction = self._session.run(
            self._output_names, {self._input_name: blob}
        )[0][0]
        if len(prediction) != 3:
            return ChildFaceEvidence(False, 0.0, None)
        age = int(np.clip(np.rint(float(prediction[2]) * 100), 0, 100))
        score = round(float(np.clip((15.0 - age) / 7.0, 0.0, 1.0)), 3)
        return ChildFaceEvidence(
            is_child=age <= self.maximum_child_age and score >= 0.55,
            score=score,
            estimated_age=age,
        )
