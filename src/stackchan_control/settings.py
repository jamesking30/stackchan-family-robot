from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(PROJECT_ROOT / ".env")


@dataclass(frozen=True)
class Settings:
    db_path: Path
    seed_character_dir: Path
    web_dir: Path
    admin_api_key: str | None = None
    host: str = "127.0.0.1"
    port: int = 8765

    @classmethod
    def from_env(cls) -> "Settings":
        db_value = os.getenv("ROBOT_DB_PATH", "var/stackchan.db")
        db_path = Path(db_value)
        if not db_path.is_absolute():
            db_path = PROJECT_ROOT / db_path
        return cls(
            db_path=db_path,
            seed_character_dir=PROJECT_ROOT / "config" / "seed_character",
            web_dir=PROJECT_ROOT / "web",
            admin_api_key=os.getenv("ROBOT_ADMIN_API_KEY") or None,
            host=os.getenv("ROBOT_HOST", "127.0.0.1"),
            port=int(os.getenv("ROBOT_PORT", "8765")),
        )
