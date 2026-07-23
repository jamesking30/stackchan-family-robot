from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(PROJECT_ROOT / ".env")


def project_path(value: str) -> Path:
    path = Path(value).expanduser()
    return path if path.is_absolute() else PROJECT_ROOT / path


@dataclass(frozen=True)
class Settings:
    db_path: Path
    seed_character_dir: Path
    web_dir: Path
    avatar_assets_dir: Path = PROJECT_ROOT / "assets/avatars/elysia/v1"
    avatar_voice_enabled: bool = False
    avatar_idle_animation_enabled: bool = True
    avatar_idle_min_seconds: float = 4.5
    avatar_idle_max_seconds: float = 9.5
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
    voice_whisper_server_url: str = "http://127.0.0.1:8767"
    voice_whisper_model: Path = PROJECT_ROOT / "var/models/ggml-small.bin"
    voice_zh_name: str = "Tingting"
    voice_en_name: str = "Samantha"
    voice_tts_provider: str = "gpt_sovits"
    voice_gpt_sovits_base_url: str = "http://127.0.0.1:9880"
    voice_gpt_sovits_ref_audio: Path = (
        PROJECT_ROOT / "var/models/gpt-sovits/elysia/reference-happy.wav"
    )
    voice_gpt_sovits_prompt_text: str = "所以你今天就来见我了吗？哇，真令人开心呢。"
    voice_gpt_sovits_prompt_lang: str = "zh"
    voice_gpt_sovits_speed: float = 1.08
    voice_tts_base_url: str = "http://127.0.0.1:8766"
    voice_tts_model: str = "mlx-community/Qwen3-TTS-12Hz-0.6B-CustomVoice-8bit"
    voice_tts_speaker: str = "Vivian"
    voice_tts_instruction: str = "明亮、轻快、活泼、有精神，语速稍快，句尾干净利落；保持亲切自然，不要慵懒拖音，不要尖叫或夸张卖萌。"
    voice_tts_speed: float = 1.08
    voice_tts_fallback_to_system: bool = True
    voice_silence_ms: int = 600
    voice_min_speech_ms: int = 300
    voice_max_speech_seconds: int = 15
    voice_wake_word: str = "爱莉"
    voice_wake_aliases: tuple[str, ...] = (
        "艾莉",
        "爱丽",
        "艾丽",
        "爱里",
        "爱莉希雅",
        "Ai Li",
        "Aili",
        "Ali",
        "Ally",
        "Eli",
        "Ellie",
    )
    voice_wake_session_seconds: float = 45.0
    voice_sleep_phrases: tuple[str, ...] = ("再见", "休息吧", "不用了")
    voice_wake_ack_pcm: Path = PROJECT_ROOT / "var/cache/voice/wake-ack-v2.wav"
    voice_kws_enabled: bool = False
    voice_kws_model_dir: Path = (
        PROJECT_ROOT
        / "var/models/sherpa/sherpa-onnx-kws-zipformer-zh-en-3M-2025-12-20"
    )
    voice_kws_keywords_file: Path = (
        PROJECT_ROOT
        / "var/models/sherpa/sherpa-onnx-kws-zipformer-zh-en-3M-2025-12-20"
        / "keywords.txt"
    )
    voice_kws_score: float = 2.0
    voice_kws_threshold: float = 0.20

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
            avatar_assets_dir=project_path(
                os.getenv(
                    "ROBOT_AVATAR_ASSETS_DIR",
                    str(PROJECT_ROOT / "assets/avatars/elysia/v1"),
                )
            ),
            avatar_voice_enabled=os.getenv(
                "ROBOT_AVATAR_VOICE_ENABLED", "true"
            ).lower()
            in {"1", "true", "yes", "on"},
            avatar_idle_animation_enabled=os.getenv(
                "ROBOT_AVATAR_IDLE_ANIMATION_ENABLED", "true"
            ).lower()
            in {"1", "true", "yes", "on"},
            avatar_idle_min_seconds=float(
                os.getenv("ROBOT_AVATAR_IDLE_MIN_SECONDS", "4.5")
            ),
            avatar_idle_max_seconds=float(
                os.getenv("ROBOT_AVATAR_IDLE_MAX_SECONDS", "9.5")
            ),
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
            voice_whisper_server_url=os.getenv(
                "ROBOT_VOICE_WHISPER_SERVER_URL", "http://127.0.0.1:8767"
            ).rstrip("/"),
            voice_whisper_model=project_path(
                os.getenv(
                    "ROBOT_VOICE_WHISPER_MODEL",
                    str(PROJECT_ROOT / "var/models/ggml-small.bin"),
                )
            ),
            voice_zh_name=os.getenv("ROBOT_VOICE_ZH_NAME", "Tingting"),
            voice_en_name=os.getenv("ROBOT_VOICE_EN_NAME", "Samantha"),
            voice_tts_provider=os.getenv(
                "ROBOT_VOICE_TTS_PROVIDER", "gpt_sovits"
            ).strip().lower(),
            voice_gpt_sovits_base_url=os.getenv(
                "ROBOT_VOICE_GPT_SOVITS_BASE_URL", "http://127.0.0.1:9880"
            ).rstrip("/"),
            voice_gpt_sovits_ref_audio=project_path(
                os.getenv(
                    "ROBOT_VOICE_GPT_SOVITS_REF_AUDIO",
                    str(
                        PROJECT_ROOT
                        / "var/models/gpt-sovits/elysia/reference-happy.wav"
                    ),
                )
            ),
            voice_gpt_sovits_prompt_text=os.getenv(
                "ROBOT_VOICE_GPT_SOVITS_PROMPT_TEXT",
                "所以你今天就来见我了吗？哇，真令人开心呢。",
            ),
            voice_gpt_sovits_prompt_lang=os.getenv(
                "ROBOT_VOICE_GPT_SOVITS_PROMPT_LANG", "zh"
            ),
            voice_gpt_sovits_speed=float(
                os.getenv("ROBOT_VOICE_GPT_SOVITS_SPEED", "1.08")
            ),
            voice_tts_base_url=os.getenv(
                "ROBOT_VOICE_TTS_BASE_URL", "http://127.0.0.1:8766"
            ).rstrip("/"),
            voice_tts_model=os.getenv(
                "ROBOT_VOICE_TTS_MODEL",
                "mlx-community/Qwen3-TTS-12Hz-0.6B-CustomVoice-8bit",
            ),
            voice_tts_speaker=os.getenv("ROBOT_VOICE_TTS_SPEAKER", "Vivian"),
            voice_tts_instruction=os.getenv(
                "ROBOT_VOICE_TTS_INSTRUCTION",
                "明亮、轻快、活泼、有精神，语速稍快，句尾干净利落；保持亲切自然，不要慵懒拖音，不要尖叫或夸张卖萌。",
            ),
            voice_tts_speed=float(os.getenv("ROBOT_VOICE_TTS_SPEED", "1.08")),
            voice_tts_fallback_to_system=os.getenv(
                "ROBOT_VOICE_TTS_FALLBACK_TO_SYSTEM", "true"
            ).lower()
            in {"1", "true", "yes", "on"},
            voice_silence_ms=int(os.getenv("ROBOT_VOICE_SILENCE_MS", "600")),
            voice_min_speech_ms=int(os.getenv("ROBOT_VOICE_MIN_SPEECH_MS", "300")),
            voice_max_speech_seconds=int(
                os.getenv("ROBOT_VOICE_MAX_SPEECH_SECONDS", "15")
            ),
            voice_wake_word=os.getenv("ROBOT_VOICE_WAKE_WORD", "爱莉").strip(),
            voice_wake_aliases=tuple(
                item.strip()
                for item in os.getenv(
                    "ROBOT_VOICE_WAKE_ALIASES",
                    "艾莉,爱丽,艾丽,爱里,爱莉希雅,Ai Li,Aili,Ali,Ally,Eli,Ellie",
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
            voice_wake_ack_pcm=project_path(
                os.getenv(
                    "ROBOT_VOICE_WAKE_ACK_PCM",
                    str(PROJECT_ROOT / "var/cache/voice/wake-ack-v2.wav"),
                )
            ),
            voice_kws_enabled=os.getenv(
                "ROBOT_VOICE_KWS_ENABLED", "false"
            ).lower()
            in {"1", "true", "yes", "on"},
            voice_kws_model_dir=project_path(
                os.getenv(
                    "ROBOT_VOICE_KWS_MODEL_DIR",
                    str(
                        PROJECT_ROOT
                        / "var/models/sherpa"
                        / "sherpa-onnx-kws-zipformer-zh-en-3M-2025-12-20"
                    ),
                )
            ),
            voice_kws_keywords_file=project_path(
                os.getenv(
                    "ROBOT_VOICE_KWS_KEYWORDS_FILE",
                    str(
                        PROJECT_ROOT
                        / "var/models/sherpa"
                        / "sherpa-onnx-kws-zipformer-zh-en-3M-2025-12-20"
                        / "keywords.txt"
                    ),
                )
            ),
            voice_kws_score=float(os.getenv("ROBOT_VOICE_KWS_SCORE", "2.0")),
            voice_kws_threshold=float(
                os.getenv("ROBOT_VOICE_KWS_THRESHOLD", "0.20")
            ),
        )
