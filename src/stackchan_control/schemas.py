from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator


UserRole = Literal["adult", "child", "unassigned", "guest"]
MemoryStatus = Literal["active", "pending_review", "deleted"]
TaskStatus = Literal[
    "queued", "running", "waiting", "completed", "failed", "cancelled"
]


class UserProfile(BaseModel):
    user_id: str
    display_name: str
    role: UserRole
    locale: str = "zh-CN"
    is_admin: bool = False
    face_profile_id: str | None = None
    enabled: bool = True


class UserUpdate(BaseModel):
    display_name: str | None = Field(default=None, min_length=1, max_length=80)
    role: UserRole | None = None
    locale: str | None = Field(default=None, pattern=r"^[a-z]{2}-[A-Z]{2}$")
    is_admin: bool | None = None
    face_profile_id: str | None = Field(default=None, max_length=120)
    enabled: bool | None = None


class CharacterVersion(BaseModel):
    character_id: str
    version: str
    parent_version: str | None
    documents: dict[str, str]
    created_by: str
    reason: str
    created_at: datetime
    active: bool


class CharacterVersionCreate(BaseModel):
    base_version: str
    patch: dict[str, str]
    reason: str = Field(min_length=3, max_length=240)
    actor: str = Field(default="admin", min_length=1, max_length=80)
    activate: bool = True

    @field_validator("patch")
    @classmethod
    def patch_must_not_be_empty(cls, value: dict[str, str]) -> dict[str, str]:
        if not value:
            raise ValueError("patch must contain at least one document")
        return value


class RollbackRequest(BaseModel):
    target_version: str | None = None
    actor: str = Field(default="admin", min_length=1, max_length=80)
    reason: str = Field(default="manual rollback", min_length=3, max_length=240)


class PromptPreview(BaseModel):
    character_id: str
    version: str
    system_prompt: str
    presentation: dict[str, object]


class MemoryCreate(BaseModel):
    namespace: Literal["profile", "preference", "episode", "relationship", "note"]
    content: str = Field(min_length=1, max_length=2000)
    source: Literal[
        "user_confirmed", "adult_confirmed", "assistant_inference", "imported"
    ] = "user_confirmed"
    sensitivity: Literal["normal", "sensitive"] = "normal"
    importance: float = Field(default=0.5, ge=0, le=1)


class MemoryItem(BaseModel):
    memory_id: str
    user_id: str
    namespace: str
    content: str
    source: str
    sensitivity: str
    status: MemoryStatus
    importance: float
    created_at: datetime
    updated_at: datetime


class TaskReport(BaseModel):
    task_id: str = Field(min_length=1, max_length=120)
    source: Literal["codex", "openclaw", "system"]
    title: str = Field(min_length=1, max_length=160)
    status: TaskStatus
    progress: float = Field(default=0, ge=0, le=1)
    summary: str = Field(default="", max_length=500)
    display_emotion: str | None = Field(default=None, max_length=40)


class TaskItem(TaskReport):
    updated_at: datetime


class DisplayState(BaseModel):
    mode: Literal["idle", "task", "attention"]
    emotion: str
    title: str
    subtitle: str
    progress: float | None = None
    source: str | None = None
    task_id: str | None = None


class DeviceState(BaseModel):
    device_id: str
    online: bool
    connected_at: datetime | None = None
    last_seen: datetime | None = None
    frames_received: int = 0
    frames_sent: int = 0
    last_message_type: int | None = None


class PresenceStateResponse(BaseModel):
    enabled: bool
    mode: str
    faces_detected: int
    target_yaw: float | None = None
    target_pitch: float | None = None
    target_center_x: float | None = None
    target_center_y: float | None = None
    target_area: float | None = None
    target_scan_yaw: float | None = None
    target_scan_pitch: float | None = None
    current_yaw: float
    current_pitch: float
    last_scan_at: datetime | None = None
    last_wake_reacquire_at: datetime | None = None
    last_wake_reacquire_found: bool | None = None
    last_sound_direction: float | None = None
    last_sound_direction_confidence: float | None = None
    last_sound_direction_at: datetime | None = None
    body_guidance_enabled: bool = False
    body_guidance_available: bool = False
    body_guidance_count: int = 0
    last_body_guided_at: datetime | None = None
    last_child_face: bool | None = None
    last_estimated_age: int | None = None
    last_child_identity_confidence: float | None = None
    target_seen_at: datetime | None = None
    manual_override_seconds: float
    camera_frames_persisted: bool
    error: str | None = None


class RobotMotionCommand(BaseModel):
    yaw_degrees: float = Field(ge=-45, le=45)
    pitch_degrees: float = Field(ge=0, le=45)
    speed: int = Field(default=150, ge=100, le=500)


class RobotExpressionCommand(BaseModel):
    emotion: Literal["neutral", "happy", "angry", "sad", "doubt", "sleepy"]
    mouth_weight: int | None = Field(default=None, ge=0, le=100)


class RobotAvatarCommand(BaseModel):
    emotion: Literal[
        "neutral",
        "listening",
        "thinking",
        "doubt",
        "happy",
        "excited",
        "concerned",
        "angry",
    ]


class RobotTextCommand(BaseModel):
    name: str = Field(default="家庭助手", min_length=1, max_length=40)
    content: str = Field(min_length=1, max_length=240)


class VoiceStartRequest(BaseModel):
    user_id: str = Field(default="user-2", min_length=1, max_length=80)


class VoiceTextTurn(BaseModel):
    transcript: str = Field(min_length=1, max_length=1000)


class VoiceStateResponse(BaseModel):
    mode: Literal[
        "stopped",
        "waiting_for_wake_word",
        "listening",
        "transcribing",
        "thinking",
        "speaking",
        "error",
    ]
    enabled: bool
    user_id: str
    turn_id: int
    wake_word: str = ""
    awake: bool = False
    last_wake_keyword: str | None = None
    wake_detected_at: datetime | None = None
    wake_detection_count: int = 0
    last_wake_child_voice: bool | None = None
    last_wake_pitch_hz: float | None = None
    last_wake_voiced_ratio: float | None = None
    kws_processed_frames: int = 0
    kws_input_rms: int = 0
    kws_input_peak_rms: int = 0
    kws_applied_gain: float = 1.0
    last_heard_transcript: str | None = None
    speaker_identity: str | None = None
    speaker_identity_confidence: float | None = None
    speaker_identity_reason: str | None = None
    speaker_identity_at: datetime | None = None
    transcript: str | None = None
    response_text: str | None = None
    error: str | None = None
    audio_rms: int = 0
    audio_peak_rms: int = 0
    device_vad_available: bool = False
    device_vad_speaking: bool = False
    suppressed_background_frames: int = 0
    silero_vad_ready: bool = False
    silero_vad_speaking: bool = False
    endpoint_reason: str | None = None
    playback_packets: int = 0
    playback_segments: int = 0
    playback_rebuffers: int = 0
    playback_avatar_frames: int = 0
    playback_max_packet_gap_ms: float = 0.0
    latency_ms: dict[str, float] = Field(default_factory=dict)
    updated_at: datetime
