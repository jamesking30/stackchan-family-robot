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
