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
    device_api_key: str | None = None
    device_id: str = "stackchan-home-01"
    host: str = "127.0.0.1"
    port: int = 8765
    gateway_heartbeat_seconds: float = 5.0
    gateway_timeout_seconds: float = 15.0
    deepseek_api_key: str | None = None
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_model: str = "deepseek-v4-flash"
    voice_auto_start: bool = False
    voice_user_id: str = "user-2"
    voice_whisper_binary: str = "whisper-cli"
    voice_whisper_model: Path = PROJECT_ROOT / "var/models/ggml-small.bin"
    voice_zh_name: str = "Tingting"
    voice_en_name: str = "Samantha"
    voice_silence_ms: int = 600
    voice_min_speech_ms: int = 300
    voice_max_speech_seconds: int = 15
    voice_wake_word: str = "小栈小栈"
    voice_wake_aliases: tuple[str, ...] = ("小站小站", "StackChan", "Stack Chan")
    voice_wake_session_seconds: float = 45.0
    voice_sleep_phrases: tuple[str, ...] = ("再见", "休息吧", "不用了")

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
            device_api_key=os.getenv("ROBOT_DEVICE_API_KEY") or None,
            device_id=os.getenv("STACKCHAN_DEVICE_ID", "stackchan-home-01"),
            host=os.getenv("ROBOT_HOST", "127.0.0.1"),
            port=int(os.getenv("ROBOT_PORT", "8765")),
            gateway_heartbeat_seconds=float(
                os.getenv("ROBOT_GATEWAY_HEARTBEAT_SECONDS", "5")
            ),
            gateway_timeout_seconds=float(
                os.getenv("ROBOT_GATEWAY_TIMEOUT_SECONDS", "15")
            ),
            deepseek_api_key=os.getenv("DEEPSEEK_API_KEY") or None,
            deepseek_base_url=os.getenv(
                "DEEPSEEK_BASE_URL", "https://api.deepseek.com"
            ).rstrip("/"),
            deepseek_model=os.getenv("DEEPSEEK_MODEL", "deepseek-v4-flash"),
            voice_auto_start=os.getenv("ROBOT_VOICE_AUTO_START", "false").lower()
            in {"1", "true", "yes", "on"},
            voice_user_id=os.getenv("ROBOT_VOICE_USER_ID", "user-2"),
            voice_whisper_binary=os.getenv(
                "ROBOT_VOICE_WHISPER_BINARY", "whisper-cli"
            ),
            voice_whisper_model=Path(
                os.getenv(
                    "ROBOT_VOICE_WHISPER_MODEL",
                    str(PROJECT_ROOT / "var/models/ggml-small.bin"),
                )
            ).expanduser(),
            voice_zh_name=os.getenv("ROBOT_VOICE_ZH_NAME", "Tingting"),
            voice_en_name=os.getenv("ROBOT_VOICE_EN_NAME", "Samantha"),
            voice_silence_ms=int(os.getenv("ROBOT_VOICE_SILENCE_MS", "600")),
            voice_min_speech_ms=int(os.getenv("ROBOT_VOICE_MIN_SPEECH_MS", "300")),
            voice_max_speech_seconds=int(
                os.getenv("ROBOT_VOICE_MAX_SPEECH_SECONDS", "15")
            ),
            voice_wake_word=os.getenv("ROBOT_VOICE_WAKE_WORD", "小栈小栈").strip(),
            voice_wake_aliases=tuple(
                item.strip()
                for item in os.getenv(
                    "ROBOT_VOICE_WAKE_ALIASES", "小站小站,StackChan,Stack Chan"
                ).split(",")
                if item.strip()
            ),
            voice_wake_session_seconds=float(
                os.getenv("ROBOT_VOICE_WAKE_SESSION_SECONDS", "45")
            ),
            voice_sleep_phrases=tuple(
                item.strip()
                for item in os.getenv(
                    "ROBOT_VOICE_SLEEP_PHRASES", "再见,休息吧,不用了"
                ).split(",")
                if item.strip()
            ),
        )
