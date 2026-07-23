from __future__ import annotations

import asyncio
import logging
import math
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Protocol

import cv2
import mediapipe as mp
import numpy as np

from .child_identity import (
    ChildFaceEvidence,
    ChildVoiceEvidence,
    InsightFaceAgeClassifier,
    WakeIdentityEvidence,
)
from .gateway import DeviceOfflineError, MessageType, StackChanGateway
from .settings import Settings


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class FaceDetection:
    center_x: float
    center_y: float
    width: float
    height: float
    confidence: float

    @property
    def area(self) -> float:
        return self.width * self.height


@dataclass(frozen=True)
class ScanCandidate:
    scan_yaw: float
    scan_pitch: float
    detection: FaceDetection

    @property
    def proximity_score(self) -> float:
        return self.detection.area * self.detection.confidence


class FaceDetector(Protocol):
    def detect(self, jpeg: bytes) -> list[FaceDetection]: ...


class BodyHeadEstimator(Protocol):
    def estimate_heads(self, jpeg: bytes) -> list[FaceDetection]: ...


class FaceAgeClassifier(Protocol):
    def classify(
        self,
        jpeg: bytes,
        *,
        center_x: float,
        center_y: float,
        width: float,
        height: float,
    ) -> ChildFaceEvidence: ...


class MediaPipeFaceDetector:
    """Local-only face detector. Input frames are decoded in memory and discarded."""

    def __init__(self, model_path: Path, min_confidence: float) -> None:
        if not model_path.is_file():
            raise FileNotFoundError(f"face detector model is missing: {model_path}")
        options = mp.tasks.vision.FaceDetectorOptions(
            base_options=mp.tasks.BaseOptions(model_asset_path=str(model_path)),
            running_mode=mp.tasks.vision.RunningMode.IMAGE,
            min_detection_confidence=min_confidence,
        )
        self._detector = mp.tasks.vision.FaceDetector.create_from_options(options)

    def detect(self, jpeg: bytes) -> list[FaceDetection]:
        encoded = np.frombuffer(jpeg, dtype=np.uint8)
        bgr = cv2.imdecode(encoded, cv2.IMREAD_COLOR)
        if bgr is None or bgr.size == 0:
            return []
        rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
        height, width = rgb.shape[:2]
        image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
        result = self._detector.detect(image)
        detections: list[FaceDetection] = []
        for item in result.detections:
            box = item.bounding_box
            confidence = float(item.categories[0].score) if item.categories else 0.0
            detections.append(
                FaceDetection(
                    center_x=(box.origin_x + box.width / 2) / width,
                    center_y=(box.origin_y + box.height / 2) / height,
                    width=box.width / width,
                    height=box.height / height,
                    confidence=confidence,
                )
            )
        return detections


class MediaPipeBodyHeadEstimator:
    """Infer a likely head location from locally detected upper-body landmarks."""

    _UPPER_BODY = (11, 12, 13, 14, 15, 16, 23, 24)

    def __init__(self, model_path: Path, min_confidence: float) -> None:
        if not model_path.is_file():
            raise FileNotFoundError(
                f"pose detector model is missing: {model_path}"
            )
        options = mp.tasks.vision.PoseLandmarkerOptions(
            base_options=mp.tasks.BaseOptions(model_asset_path=str(model_path)),
            running_mode=mp.tasks.vision.RunningMode.IMAGE,
            num_poses=2,
            min_pose_detection_confidence=min_confidence,
            min_pose_presence_confidence=min_confidence,
            min_tracking_confidence=min_confidence,
            output_segmentation_masks=False,
        )
        self._landmarker = (
            mp.tasks.vision.PoseLandmarker.create_from_options(options)
        )
        self._min_confidence = min_confidence

    @staticmethod
    def _score(landmark: object) -> float:
        visibility = float(getattr(landmark, "visibility", 1.0) or 0.0)
        presence = float(getattr(landmark, "presence", 1.0) or 0.0)
        return min(visibility, presence)

    def estimate_heads(self, jpeg: bytes) -> list[FaceDetection]:
        encoded = np.frombuffer(jpeg, dtype=np.uint8)
        bgr = cv2.imdecode(encoded, cv2.IMREAD_COLOR)
        if bgr is None or bgr.size == 0:
            return []
        rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
        image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
        result = self._landmarker.detect(image)
        estimates: list[FaceDetection] = []
        for landmarks in result.pose_landmarks:
            estimate = self._estimate_one(landmarks)
            if estimate is not None:
                estimates.append(estimate)
        return estimates

    def _estimate_one(self, landmarks: list[object]) -> FaceDetection | None:
        nose = landmarks[0]
        nose_score = self._score(nose)
        left_shoulder = landmarks[11]
        right_shoulder = landmarks[12]
        left_score = self._score(left_shoulder)
        right_score = self._score(right_shoulder)

        if (
            left_score >= self._min_confidence
            and right_score >= self._min_confidence
        ):
            shoulder_width = abs(
                float(left_shoulder.x) - float(right_shoulder.x)
            )
            if shoulder_width < 0.035:
                return None
            shoulder_x = (
                float(left_shoulder.x) + float(right_shoulder.x)
            ) / 2
            shoulder_y = (
                float(left_shoulder.y) + float(right_shoulder.y)
            ) / 2
            center_x = (
                float(nose.x)
                if nose_score >= self._min_confidence
                else shoulder_x
            )
            center_y = (
                float(nose.y)
                if nose_score >= self._min_confidence
                else shoulder_y - shoulder_width * 0.58
            )
            confidence = min(0.92, (left_score + right_score) / 2)
            width = max(0.08, min(0.34, shoulder_width * 0.52))
            return FaceDetection(
                center_x=max(-0.25, min(1.25, center_x)),
                center_y=max(-0.30, min(1.20, center_y)),
                width=width,
                height=min(0.42, width * 1.25),
                confidence=confidence,
            )

        # A partly cropped person may expose only arms/chest. MediaPipe uses
        # those landmarks to recover the pose; use a conservative median as a
        # directional hint, never as a confirmed face.
        visible = [
            landmarks[index]
            for index in self._UPPER_BODY
            if self._score(landmarks[index]) >= self._min_confidence
        ]
        if len(visible) < 3:
            return None
        xs = sorted(float(item.x) for item in visible)
        ys = sorted(float(item.y) for item in visible)
        span_x = max(xs) - min(xs)
        span_y = max(ys) - min(ys)
        if max(span_x, span_y) < 0.08:
            return None
        center_x = xs[len(xs) // 2]
        upper_y = min(ys)
        center_y = upper_y - max(0.10, min(0.28, span_y * 0.38))
        confidence = min(
            0.72,
            sum(self._score(item) for item in visible) / len(visible) * 0.8,
        )
        width = max(0.10, min(0.30, max(0.18, span_x * 0.35)))
        return FaceDetection(
            center_x=max(-0.25, min(1.25, center_x)),
            center_y=max(-0.30, min(1.20, center_y)),
            width=width,
            height=min(0.40, width * 1.25),
            confidence=confidence,
        )


class PresenceTracker:
    """Periodically scan for the nearest visible face and turn StackChan toward it."""

    def __init__(
        self,
        settings: Settings,
        gateway: StackChanGateway,
        voice_mode: Callable[[], str],
        detector: FaceDetector | None = None,
        body_head_estimator: BodyHeadEstimator | None = None,
        age_classifier: FaceAgeClassifier | None = None,
        voice_is_speaking: Callable[[], bool] | None = None,
    ) -> None:
        self.settings = settings
        self.gateway = gateway
        self.voice_mode = voice_mode
        self.voice_is_speaking = voice_is_speaking or (lambda: False)
        self._detector = detector
        self._body_head_estimator = body_head_estimator
        self._body_head_estimator_attempted = body_head_estimator is not None
        self._age_classifier = age_classifier
        self._age_classifier_attempted = age_classifier is not None
        self._loop_task: asyncio.Task[None] | None = None
        self._operation_lock = asyncio.Lock()
        self._wake_reacquire_lock = asyncio.Lock()
        self._manual_override_until = 0.0
        self._next_full_scan = 0.0
        self._current_yaw = 0.0
        self._current_pitch = settings.presence_pitch_degrees
        self._target_score: float | None = None
        self._target_seen_at: datetime | None = None
        self.enabled = settings.presence_enabled
        self.mode = "disabled" if not self.enabled else "idle"
        self.faces_detected = 0
        self.target_yaw: float | None = None
        self.target_pitch: float | None = None
        self.target_center_x: float | None = None
        self.target_center_y: float | None = None
        self.target_area: float | None = None
        self.target_scan_yaw: float | None = None
        self.target_scan_pitch: float | None = None
        self.last_scan_at: datetime | None = None
        self.last_wake_reacquire_at: datetime | None = None
        self.last_wake_reacquire_found: bool | None = None
        self.body_guidance_count = 0
        self.last_body_guided_at: datetime | None = None
        self.last_child_face: bool | None = None
        self.last_estimated_age: int | None = None
        self.last_child_identity_confidence: float | None = None
        self.last_error: str | None = None

    def snapshot(self) -> dict[str, object]:
        return {
            "enabled": self.enabled,
            "mode": self.mode,
            "faces_detected": self.faces_detected,
            "target_yaw": self.target_yaw,
            "target_pitch": self.target_pitch,
            "target_center_x": self.target_center_x,
            "target_center_y": self.target_center_y,
            "target_area": self.target_area,
            "target_scan_yaw": self.target_scan_yaw,
            "target_scan_pitch": self.target_scan_pitch,
            "current_yaw": round(self._current_yaw, 1),
            "current_pitch": round(self._current_pitch, 1),
            "last_scan_at": self.last_scan_at,
            "last_wake_reacquire_at": self.last_wake_reacquire_at,
            "last_wake_reacquire_found": self.last_wake_reacquire_found,
            "body_guidance_enabled": (
                self.settings.presence_body_guidance_enabled
            ),
            "body_guidance_available": self._body_head_estimator is not None,
            "body_guidance_count": self.body_guidance_count,
            "last_body_guided_at": self.last_body_guided_at,
            "last_child_face": self.last_child_face,
            "last_estimated_age": self.last_estimated_age,
            "last_child_identity_confidence": self.last_child_identity_confidence,
            "target_seen_at": self._target_seen_at,
            "manual_override_seconds": round(
                max(0.0, self._manual_override_until - time.monotonic()), 1
            ),
            "camera_frames_persisted": False,
            "error": self.last_error,
        }

    async def on_device_connected(self) -> None:
        if not self.enabled:
            return
        try:
            self._ensure_detector()
        except Exception as exc:
            self.mode = "error"
            self.last_error = str(exc)[:240]
            logger.warning("presence detector failed to start: %s", exc)
            return
        self._next_full_scan = time.monotonic() + self.settings.presence_start_delay_seconds
        if self._loop_task is None or self._loop_task.done():
            self._loop_task = asyncio.create_task(self._run_loop())

    async def on_device_disconnected(self) -> None:
        task = self._loop_task
        self._loop_task = None
        if task is not None and not task.done():
            task.cancel()
        self.mode = "offline" if self.enabled else "disabled"

    def note_manual_override(self, yaw: float, pitch: float) -> None:
        self._manual_override_until = (
            time.monotonic() + self.settings.presence_manual_override_seconds
        )
        self._current_yaw = yaw
        self._current_pitch = pitch
        self.target_yaw = None
        self.target_pitch = None
        self._clear_target_metadata()
        self.mode = "manual_override"

    async def reacquire_after_wake(
        self, voice_evidence: ChildVoiceEvidence | None = None
    ) -> WakeIdentityEvidence | None:
        if (
            not self.enabled
            or self._wake_reacquire_lock.locked()
            or time.monotonic() < self._manual_override_until
        ):
            return None
        async with self._wake_reacquire_lock:
            deadline = time.monotonic() + 12.0
            while self.voice_mode() not in {
                "listening",
                "waiting_for_wake_word",
                "speaking",
                "stopped",
            }:
                if time.monotonic() >= deadline:
                    self.mode = "wake_deferred"
                    self._next_full_scan = 0.0
                    return None
                await asyncio.sleep(0.1)
            if (
                not await self.gateway.is_online()
                or time.monotonic() < self._manual_override_until
            ):
                return None
            async with self._operation_lock:
                return await self._quick_reacquire(voice_evidence)

    async def scan_now(self, *, force: bool = False) -> dict[str, object]:
        if not self.enabled:
            return self.snapshot()
        self._ensure_detector()
        if not force and not self._can_full_scan():
            self.mode = "deferred"
            return self.snapshot()
        async with self._operation_lock:
            if not force and (
                not self._can_full_scan()
                or time.monotonic() < self._next_full_scan
            ):
                return self.snapshot()
            await self._full_scan(force=force)
        return self.snapshot()

    async def _run_loop(self) -> None:
        try:
            while self.enabled and await self.gateway.is_online():
                now = time.monotonic()
                if self._can_full_scan() and now >= self._next_full_scan:
                    await self.scan_now()
                elif self.target_yaw is not None and self._can_track():
                    async with self._operation_lock:
                        await self._track_target()
                interval = (
                    self.settings.presence_active_tracking_interval_seconds
                    if self.voice_mode() == "listening"
                    else self.settings.presence_tracking_interval_seconds
                )
                await asyncio.sleep(interval)
        except asyncio.CancelledError:
            raise
        except DeviceOfflineError:
            self.mode = "offline"
        except Exception as exc:
            logger.exception("presence tracking failed")
            self.mode = "error"
            self.last_error = str(exc)[:240]

    def _can_full_scan(self) -> bool:
        return (
            self.voice_mode() in {"waiting_for_wake_word", "stopped"}
            and time.monotonic() >= self._manual_override_until
        )

    def _can_track(self) -> bool:
        return (
            self.voice_mode()
            in {"waiting_for_wake_word", "listening", "stopped"}
            and not self.voice_is_speaking()
            and time.monotonic() >= self._manual_override_until
        )

    def _ensure_detector(self) -> None:
        if self._detector is None:
            self._detector = MediaPipeFaceDetector(
                self.settings.presence_face_model,
                self.settings.presence_min_confidence,
            )
        self._ensure_body_head_estimator()

    def _ensure_body_head_estimator(self) -> BodyHeadEstimator | None:
        if not self.settings.presence_body_guidance_enabled:
            return None
        if not self._body_head_estimator_attempted:
            self._body_head_estimator_attempted = True
            try:
                self._body_head_estimator = MediaPipeBodyHeadEstimator(
                    self.settings.presence_pose_model,
                    self.settings.presence_pose_min_confidence,
                )
            except Exception as exc:
                logger.warning("body guidance unavailable: %s", exc)
                self._body_head_estimator = None
        return self._body_head_estimator

    def _ensure_age_classifier(self) -> FaceAgeClassifier | None:
        if not self.settings.child_identity_enabled:
            return None
        if not self._age_classifier_attempted:
            self._age_classifier_attempted = True
            try:
                self._age_classifier = InsightFaceAgeClassifier(
                    self.settings.child_identity_age_model,
                    self.settings.child_identity_maximum_age,
                )
            except Exception as exc:
                logger.warning("child age classifier unavailable: %s", exc)
                self._age_classifier = None
        return self._age_classifier

    async def _full_scan(self, *, force: bool = False) -> None:
        self.mode = "scanning"
        self.last_error = None
        candidates: list[ScanCandidate] = []
        aborted = False
        starting_yaw = self._current_yaw
        starting_pitch = self._current_pitch
        await self.gateway.send(MessageType.START_CAMERA_STREAM)
        try:
            for yaw_index, yaw in enumerate(
                self.settings.presence_scan_yaw_degrees
            ):
                pitches = self.settings.presence_scan_pitch_degrees
                if yaw_index % 2:
                    pitches = tuple(reversed(pitches))
                for pitch in pitches:
                    if not force and not self._can_full_scan():
                        aborted = True
                        break
                    await self._move(yaw, pitch)
                    await asyncio.sleep(
                        self.settings.presence_servo_settle_seconds
                    )
                    if not force and not self._can_full_scan():
                        aborted = True
                        break
                    self.gateway.clear_camera_frames()
                    for frame in await self._collect_frames():
                        assert self._detector is not None
                        for detection in self._detector.detect(frame):
                            candidates.append(
                                ScanCandidate(yaw, pitch, detection)
                            )
                if aborted:
                    break
        finally:
            await self.gateway.send(MessageType.STOP_CAMERA_STREAM)

        if aborted:
            if time.monotonic() < self._manual_override_until:
                self.mode = "manual_override"
            else:
                await self._move(starting_yaw, starting_pitch)
                self._next_full_scan = time.monotonic() + 30.0
                self.mode = "deferred"
            return
        self.last_scan_at = datetime.now(timezone.utc)
        self.faces_detected = len(candidates)
        self._next_full_scan = (
            time.monotonic() + self.settings.presence_scan_interval_seconds
        )
        if not candidates:
            self.target_yaw = None
            self.target_pitch = None
            self._target_score = None
            self._target_seen_at = None
            self._clear_target_metadata()
            await self._move(0.0, self.settings.presence_pitch_degrees)
            self.mode = "no_target"
            return

        best = max(candidates, key=lambda item: item.proximity_score)
        target_yaw = self._yaw_for_detection(best.scan_yaw, best.detection)
        target_pitch = self._pitch_for_detection(
            best.scan_pitch, best.detection
        )
        await self._move(target_yaw, target_pitch)
        self.target_yaw = target_yaw
        self.target_pitch = target_pitch
        self._target_score = best.proximity_score
        self._target_seen_at = datetime.now(timezone.utc)
        self._record_target(
            best.detection, best.scan_yaw, best.scan_pitch
        )
        self.mode = "tracking"

    async def _track_target(self) -> None:
        if self.target_yaw is None:
            return
        self.mode = "tracking"
        await self.gateway.send(MessageType.START_CAMERA_STREAM)
        try:
            await asyncio.sleep(0.08)
            self.gateway.clear_camera_frames()
            frames = await self._collect_frames()
        finally:
            await self.gateway.send(MessageType.STOP_CAMERA_STREAM)

        detections: list[FaceDetection] = []
        assert self._detector is not None
        for frame in frames:
            detections.extend(self._detector.detect(frame))
        if not self._can_track():
            self.mode = (
                "manual_override"
                if time.monotonic() < self._manual_override_until
                else "deferred"
            )
            return
        if not detections:
            if (
                self._target_seen_at is not None
                and (
                    datetime.now(timezone.utc) - self._target_seen_at
                ).total_seconds()
                >= self.settings.presence_target_lost_seconds
            ):
                self.target_yaw = None
                self.target_pitch = None
                self._target_score = None
                self._clear_target_metadata()
                await self._move(0.0, self.settings.presence_pitch_degrees)
                self.mode = "no_target"
            return

        best = self._select_tracking_face(detections)
        score = best.area * best.confidence
        self._target_score = score
        self._target_seen_at = datetime.now(timezone.utc)
        self._record_target(best, self._current_yaw, self._current_pitch)
        horizontal_centered = (
            abs(best.center_x - 0.5)
            < self.settings.presence_center_deadband
        )
        vertical_centered = (
            abs(best.center_y - self.settings.presence_vertical_center)
            < self.settings.presence_vertical_deadband
        )
        if horizontal_centered and vertical_centered:
            return
        target_yaw = self._yaw_for_detection(self._current_yaw, best)
        target_pitch = self._pitch_for_detection(self._current_pitch, best)
        yaw_step = max(
            -self.settings.presence_max_step_degrees,
            min(
                self.settings.presence_max_step_degrees,
                target_yaw - self._current_yaw,
            ),
        )
        pitch_step = max(
            -self.settings.presence_max_pitch_step_degrees,
            min(
                self.settings.presence_max_pitch_step_degrees,
                target_pitch - self._current_pitch,
            ),
        )
        await self._move(
            self._current_yaw + yaw_step,
            self._current_pitch + pitch_step,
        )
        self.target_yaw = self._current_yaw
        self.target_pitch = self._current_pitch

    async def _quick_reacquire(
        self, voice_evidence: ChildVoiceEvidence | None = None
    ) -> WakeIdentityEvidence | None:
        self.mode = "wake_searching"
        self.last_error = None
        starting_yaw = self._current_yaw
        starting_pitch = self._current_pitch
        search_poses = [(starting_yaw, starting_pitch)]
        search_poses.extend(
            (
                max(-45.0, min(45.0, starting_yaw + offset)),
                starting_pitch,
            )
            for offset in self.settings.presence_wake_search_yaw_offsets
        )
        search_poses.extend(
            (
                starting_yaw,
                max(0.0, min(45.0, starting_pitch + offset)),
            )
            for offset in self.settings.presence_wake_search_pitch_offsets
        )
        detected_frames: list[
            tuple[FaceDetection, bytes, float, float]
        ] = []
        body_guidance_used = False
        body_head_estimator = self._ensure_body_head_estimator()
        await self.gateway.send(MessageType.START_CAMERA_STREAM)
        try:
            for pose_index, (yaw, pitch) in enumerate(search_poses):
                if pose_index:
                    if self.voice_is_speaking():
                        break
                    await self._move(yaw, pitch)
                    await asyncio.sleep(
                        self.settings.presence_wake_search_settle_seconds
                    )
                else:
                    await asyncio.sleep(0.06)
                self.gateway.clear_camera_frames()
                frames = await self._collect_frames(
                    frame_limit=1,
                    timeout_seconds=(
                        self.settings.presence_wake_search_frame_timeout_seconds
                    ),
                )
                assert self._detector is not None
                for frame in frames:
                    faces = self._detector.detect(frame)
                    detected_frames.extend(
                        (detection, frame, yaw, pitch)
                        for detection in faces
                    )
                    if (
                        not faces
                        and not body_guidance_used
                        and body_head_estimator is not None
                    ):
                        head_hints = body_head_estimator.estimate_heads(frame)
                        if head_hints:
                            body_guidance_used = True
                            best_hint = max(
                                head_hints,
                                key=lambda item: (
                                    item.area * item.confidence
                                ),
                            )
                            detected_frames.extend(
                                await self._confirm_body_guided_face(
                                    best_hint, yaw, pitch
                                )
                            )
                if detected_frames:
                    break
        finally:
            await self.gateway.send(MessageType.STOP_CAMERA_STREAM)

        detections = [item[0] for item in detected_frames]
        self.last_wake_reacquire_at = datetime.now(timezone.utc)
        self.last_wake_reacquire_found = bool(detections)
        self.faces_detected = len(detections)
        if not detections:
            self.mode = "wake_no_target"
            self._next_full_scan = 0.0
            self.last_child_face = None
            self.last_estimated_age = None
            self.last_child_identity_confidence = None
            await self._smooth_move(starting_yaw, starting_pitch)
            if voice_evidence is None:
                return None
            return WakeIdentityEvidence(voice_evidence, None)

        best = self._select_tracking_face(detections)
        best_frame, scan_yaw, scan_pitch = next(
            (frame, yaw, pitch)
            for detection, frame, yaw, pitch in detected_frames
            if detection is best
        )
        face_evidence: ChildFaceEvidence | None = None
        age_classifier = self._ensure_age_classifier()
        if age_classifier is not None and voice_evidence is not None:
            try:
                face_evidence = age_classifier.classify(
                    best_frame,
                    center_x=best.center_x,
                    center_y=best.center_y,
                    width=best.width,
                    height=best.height,
                )
            except Exception as exc:
                logger.warning("child face classification failed: %s", exc)
        self.last_child_face = (
            face_evidence.is_child if face_evidence is not None else None
        )
        self.last_estimated_age = (
            face_evidence.estimated_age if face_evidence is not None else None
        )
        combined = (
            WakeIdentityEvidence(voice_evidence, face_evidence)
            if voice_evidence is not None
            else None
        )
        self.last_child_identity_confidence = (
            combined.confidence if combined is not None else None
        )
        target_yaw = self._yaw_for_detection(scan_yaw, best)
        target_pitch = self._pitch_for_detection(scan_pitch, best)
        await self._smooth_move(target_yaw, target_pitch)
        self.target_yaw = self._current_yaw
        self.target_pitch = self._current_pitch
        self._target_score = best.area * best.confidence
        self._target_seen_at = datetime.now(timezone.utc)
        self._record_target(best, scan_yaw, scan_pitch)
        self.mode = "wake_tracking"
        return combined

    async def _confirm_body_guided_face(
        self,
        head_hint: FaceDetection,
        scan_yaw: float,
        scan_pitch: float,
    ) -> list[tuple[FaceDetection, bytes, float, float]]:
        """Move toward an inferred head, then require a real face detection."""
        target_yaw = self._yaw_for_detection(scan_yaw, head_hint)
        target_pitch = self._pitch_for_detection(scan_pitch, head_hint)
        self.mode = "wake_body_guiding"
        self.body_guidance_count += 1
        self.last_body_guided_at = datetime.now(timezone.utc)
        await self._smooth_move(target_yaw, target_pitch)
        await asyncio.sleep(
            self.settings.presence_body_guidance_settle_seconds
        )
        self.gateway.clear_camera_frames()
        frames = await self._collect_frames(
            frame_limit=1,
            timeout_seconds=(
                self.settings.presence_wake_search_frame_timeout_seconds
            ),
        )
        assert self._detector is not None
        confirmed: list[tuple[FaceDetection, bytes, float, float]] = []
        for frame in frames:
            confirmed.extend(
                (face, frame, target_yaw, target_pitch)
                for face in self._detector.detect(frame)
            )
        self.mode = "wake_searching"
        return confirmed

    def _select_tracking_face(
        self, detections: list[FaceDetection]
    ) -> FaceDetection:
        nearest = max(
            detections, key=lambda item: item.area * item.confidence
        )
        if len(detections) == 1:
            return nearest
        incumbent = min(
            detections,
            key=lambda item: (
                (item.center_x - 0.5) ** 2
                + (
                    item.center_y
                    - self.settings.presence_vertical_center
                )
                ** 2
            ),
        )
        nearest_score = nearest.area * nearest.confidence
        incumbent_score = incumbent.area * incumbent.confidence
        if (
            nearest is not incumbent
            and nearest_score
            < incumbent_score * self.settings.presence_target_switch_ratio
        ):
            return incumbent
        return nearest

    def _record_target(
        self,
        detection: FaceDetection,
        scan_yaw: float,
        scan_pitch: float,
    ) -> None:
        self.target_center_x = round(detection.center_x, 3)
        self.target_center_y = round(detection.center_y, 3)
        self.target_area = round(detection.area, 4)
        self.target_scan_yaw = round(scan_yaw, 1)
        self.target_scan_pitch = round(scan_pitch, 1)

    def _clear_target_metadata(self) -> None:
        self.target_center_x = None
        self.target_center_y = None
        self.target_area = None
        self.target_scan_yaw = None
        self.target_scan_pitch = None

    async def _collect_frames(
        self,
        *,
        frame_limit: int | None = None,
        timeout_seconds: float | None = None,
    ) -> list[bytes]:
        frames: list[bytes] = []
        limit = frame_limit or self.settings.presence_frames_per_pose
        timeout = (
            timeout_seconds
            if timeout_seconds is not None
            else self.settings.presence_frame_timeout_seconds
        )
        deadline = time.monotonic() + timeout
        while len(frames) < limit:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            try:
                frames.append(await self.gateway.next_camera_frame(remaining))
            except asyncio.TimeoutError:
                break
        return frames

    async def _smooth_move(self, yaw: float, pitch: float) -> None:
        start_yaw = self._current_yaw
        start_pitch = self._current_pitch
        distance = max(abs(yaw - start_yaw), abs(pitch - start_pitch))
        step_size = max(1.0, self.settings.presence_wake_smooth_step_degrees)
        steps = max(1, math.ceil(distance / step_size))
        for step in range(1, steps + 1):
            fraction = step / steps
            await self._move(
                start_yaw + (yaw - start_yaw) * fraction,
                start_pitch + (pitch - start_pitch) * fraction,
            )
            if step < steps:
                await asyncio.sleep(
                    self.settings.presence_wake_smooth_step_seconds
                )

    async def _move(self, yaw: float, pitch: float | None = None) -> None:
        yaw = max(-45.0, min(45.0, yaw))
        pitch = self._current_pitch if pitch is None else pitch
        pitch = max(0.0, min(45.0, pitch))
        await self.gateway.send_json(
            MessageType.CONTROL_MOTION,
            {
                "yawServo": {
                    "angle": round(yaw * 10),
                    "speed": self.settings.presence_servo_speed,
                },
                "pitchServo": {
                    "angle": round(pitch * 10),
                    "speed": self.settings.presence_servo_speed,
                },
            },
        )
        self._current_yaw = yaw
        self._current_pitch = pitch

    def _yaw_for_detection(
        self, camera_yaw: float, detection: FaceDetection
    ) -> float:
        offset = (
            (detection.center_x - 0.5)
            * self.settings.presence_camera_horizontal_fov
            * self.settings.presence_yaw_direction
        )
        return round(max(-45.0, min(45.0, camera_yaw + offset)), 1)

    def _pitch_for_detection(
        self, camera_pitch: float, detection: FaceDetection
    ) -> float:
        offset = (
            (self.settings.presence_vertical_center - detection.center_y)
            * self.settings.presence_camera_vertical_fov
            * self.settings.presence_pitch_direction
        )
        return round(
            max(0.0, min(45.0, camera_pitch + offset)),
            1,
        )
