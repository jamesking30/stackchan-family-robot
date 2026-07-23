from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Protocol

import cv2
import mediapipe as mp
import numpy as np

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


class PresenceTracker:
    """Periodically scan for the nearest visible face and turn StackChan toward it."""

    def __init__(
        self,
        settings: Settings,
        gateway: StackChanGateway,
        voice_mode: Callable[[], str],
        detector: FaceDetector | None = None,
    ) -> None:
        self.settings = settings
        self.gateway = gateway
        self.voice_mode = voice_mode
        self._detector = detector
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

    async def reacquire_after_wake(self) -> None:
        if (
            not self.enabled
            or self._wake_reacquire_lock.locked()
            or time.monotonic() < self._manual_override_until
        ):
            return
        async with self._wake_reacquire_lock:
            deadline = time.monotonic() + 12.0
            while self.voice_mode() not in {
                "listening",
                "waiting_for_wake_word",
                "stopped",
            }:
                if time.monotonic() >= deadline:
                    self.mode = "wake_deferred"
                    self._next_full_scan = 0.0
                    return
                await asyncio.sleep(0.1)
            if (
                not await self.gateway.is_online()
                or time.monotonic() < self._manual_override_until
            ):
                return
            async with self._operation_lock:
                await self._quick_reacquire()

    async def scan_now(self, *, force: bool = False) -> dict[str, object]:
        if not self.enabled:
            return self.snapshot()
        self._ensure_detector()
        if not force and not self._can_move():
            self.mode = "deferred"
            return self.snapshot()
        async with self._operation_lock:
            if not force and (
                not self._can_move()
                or time.monotonic() < self._next_full_scan
            ):
                return self.snapshot()
            await self._full_scan(force=force)
        return self.snapshot()

    async def _run_loop(self) -> None:
        try:
            while self.enabled and await self.gateway.is_online():
                now = time.monotonic()
                if self._can_move():
                    if now >= self._next_full_scan:
                        await self.scan_now()
                    elif self.target_yaw is not None:
                        async with self._operation_lock:
                            await self._track_target()
                await asyncio.sleep(self.settings.presence_tracking_interval_seconds)
        except asyncio.CancelledError:
            raise
        except DeviceOfflineError:
            self.mode = "offline"
        except Exception as exc:
            logger.exception("presence tracking failed")
            self.mode = "error"
            self.last_error = str(exc)[:240]

    def _can_move(self) -> bool:
        return (
            self.voice_mode() in {"waiting_for_wake_word", "stopped"}
            and time.monotonic() >= self._manual_override_until
        )

    def _ensure_detector(self) -> None:
        if self._detector is None:
            self._detector = MediaPipeFaceDetector(
                self.settings.presence_face_model,
                self.settings.presence_min_confidence,
            )

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
                    if not force and not self._can_move():
                        aborted = True
                        break
                    await self._move(yaw, pitch)
                    await asyncio.sleep(
                        self.settings.presence_servo_settle_seconds
                    )
                    if not force and not self._can_move():
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
        if not self._can_move():
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

    async def _quick_reacquire(self) -> None:
        self.mode = "wake_reacquire"
        self.last_error = None
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
        self.last_wake_reacquire_at = datetime.now(timezone.utc)
        self.last_wake_reacquire_found = bool(detections)
        self.faces_detected = len(detections)
        if not detections:
            self.mode = "wake_no_target"
            self._next_full_scan = 0.0
            return

        best = self._select_tracking_face(detections)
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
        self._target_score = best.area * best.confidence
        self._target_seen_at = datetime.now(timezone.utc)
        self._record_target(best, self._current_yaw, self._current_pitch)
        self.mode = "wake_tracking"

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

    async def _collect_frames(self) -> list[bytes]:
        frames: list[bytes] = []
        deadline = time.monotonic() + self.settings.presence_frame_timeout_seconds
        while len(frames) < self.settings.presence_frames_per_pose:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            try:
                frames.append(await self.gateway.next_camera_frame(remaining))
            except asyncio.TimeoutError:
                break
        return frames

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
