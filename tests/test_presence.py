import asyncio
from pathlib import Path

from stackchan_control.gateway import MessageType
from stackchan_control.child_identity import (
    ChildFaceEvidence,
    ChildVoiceEvidence,
)
from stackchan_control.presence import FaceDetection, PresenceTracker
from stackchan_control.settings import PROJECT_ROOT, Settings


class FakeGateway:
    def __init__(self):
        self.messages: list[tuple[MessageType, object]] = []

    async def is_online(self):
        return True

    async def send(self, message_type, payload=b""):
        self.messages.append((message_type, payload))

    async def send_json(self, message_type, payload):
        self.messages.append((message_type, payload))

    def clear_camera_frames(self):
        return None

    async def next_camera_frame(self, timeout):
        return b"camera-frame"


class SequenceDetector:
    def __init__(self, results):
        self.results = iter(results)

    def detect(self, jpeg):
        assert jpeg == b"camera-frame"
        return next(self.results, [])


class SequenceBodyHeadEstimator:
    def __init__(self, results):
        self.results = iter(results)

    def estimate_heads(self, jpeg):
        assert jpeg == b"camera-frame"
        return next(self.results, [])


class FakeChildAgeClassifier:
    def __init__(self, age: int):
        self.age = age

    def classify(self, jpeg, **kwargs):
        assert jpeg == b"camera-frame"
        return ChildFaceEvidence(
            is_child=self.age <= 11,
            score=0.9,
            estimated_age=self.age,
        )


def settings(tmp_path: Path, **overrides) -> Settings:
    values = {
        "db_path": tmp_path / "presence.db",
        "seed_character_dir": PROJECT_ROOT / "config" / "seed_character",
        "web_dir": PROJECT_ROOT / "web",
        "presence_enabled": True,
        "presence_frames_per_pose": 1,
        "presence_scan_yaw_degrees": (-20.0, 0.0, 20.0),
        "presence_scan_pitch_degrees": (10.0,),
        "presence_servo_settle_seconds": 0.0,
        "presence_frame_timeout_seconds": 0.01,
        "presence_camera_horizontal_fov": 60.0,
        "presence_body_guidance_enabled": False,
    }
    values.update(overrides)
    return Settings(**values)


def test_full_scan_selects_largest_face_and_centers_it(tmp_path: Path):
    gateway = FakeGateway()
    small = FaceDetection(0.5, 0.5, 0.15, 0.15, 0.9)
    nearest = FaceDetection(0.4, 0.5, 0.4, 0.4, 0.9)
    detector = SequenceDetector([[small], [], [nearest]])
    tracker = PresenceTracker(
        settings(tmp_path),
        gateway,  # type: ignore[arg-type]
        voice_mode=lambda: "waiting_for_wake_word",
        detector=detector,
    )

    state = asyncio.run(tracker.scan_now(force=True))

    assert state["mode"] == "tracking"
    assert state["faces_detected"] == 2
    assert state["target_yaw"] == 14.0
    assert state["target_pitch"] == 7.8
    assert gateway.messages[0][0] == MessageType.START_CAMERA_STREAM
    assert gateway.messages[-2][0] == MessageType.STOP_CAMERA_STREAM
    assert gateway.messages[-1][1] == {
        "yawServo": {"angle": 140, "speed": 120},
        "pitchServo": {"angle": 78, "speed": 120},
    }


def test_scan_returns_to_center_when_no_face_is_visible(tmp_path: Path):
    gateway = FakeGateway()
    detector = SequenceDetector([[], [], []])
    tracker = PresenceTracker(
        settings(tmp_path),
        gateway,  # type: ignore[arg-type]
        voice_mode=lambda: "waiting_for_wake_word",
        detector=detector,
    )

    state = asyncio.run(tracker.scan_now(force=True))

    assert state["mode"] == "no_target"
    assert state["target_yaw"] is None
    assert state["target_pitch"] is None
    assert state["current_yaw"] == 0.0


def test_manual_motion_suspends_automatic_scan(tmp_path: Path):
    gateway = FakeGateway()
    tracker = PresenceTracker(
        settings(tmp_path),
        gateway,  # type: ignore[arg-type]
        voice_mode=lambda: "waiting_for_wake_word",
        detector=SequenceDetector([]),
    )
    tracker.note_manual_override(12.0, 8.0)

    state = asyncio.run(tracker.scan_now())

    assert state["mode"] == "deferred"
    assert state["current_yaw"] == 12.0
    assert state["target_yaw"] is None
    assert gateway.messages == []


def test_no_target_waits_until_next_full_scan(tmp_path: Path):
    gateway = FakeGateway()
    tracker = PresenceTracker(
        settings(tmp_path, presence_tracking_interval_seconds=0.01),
        gateway,  # type: ignore[arg-type]
        voice_mode=lambda: "waiting_for_wake_word",
        detector=SequenceDetector([[], [], []]),
    )

    async def run():
        await tracker.scan_now(force=True)
        before = len(gateway.messages)
        tracker._loop_task = asyncio.create_task(tracker._run_loop())
        await asyncio.sleep(0.04)
        tracker._loop_task.cancel()
        try:
            await tracker._loop_task
        except asyncio.CancelledError:
            pass
        return before, len(gateway.messages)

    before, after = asyncio.run(run())

    assert before == after


def test_vertical_face_offset_adjusts_pitch(tmp_path: Path):
    tracker = PresenceTracker(
        settings(tmp_path),
        FakeGateway(),  # type: ignore[arg-type]
        voice_mode=lambda: "waiting_for_wake_word",
        detector=SequenceDetector([]),
    )
    face_above_center = FaceDetection(0.5, 0.25, 0.3, 0.3, 0.9)

    assert tracker._pitch_for_detection(10.0, face_above_center) == 19.0


def test_interrupted_scan_returns_to_starting_pose(tmp_path: Path):
    gateway = FakeGateway()
    modes = iter(
        ["waiting_for_wake_word", "waiting_for_wake_word", "speaking"]
    )
    tracker = PresenceTracker(
        settings(tmp_path),
        gateway,  # type: ignore[arg-type]
        voice_mode=lambda: next(modes, "speaking"),
        detector=SequenceDetector([]),
    )

    state = asyncio.run(tracker.scan_now())

    assert state["mode"] == "deferred"
    assert state["current_yaw"] == 0.0
    assert state["current_pitch"] == 10.0
    assert gateway.messages[-1][1] == {
        "yawServo": {"angle": 0, "speed": 120},
        "pitchServo": {"angle": 100, "speed": 120},
    }


def test_queued_scan_rechecks_deadline_after_lock(tmp_path: Path):
    gateway = FakeGateway()
    tracker = PresenceTracker(
        settings(tmp_path),
        gateway,  # type: ignore[arg-type]
        voice_mode=lambda: "waiting_for_wake_word",
        detector=SequenceDetector([]),
    )

    async def run():
        await tracker._operation_lock.acquire()
        queued = asyncio.create_task(tracker.scan_now())
        await asyncio.sleep(0)
        tracker._next_full_scan = 10**12
        tracker._operation_lock.release()
        await queued

    asyncio.run(run())

    assert gateway.messages == []


def test_tracking_keeps_centered_face_until_new_face_is_clearly_nearer(
    tmp_path: Path,
):
    tracker = PresenceTracker(
        settings(tmp_path),
        FakeGateway(),  # type: ignore[arg-type]
        voice_mode=lambda: "waiting_for_wake_word",
        detector=SequenceDetector([]),
    )
    incumbent = FaceDetection(0.5, 0.45, 0.30, 0.30, 0.9)
    slightly_larger = FaceDetection(0.8, 0.45, 0.32, 0.32, 0.9)
    clearly_larger = FaceDetection(0.8, 0.45, 0.40, 0.40, 0.9)

    assert (
        tracker._select_tracking_face([incumbent, slightly_larger])
        is incumbent
    )
    assert (
        tracker._select_tracking_face([incumbent, clearly_larger])
        is clearly_larger
    )


def test_wake_reacquire_smoothly_centers_visible_face(
    tmp_path: Path,
):
    gateway = FakeGateway()
    face = FaceDetection(0.8, 0.2, 0.35, 0.35, 0.9)
    tracker = PresenceTracker(
        settings(tmp_path),
        gateway,  # type: ignore[arg-type]
        voice_mode=lambda: "listening",
        detector=SequenceDetector([[face]]),
    )

    asyncio.run(tracker.reacquire_after_wake())
    state = tracker.snapshot()

    assert state["mode"] == "wake_tracking"
    assert state["last_wake_reacquire_found"] is True
    assert state["current_yaw"] == 18.0
    assert state["current_pitch"] == 21.2


def test_wake_reacquire_schedules_full_scan_when_current_view_is_empty(
    tmp_path: Path,
):
    tracker = PresenceTracker(
        settings(tmp_path),
        FakeGateway(),  # type: ignore[arg-type]
        voice_mode=lambda: "listening",
        detector=SequenceDetector([[]]),
    )
    tracker._next_full_scan = 10**12

    asyncio.run(tracker.reacquire_after_wake())

    assert tracker.mode == "wake_no_target"
    assert tracker.last_wake_reacquire_found is False
    assert tracker._next_full_scan == 0.0


def test_wake_reacquire_searches_nearby_pose_before_deferring(
    tmp_path: Path,
):
    face = FaceDetection(0.5, 0.45, 0.35, 0.35, 0.9)
    tracker = PresenceTracker(
        settings(
            tmp_path,
            presence_wake_search_yaw_offsets=(-18.0,),
            presence_wake_search_pitch_offsets=(),
        ),
        FakeGateway(),  # type: ignore[arg-type]
        voice_mode=lambda: "speaking",
        detector=SequenceDetector([[], [face]]),
    )

    asyncio.run(tracker.reacquire_after_wake())

    assert tracker.mode == "wake_tracking"
    assert tracker.last_wake_reacquire_found is True
    assert tracker.snapshot()["current_yaw"] == -18.0


def test_wake_reacquire_uses_body_to_preposition_then_confirms_face(
    tmp_path: Path,
):
    gateway = FakeGateway()
    inferred_head = FaceDetection(0.75, -0.10, 0.18, 0.23, 0.75)
    confirmed_face = FaceDetection(0.50, 0.45, 0.30, 0.30, 0.9)
    tracker = PresenceTracker(
        settings(
            tmp_path,
            presence_body_guidance_enabled=True,
            presence_body_guidance_settle_seconds=0.0,
            presence_wake_search_yaw_offsets=(),
            presence_wake_search_pitch_offsets=(),
        ),
        gateway,  # type: ignore[arg-type]
        voice_mode=lambda: "listening",
        detector=SequenceDetector([[], [confirmed_face]]),
        body_head_estimator=SequenceBodyHeadEstimator([[inferred_head]]),
    )

    asyncio.run(tracker.reacquire_after_wake())
    state = tracker.snapshot()

    assert state["mode"] == "wake_tracking"
    assert state["last_wake_reacquire_found"] is True
    assert state["body_guidance_count"] == 1
    assert state["current_yaw"] == 15.0
    assert state["current_pitch"] == 34.8


def test_body_hint_never_counts_as_a_face_without_confirmation(
    tmp_path: Path,
):
    inferred_head = FaceDetection(0.75, -0.10, 0.18, 0.23, 0.75)
    tracker = PresenceTracker(
        settings(
            tmp_path,
            presence_body_guidance_enabled=True,
            presence_body_guidance_settle_seconds=0.0,
            presence_wake_search_yaw_offsets=(),
            presence_wake_search_pitch_offsets=(),
        ),
        FakeGateway(),  # type: ignore[arg-type]
        voice_mode=lambda: "listening",
        detector=SequenceDetector([[], []]),
        body_head_estimator=SequenceBodyHeadEstimator([[inferred_head]]),
    )

    asyncio.run(tracker.reacquire_after_wake())
    state = tracker.snapshot()

    assert state["mode"] == "wake_no_target"
    assert state["last_wake_reacquire_found"] is False
    assert state["faces_detected"] == 0
    assert state["body_guidance_count"] == 1


def test_wake_reacquire_confirms_child_only_with_both_modalities(
    tmp_path: Path,
):
    face = FaceDetection(0.5, 0.45, 0.35, 0.35, 0.9)
    tracker = PresenceTracker(
        settings(tmp_path, child_identity_enabled=True),
        FakeGateway(),  # type: ignore[arg-type]
        voice_mode=lambda: "listening",
        detector=SequenceDetector([[face]]),
        age_classifier=FakeChildAgeClassifier(7),
    )
    voice = ChildVoiceEvidence(True, 0.85, 310.0, 0.6, 900)

    identity = asyncio.run(tracker.reacquire_after_wake(voice))

    assert identity is not None
    assert identity.confirmed_child is True
    assert identity.confidence == 0.85
    assert tracker.snapshot()["last_estimated_age"] == 7


def test_wake_reacquire_rejects_adult_voice_even_with_child_face(
    tmp_path: Path,
):
    face = FaceDetection(0.5, 0.45, 0.35, 0.35, 0.9)
    tracker = PresenceTracker(
        settings(tmp_path, child_identity_enabled=True),
        FakeGateway(),  # type: ignore[arg-type]
        voice_mode=lambda: "listening",
        detector=SequenceDetector([[face]]),
        age_classifier=FakeChildAgeClassifier(7),
    )
    voice = ChildVoiceEvidence(False, 0.2, 170.0, 0.6, 900)

    identity = asyncio.run(tracker.reacquire_after_wake(voice))

    assert identity is not None
    assert identity.confirmed_child is False
