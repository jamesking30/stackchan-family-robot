from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml


ALLOWED_CHARACTER_DOCUMENTS = {
    "manifest",
    "safety",
    "character",
    "response_instructions",
    "tool_policy",
    "emotion_map",
    "gesture_map",
    "voice",
    "vocabulary",
    "examples",
}

SEED_FILE_MAP = {
    "manifest": "manifest.yaml",
    "safety": "safety.md",
    "character": "character.md",
    "response_instructions": "response_instructions.md",
    "tool_policy": "tool_policy.yaml",
    "emotion_map": "emotion_map.yaml",
    "gesture_map": "gesture_map.yaml",
    "voice": "voice.yaml",
    "vocabulary": "vocabulary.yaml",
    "examples": "examples.md",
}


class RepositoryError(RuntimeError):
    pass


class NotFoundError(RepositoryError):
    pass


class ConflictError(RepositoryError):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def row_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(row) if row is not None else None


class RobotRepository:
    def __init__(self, db_path: Path, seed_character_dir: Path):
        self.db_path = db_path
        self.seed_character_dir = seed_character_dir
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.initialize()

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        return connection

    def initialize(self) -> None:
        with self.connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS users (
                    user_id TEXT PRIMARY KEY,
                    display_name TEXT NOT NULL,
                    role TEXT NOT NULL,
                    locale TEXT NOT NULL,
                    is_admin INTEGER NOT NULL DEFAULT 0,
                    face_profile_id TEXT,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS character_versions (
                    version TEXT PRIMARY KEY,
                    character_id TEXT NOT NULL,
                    parent_version TEXT,
                    documents_json TEXT NOT NULL,
                    created_by TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    active INTEGER NOT NULL DEFAULT 0
                );
                CREATE UNIQUE INDEX IF NOT EXISTS one_active_character
                    ON character_versions(character_id) WHERE active = 1;

                CREATE TABLE IF NOT EXISTS memories (
                    memory_id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL REFERENCES users(user_id),
                    namespace TEXT NOT NULL,
                    content TEXT NOT NULL,
                    source TEXT NOT NULL,
                    sensitivity TEXT NOT NULL,
                    status TEXT NOT NULL,
                    importance REAL NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS memories_by_user
                    ON memories(user_id, status, updated_at DESC);

                CREATE TABLE IF NOT EXISTS agent_tasks (
                    task_id TEXT PRIMARY KEY,
                    source TEXT NOT NULL,
                    title TEXT NOT NULL,
                    status TEXT NOT NULL,
                    progress REAL NOT NULL,
                    summary TEXT NOT NULL,
                    display_emotion TEXT,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS audit_log (
                    audit_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    actor TEXT NOT NULL,
                    action TEXT NOT NULL,
                    target TEXT NOT NULL,
                    details_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                """
            )
            self._seed_users(db)
            self._seed_character(db)

    def _seed_users(self, db: sqlite3.Connection) -> None:
        now = utc_now()
        profiles = [
            ("user-1", "家庭成员 1", "adult", "zh-CN", 1),
            ("user-2", "家庭成员 2", "unassigned", "zh-CN", 0),
            ("user-3", "家庭成员 3", "unassigned", "zh-CN", 0),
            ("user-4", "六六", "child", "zh-CN", 0),
        ]
        db.executemany(
            """
            INSERT OR IGNORE INTO users
            (user_id, display_name, role, locale, is_admin, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            [(*profile, now, now) for profile in profiles],
        )

    def _seed_character(self, db: sqlite3.Connection) -> None:
        existing = db.execute(
            "SELECT 1 FROM character_versions WHERE character_id = ? LIMIT 1",
            ("family-companion",),
        ).fetchone()
        if existing:
            return
        documents = {
            name: (self.seed_character_dir / filename).read_text(encoding="utf-8")
            for name, filename in SEED_FILE_MAP.items()
        }
        self.validate_documents(documents)
        version = self._version_id(documents)
        now = utc_now()
        db.execute(
            """
            INSERT INTO character_versions
            (version, character_id, parent_version, documents_json, created_by,
             reason, created_at, active)
            VALUES (?, ?, NULL, ?, ?, ?, ?, 1)
            """,
            (
                version,
                "family-companion",
                json.dumps(documents, ensure_ascii=False),
                "system",
                "initial child-safe bilingual character",
                now,
            ),
        )

    @staticmethod
    def _version_id(documents: dict[str, str]) -> str:
        payload = json.dumps(documents, ensure_ascii=False, sort_keys=True).encode()
        digest = hashlib.sha256(payload).hexdigest()[:10]
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S%f")
        return f"v-{stamp}-{digest}"

    @staticmethod
    def validate_documents(documents: dict[str, str]) -> None:
        unknown = set(documents) - ALLOWED_CHARACTER_DOCUMENTS
        if unknown:
            raise RepositoryError(f"unknown character documents: {sorted(unknown)}")
        required = {"manifest", "safety", "character", "response_instructions"}
        missing = [key for key in required if not documents.get(key, "").strip()]
        if missing:
            raise RepositoryError(f"required documents are empty: {missing}")
        for key in ("manifest", "tool_policy", "emotion_map", "gesture_map", "voice", "vocabulary"):
            if key in documents:
                try:
                    parsed = yaml.safe_load(documents[key])
                except yaml.YAMLError as exc:
                    raise RepositoryError(f"invalid YAML in {key}: {exc}") from exc
                if not isinstance(parsed, dict):
                    raise RepositoryError(f"{key} must contain a YAML mapping")

    def audit(self, db: sqlite3.Connection, actor: str, action: str, target: str, details: dict[str, Any]) -> None:
        db.execute(
            "INSERT INTO audit_log(actor, action, target, details_json, created_at) VALUES (?, ?, ?, ?, ?)",
            (actor, action, target, json.dumps(details, ensure_ascii=False), utc_now()),
        )

    def list_users(self) -> list[dict[str, Any]]:
        with self.connect() as db:
            rows = db.execute("SELECT * FROM users ORDER BY user_id").fetchall()
        return [self._user_from_row(row) for row in rows]

    def get_user(self, user_id: str) -> dict[str, Any]:
        with self.connect() as db:
            row = db.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)).fetchone()
        if not row:
            raise NotFoundError(f"unknown user: {user_id}")
        return self._user_from_row(row)

    @staticmethod
    def _user_from_row(row: sqlite3.Row) -> dict[str, Any]:
        item = dict(row)
        item["is_admin"] = bool(item["is_admin"])
        item["enabled"] = bool(item["enabled"])
        item.pop("created_at", None)
        item.pop("updated_at", None)
        return item

    def update_user(self, user_id: str, changes: dict[str, Any], actor: str = "admin") -> dict[str, Any]:
        allowed = {"display_name", "role", "locale", "is_admin", "face_profile_id", "enabled"}
        updates = {key: value for key, value in changes.items() if key in allowed and value is not None}
        if not updates:
            return self.get_user(user_id)
        if "is_admin" in updates:
            updates["is_admin"] = int(updates["is_admin"])
        if "enabled" in updates:
            updates["enabled"] = int(updates["enabled"])
        updates["updated_at"] = utc_now()
        assignments = ", ".join(f"{key} = ?" for key in updates)
        with self.connect() as db:
            result = db.execute(
                f"UPDATE users SET {assignments} WHERE user_id = ?",
                [*updates.values(), user_id],
            )
            if result.rowcount == 0:
                raise NotFoundError(f"unknown user: {user_id}")
            self.audit(db, actor, "user.update", user_id, changes)
        return self.get_user(user_id)

    def get_character(self, character_id: str = "family-companion") -> dict[str, Any]:
        with self.connect() as db:
            row = db.execute(
                "SELECT * FROM character_versions WHERE character_id = ? AND active = 1",
                (character_id,),
            ).fetchone()
        if not row:
            raise NotFoundError(f"no active version for character: {character_id}")
        return self._character_from_row(row)

    def get_character_version(self, character_id: str, version: str) -> dict[str, Any]:
        with self.connect() as db:
            row = db.execute(
                "SELECT * FROM character_versions WHERE character_id = ? AND version = ?",
                (character_id, version),
            ).fetchone()
        if not row:
            raise NotFoundError(f"unknown character version: {version}")
        return self._character_from_row(row)

    @staticmethod
    def _character_from_row(row: sqlite3.Row) -> dict[str, Any]:
        item = dict(row)
        item["documents"] = json.loads(item.pop("documents_json"))
        item["active"] = bool(item["active"])
        return item

    def create_character_version(
        self,
        character_id: str,
        base_version: str,
        patch: dict[str, str],
        reason: str,
        actor: str,
        activate: bool,
    ) -> dict[str, Any]:
        unknown = set(patch) - ALLOWED_CHARACTER_DOCUMENTS
        if unknown:
            raise RepositoryError(f"documents cannot be changed: {sorted(unknown)}")
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            active_row = db.execute(
                "SELECT * FROM character_versions WHERE character_id = ? AND active = 1",
                (character_id,),
            ).fetchone()
            if not active_row:
                raise NotFoundError(f"unknown character: {character_id}")
            if active_row["version"] != base_version:
                raise ConflictError(
                    f"base version {base_version} is stale; active version is {active_row['version']}"
                )
            documents = json.loads(active_row["documents_json"])
            documents.update(patch)
            self.validate_documents(documents)
            version = self._version_id(documents)
            if activate:
                db.execute(
                    "UPDATE character_versions SET active = 0 WHERE character_id = ?",
                    (character_id,),
                )
            db.execute(
                """
                INSERT INTO character_versions
                (version, character_id, parent_version, documents_json, created_by,
                 reason, created_at, active)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    version,
                    character_id,
                    base_version,
                    json.dumps(documents, ensure_ascii=False),
                    actor,
                    reason,
                    utc_now(),
                    int(activate),
                ),
            )
            self.audit(
                db,
                actor,
                "character.version.create",
                character_id,
                {"version": version, "base_version": base_version, "changed": sorted(patch), "activate": activate},
            )
        return self.get_character_version(character_id, version)

    def rollback_character(
        self,
        character_id: str,
        target_version: str | None,
        actor: str,
        reason: str,
    ) -> dict[str, Any]:
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            active = db.execute(
                "SELECT * FROM character_versions WHERE character_id = ? AND active = 1",
                (character_id,),
            ).fetchone()
            if not active:
                raise NotFoundError(f"unknown character: {character_id}")
            destination = target_version or active["parent_version"]
            if not destination:
                raise ConflictError("the active version has no parent to roll back to")
            target = db.execute(
                "SELECT * FROM character_versions WHERE character_id = ? AND version = ?",
                (character_id, destination),
            ).fetchone()
            if not target:
                raise NotFoundError(f"unknown character version: {destination}")
            db.execute("UPDATE character_versions SET active = 0 WHERE character_id = ?", (character_id,))
            db.execute("UPDATE character_versions SET active = 1 WHERE version = ?", (destination,))
            self.audit(
                db,
                actor,
                "character.rollback",
                character_id,
                {"from": active["version"], "to": destination, "reason": reason},
            )
        return self.get_character(character_id)

    def prompt_preview(self, character_id: str = "family-companion") -> dict[str, Any]:
        character = self.get_character(character_id)
        documents = character["documents"]
        ordered = ["safety", "character", "response_instructions", "tool_policy", "vocabulary", "examples"]
        sections = [documents[key].strip() for key in ordered if documents.get(key, "").strip()]
        presentation: dict[str, Any] = {}
        for key in ("voice", "emotion_map", "gesture_map"):
            if documents.get(key):
                presentation[key] = yaml.safe_load(documents[key])
        return {
            "character_id": character_id,
            "version": character["version"],
            "system_prompt": "\n\n---\n\n".join(sections),
            "presentation": presentation,
        }

    def add_memory(
        self,
        user_id: str,
        namespace: str,
        content: str,
        source: str,
        sensitivity: str,
        importance: float,
    ) -> dict[str, Any]:
        user = self.get_user(user_id)
        status = "active"
        if source == "assistant_inference" and user["role"] in {"child", "unassigned"}:
            status = "pending_review"
        memory_id = f"mem-{uuid.uuid4().hex}"
        now = utc_now()
        with self.connect() as db:
            db.execute(
                """
                INSERT INTO memories
                (memory_id, user_id, namespace, content, source, sensitivity,
                 status, importance, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (memory_id, user_id, namespace, content, source, sensitivity, status, importance, now, now),
            )
            self.audit(db, source, "memory.create", memory_id, {"user_id": user_id, "status": status})
        return self.get_memory(user_id, memory_id)

    def get_memory(self, user_id: str, memory_id: str) -> dict[str, Any]:
        with self.connect() as db:
            row = db.execute(
                "SELECT * FROM memories WHERE user_id = ? AND memory_id = ?",
                (user_id, memory_id),
            ).fetchone()
        if not row:
            raise NotFoundError(f"unknown memory for user {user_id}: {memory_id}")
        return dict(row)

    def list_memories(
        self, user_id: str, query: str | None = None, include_pending: bool = True
    ) -> list[dict[str, Any]]:
        self.get_user(user_id)
        clauses = ["user_id = ?", "status != 'deleted'"]
        values: list[Any] = [user_id]
        if not include_pending:
            clauses.append("status = 'active'")
        if query:
            clauses.append("content LIKE ?")
            values.append(f"%{query}%")
        sql = f"SELECT * FROM memories WHERE {' AND '.join(clauses)} ORDER BY importance DESC, updated_at DESC"
        with self.connect() as db:
            rows = db.execute(sql, values).fetchall()
        return [dict(row) for row in rows]

    def forget_memory(self, user_id: str, memory_id: str, actor: str) -> None:
        with self.connect() as db:
            result = db.execute(
                "UPDATE memories SET status = 'deleted', updated_at = ? WHERE user_id = ? AND memory_id = ? AND status != 'deleted'",
                (utc_now(), user_id, memory_id),
            )
            if result.rowcount == 0:
                raise NotFoundError(f"unknown active memory for user {user_id}: {memory_id}")
            self.audit(db, actor, "memory.forget", memory_id, {"user_id": user_id})

    def approve_memory(self, user_id: str, memory_id: str, actor: str) -> dict[str, Any]:
        with self.connect() as db:
            result = db.execute(
                "UPDATE memories SET status = 'active', updated_at = ? WHERE user_id = ? AND memory_id = ? AND status = 'pending_review'",
                (utc_now(), user_id, memory_id),
            )
            if result.rowcount == 0:
                raise NotFoundError(
                    f"unknown pending memory for user {user_id}: {memory_id}"
                )
            self.audit(db, actor, "memory.approve", memory_id, {"user_id": user_id})
        return self.get_memory(user_id, memory_id)

    def report_task(self, task: dict[str, Any]) -> dict[str, Any]:
        now = utc_now()
        with self.connect() as db:
            db.execute(
                """
                INSERT INTO agent_tasks
                (task_id, source, title, status, progress, summary, display_emotion, updated_at)
                VALUES (:task_id, :source, :title, :status, :progress, :summary, :display_emotion, :updated_at)
                ON CONFLICT(task_id) DO UPDATE SET
                    source = excluded.source,
                    title = excluded.title,
                    status = excluded.status,
                    progress = excluded.progress,
                    summary = excluded.summary,
                    display_emotion = excluded.display_emotion,
                    updated_at = excluded.updated_at
                """,
                {**task, "updated_at": now},
            )
        return self.get_task(task["task_id"])

    def get_task(self, task_id: str) -> dict[str, Any]:
        with self.connect() as db:
            row = db.execute("SELECT * FROM agent_tasks WHERE task_id = ?", (task_id,)).fetchone()
        if not row:
            raise NotFoundError(f"unknown task: {task_id}")
        return dict(row)

    def list_tasks(self, limit: int = 20) -> list[dict[str, Any]]:
        with self.connect() as db:
            rows = db.execute(
                "SELECT * FROM agent_tasks ORDER BY updated_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(row) for row in rows]

    def display_state(self) -> dict[str, Any]:
        with self.connect() as db:
            task = db.execute(
                """
                SELECT * FROM agent_tasks
                ORDER BY
                  CASE status WHEN 'running' THEN 0 WHEN 'waiting' THEN 1 WHEN 'queued' THEN 2 ELSE 3 END,
                  updated_at DESC
                LIMIT 1
                """
            ).fetchone()
        if not task:
            return {"mode": "idle", "emotion": "neutral", "title": "爱莉已就绪", "subtitle": "Aili is ready", "progress": None, "source": None, "task_id": None}
        item = dict(task)
        if item["status"] in {"running", "waiting", "queued"}:
            default_emotion = "thinking" if item["status"] != "waiting" else "concerned"
            return {
                "mode": "task",
                "emotion": item["display_emotion"] or default_emotion,
                "title": item["title"],
                "subtitle": item["summary"] or f"{item['source']} · {item['status']}",
                "progress": item["progress"],
                "source": item["source"],
                "task_id": item["task_id"],
            }
        emotion = "happy" if item["status"] == "completed" else "concerned" if item["status"] == "failed" else "neutral"
        return {
            "mode": "attention",
            "emotion": item["display_emotion"] or emotion,
            "title": item["title"],
            "subtitle": item["summary"] or item["status"],
            "progress": item["progress"],
            "source": item["source"],
            "task_id": item["task_id"],
        }
