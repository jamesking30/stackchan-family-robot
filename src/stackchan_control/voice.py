from __future__ import annotations

import asyncio
import audioop
import ctypes
import ctypes.util
import io
import json
import logging
import random
import re
import shutil
import tempfile
import time
import wave
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import AsyncIterator, Awaitable, Callable, Protocol

import httpx

from .avatar import AvatarController
from .child_identity import ChildVoiceClassifier, ChildVoiceEvidence
from .gateway import DeviceOfflineError, MessageType, StackChanGateway
from .repository import RobotRepository
from .settings import Settings
from .wake import SherpaWakeWordDetector, WakeWordDetector


logger = logging.getLogger(__name__)


class VoiceError(RuntimeError):
    pass


class NoSpeechDetected(VoiceError):
    pass


def resolve_local_executable(command: str) -> str:
    """Resolve Homebrew tools even when launchd provides a minimal PATH."""
    expanded = Path(command).expanduser()
    if expanded.is_absolute():
        if expanded.is_file():
            return str(expanded)
        raise VoiceError(f"local executable was not found: {expanded}")

    discovered = shutil.which(command)
    if discovered:
        return discovered

    for directory in (
        Path("/opt/homebrew/bin"),
        Path("/usr/local/bin"),
        Path("/usr/bin"),
        Path("/bin"),
    ):
        candidate = directory / command
        if candidate.is_file():
            return str(candidate)
    raise VoiceError(f"local executable was not found: {command}")


class VoiceMode(str, Enum):
    STOPPED = "stopped"
    WAITING_FOR_WAKE_WORD = "waiting_for_wake_word"
    LISTENING = "listening"
    TRANSCRIBING = "transcribing"
    THINKING = "thinking"
    SPEAKING = "speaking"
    ERROR = "error"


class VoiceProvider(Protocol):
    async def transcribe(self, wav_audio: bytes) -> str: ...

    async def answer(
        self,
        instructions: str,
        transcript: str,
        history: list[dict[str, str]] | None = None,
    ) -> str: ...

    async def synthesize(self, text: str) -> bytes: ...


class LocalDeepSeekVoiceProvider:
    """Local ASR/TTS with DeepSeek receiving text only."""

    def __init__(self, settings: Settings) -> None:
        if not settings.deepseek_api_key:
            raise VoiceError("DEEPSEEK_API_KEY is required for voice conversations")
        self.api_key = settings.deepseek_api_key
        self.base_url = settings.deepseek_base_url.rstrip("/")
        self.model = settings.deepseek_model
        self.whisper_binary = resolve_local_executable(settings.voice_whisper_binary)
        self.whisper_server_url = settings.voice_whisper_server_url
        self.say_binary = resolve_local_executable("say")
        self.ffmpeg_binary = resolve_local_executable("ffmpeg")
        self.whisper_model = settings.voice_whisper_model
        self.zh_voice = settings.voice_zh_name
        self.en_voice = settings.voice_en_name
        self.tts_provider = settings.voice_tts_provider
        self.gpt_sovits_base_url = settings.voice_gpt_sovits_base_url
        self.gpt_sovits_ref_audio = settings.voice_gpt_sovits_ref_audio
        self.gpt_sovits_prompt_text = settings.voice_gpt_sovits_prompt_text
        self.gpt_sovits_prompt_lang = settings.voice_gpt_sovits_prompt_lang
        self.gpt_sovits_speed = settings.voice_gpt_sovits_speed
        self.tts_base_url = settings.voice_tts_base_url
        self.tts_model = settings.voice_tts_model
        self.tts_speaker = settings.voice_tts_speaker
        self.tts_instruction = settings.voice_tts_instruction
        self.tts_speed = settings.voice_tts_speed
        self.tts_fallback_to_system = settings.voice_tts_fallback_to_system

        if not self.whisper_model.is_file():
            raise VoiceError(f"local Whisper model was not found: {self.whisper_model}")

    @property
    def headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.api_key}"}

    async def transcribe(self, wav_audio: bytes) -> str:
        if self.whisper_server_url:
            try:
                return await self._transcribe_with_server(wav_audio)
            except NoSpeechDetected:
                raise
            except (httpx.HTTPError, VoiceError, ValueError) as exc:
                logger.warning(
                    "persistent Whisper unavailable; using CLI fallback: %s", exc
                )
        return await self._transcribe_with_cli(wav_audio)

    async def _transcribe_with_server(self, wav_audio: bytes) -> str:
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(
                f"{self.whisper_server_url}/inference",
                files={"file": ("utterance.wav", wav_audio, "audio/wav")},
                data={
                    "response_format": "json",
                    "language": "auto",
                    "temperature": "0.0",
                },
            )
        if not response.is_success:
            raise VoiceError(
                f"persistent Whisper failed with HTTP {response.status_code}"
            )
        body = response.json()
        return self._clean_transcript(str(body.get("text", "")))

    async def _transcribe_with_cli(self, wav_audio: bytes) -> str:
        with tempfile.TemporaryDirectory(prefix="stackchan-whisper-") as temp_dir:
            temp_path = Path(temp_dir)
            input_path = temp_path / "utterance.wav"
            input_path.write_bytes(wav_audio)
            for language in ("auto", "zh", "en"):
                output_path = temp_path / f"transcript-{language}"
                process = await asyncio.create_subprocess_exec(
                    self.whisper_binary,
                    "-m",
                    str(self.whisper_model),
                    "-f",
                    str(input_path),
                    "-l",
                    language,
                    "-t",
                    "6",
                    "-otxt",
                    "-of",
                    str(output_path),
                    "-np",
                    "-nt",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                stdout, stderr = await process.communicate()
                if process.returncode != 0:
                    detail = stderr.decode("utf-8", errors="replace").strip()[-240:]
                    raise VoiceError(f"local Whisper failed: {detail or process.returncode}")
                transcript_file = output_path.with_suffix(".txt")
                if transcript_file.is_file():
                    text = transcript_file.read_text(encoding="utf-8").strip()
                else:
                    text = stdout.decode("utf-8", errors="replace").strip()
                try:
                    return self._clean_transcript(text)
                except NoSpeechDetected:
                    continue
        raise NoSpeechDetected("no Chinese or English speech detected")

    @staticmethod
    def _clean_transcript(text: str) -> str:
        compact = re.sub(r"\s+", " ", text).strip()
        compact = re.sub(
            r"\b([A-Za-z]{2,40})\1\b", r"\1", compact, flags=re.IGNORECASE
        )
        if (
            not compact
            or compact.upper() in {"[BLANK_AUDIO]", "[NO SPEECH]", "(BLANK AUDIO)"}
            or re.fullmatch(r"[\(\（\[].{1,40}[\)\）\]]", compact)
            or re.search(r"[\uac00-\ud7af]", compact)
            or re.search(r"字幕.{0,4}(製作|制作|提供)", compact)
            or not re.search(r"[A-Za-z0-9\u3400-\u9fff]", compact)
            or re.fullmatch(r"(.)\1{4,}", compact)
        ):
            raise NoSpeechDetected("no Chinese or English speech detected")
        return compact

    @staticmethod
    def _messages(
        instructions: str,
        transcript: str,
        history: list[dict[str, str]] | None = None,
    ) -> list[dict[str, str]]:
        return [
            {"role": "system", "content": instructions},
            *(history or []),
            {"role": "user", "content": transcript},
        ]

    async def answer(
        self,
        instructions: str,
        transcript: str,
        history: list[dict[str, str]] | None = None,
    ) -> str:
        async with httpx.AsyncClient(timeout=60) as client:
            response = await client.post(
                f"{self.base_url}/chat/completions",
                headers={**self.headers, "Content-Type": "application/json"},
                json={
                    "model": self.model,
                    "messages": self._messages(instructions, transcript, history),
                    "thinking": {"type": "disabled"},
                    "max_tokens": 240,
                    "stream": False,
                },
            )
        self._raise(response, "response")
        body = response.json()
        choices = body.get("choices", [])
        answer = ""
        if choices:
            answer = str(choices[0].get("message", {}).get("content", "")).strip()
        if not answer:
            raise VoiceError("DeepSeek returned no spoken text")
        return answer

    async def answer_segments(
        self,
        instructions: str,
        transcript: str,
        history: list[dict[str, str]] | None = None,
    ) -> AsyncIterator[str]:
        payload = {
            "model": self.model,
            "messages": self._messages(instructions, transcript, history),
            "thinking": {"type": "disabled"},
            "max_tokens": 180,
            "stream": True,
        }
        buffer = ""
        yielded = False
        async with httpx.AsyncClient(timeout=60) as client:
            async with client.stream(
                "POST",
                f"{self.base_url}/chat/completions",
                headers={**self.headers, "Content-Type": "application/json"},
                json=payload,
            ) as response:
                self._raise(response, "streaming response")
                async for line in response.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if not data or data == "[DONE]":
                        continue
                    try:
                        body = json.loads(data)
                        raw_delta = (
                            body.get("choices", [{}])[0]
                            .get("delta", {})
                            .get("content", "")
                        )
                        delta = raw_delta if isinstance(raw_delta, str) else ""
                    except (ValueError, KeyError, IndexError, TypeError):
                        continue
                    buffer += delta
                    segments, buffer = self._pop_spoken_segments(buffer)
                    for segment in segments:
                        yielded = True
                        yield segment
        tail = buffer.strip()
        if tail:
            yielded = True
            yield tail
        if not yielded:
            raise VoiceError("DeepSeek returned no spoken text")

    @staticmethod
    def _pop_spoken_segments(buffer: str) -> tuple[list[str], str]:
        segments: list[str] = []
        start = 0
        for match in re.finditer(r"[。！？!?；;]|(?<!\d)\.(?:\s|$)", buffer):
            end = match.end()
            segment = buffer[start:end].strip()
            if segment:
                segments.append(segment)
            start = end
        return segments, buffer[start:]

    async def synthesize(self, text: str) -> bytes:
        if self.tts_provider == "gpt_sovits" and self.gpt_sovits_base_url:
            try:
                return await self._synthesize_gpt_sovits(text)
            except Exception as exc:
                logger.warning("GPT-SoVITS failed; using local fallback: %s", exc)
        if self.tts_base_url:
            try:
                return await self._synthesize_neural(text)
            except Exception as exc:
                logger.warning("Qwen TTS failed; using system fallback: %s", exc)
        if not self.tts_fallback_to_system:
            raise VoiceError("all configured local speech synthesis services failed")
        return await self._synthesize_system(text)

    async def _synthesize_gpt_sovits(self, text: str) -> bytes:
        if not self.gpt_sovits_ref_audio.is_file():
            raise VoiceError(
                f"GPT-SoVITS reference audio was not found: {self.gpt_sovits_ref_audio}"
            )
        text_lang = "zh" if re.search(r"[\u3400-\u9fff]", text) else "en"
        async with httpx.AsyncClient(timeout=180) as client:
            response = await client.post(
                f"{self.gpt_sovits_base_url}/tts",
                json={
                    "text": text,
                    "text_lang": text_lang,
                    "ref_audio_path": str(self.gpt_sovits_ref_audio),
                    "prompt_text": self.gpt_sovits_prompt_text,
                    "prompt_lang": self.gpt_sovits_prompt_lang,
                    "text_split_method": "cut5",
                    "batch_size": 1,
                    "speed_factor": self.gpt_sovits_speed,
                    "media_type": "wav",
                    "streaming_mode": False,
                    "parallel_infer": True,
                    "repetition_penalty": 1.35,
                },
            )
        if not response.is_success:
            detail = response.text.strip()[-240:]
            raise VoiceError(
                f"GPT-SoVITS failed with HTTP {response.status_code}: {detail}"
            )
        if not response.content:
            raise VoiceError("GPT-SoVITS returned no audio")
        return await self._convert_audio_to_pcm(response.content)

    async def _synthesize_neural(self, text: str) -> bytes:
        language = "Chinese" if re.search(r"[\u3400-\u9fff]", text) else "English"
        chinese_chars = len(re.findall(r"[\u3400-\u9fff]", text))
        latin_words = len(re.findall(r"[A-Za-z0-9]+", text))
        max_tokens = min(280, max(48, chinese_chars * 4 + latin_words * 8 + 24))
        async with httpx.AsyncClient(timeout=180) as client:
            response = await client.post(
                f"{self.tts_base_url}/v1/audio/speech",
                json={
                    "model": self.tts_model,
                    "input": text,
                    "voice": self.tts_speaker,
                    "instruct": self.tts_instruction,
                    "lang_code": language,
                    "response_format": "wav",
                    "stream": False,
                    "speed": self.tts_speed,
                    "max_tokens": max_tokens,
                    "repetition_penalty": 1.05,
                },
            )
        if not response.is_success:
            raise VoiceError(
                f"local neural TTS failed with HTTP {response.status_code}"
            )
        if not response.content:
            raise VoiceError("local neural TTS returned no audio")
        return await self._convert_audio_to_pcm(response.content)

    async def _synthesize_system(self, text: str) -> bytes:
        voice = self.zh_voice if re.search(r"[\u3400-\u9fff]", text) else self.en_voice
        with tempfile.TemporaryDirectory(prefix="stackchan-tts-") as temp_dir:
            aiff_path = Path(temp_dir) / "speech.aiff"
            say_process = await asyncio.create_subprocess_exec(
                self.say_binary,
                "-v",
                voice,
                "-o",
                str(aiff_path),
                "-f",
                "-",
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
            )
            _, say_stderr = await say_process.communicate(text.encode("utf-8"))
            if say_process.returncode != 0:
                detail = say_stderr.decode("utf-8", errors="replace").strip()[-240:]
                raise VoiceError(f"local speech synthesis failed: {detail}")
            pcm = await self._convert_audio_to_pcm(aiff_path.read_bytes())
        if not pcm:
            raise VoiceError("local speech synthesis returned no audio")
        return pcm

    async def _convert_audio_to_pcm(self, audio: bytes) -> bytes:
        convert_process = await asyncio.create_subprocess_exec(
            self.ffmpeg_binary,
            "-nostdin",
            "-v",
            "error",
            "-i",
            "pipe:0",
            "-f",
            "s16le",
            "-acodec",
            "pcm_s16le",
            "-ar",
            "24000",
            "-ac",
            "1",
            "pipe:1",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        pcm, convert_stderr = await convert_process.communicate(audio)
        if convert_process.returncode != 0:
            detail = convert_stderr.decode("utf-8", errors="replace").strip()[-240:]
            raise VoiceError(f"local audio conversion failed: {detail}")
        if not pcm:
            raise VoiceError("local audio conversion returned no PCM")
        return pcm

    @staticmethod
    def _raise(response: httpx.Response, operation: str) -> None:
        if response.is_success:
            return
        request_id = response.headers.get("x-request-id", "unknown")
        error_code = "unknown"
        try:
            error_code = str(response.json().get("error", {}).get("code", "unknown"))
        except ValueError:
            pass
        raise VoiceError(
            f"DeepSeek {operation} failed with HTTP {response.status_code}; "
            f"code={error_code}; request_id={request_id}"
        )


class OpusCodec:
    SPEECH_SAMPLE_RATE = 24000
    SPEECH_FRAME_DURATION_MS = 20
    SPEECH_BITRATE = 48000

    def __init__(self) -> None:
        self._lib = self._load_library()
        self._configure_library()
        error = ctypes.c_int()
        self._decoder = self._lib.opus_decoder_create(16000, 1, ctypes.byref(error))
        if not self._decoder or error.value != 0:
            raise VoiceError(f"failed to create 16kHz Opus decoder: {error.value}")
        self._encoder = self._lib.opus_encoder_create(24000, 1, 2049, ctypes.byref(error))
        if not self._encoder or error.value != 0:
            self._lib.opus_decoder_destroy(self._decoder)
            raise VoiceError(f"failed to create 24kHz Opus encoder: {error.value}")
        self._set_encoder_option(4002, self.SPEECH_BITRATE)  # OPUS_SET_BITRATE
        self._set_encoder_option(4006, 1)  # OPUS_SET_VBR
        self._set_encoder_option(4010, 10)  # OPUS_SET_COMPLEXITY
        self._set_encoder_option(4024, 3001)  # OPUS_SET_SIGNAL(OPUS_SIGNAL_VOICE)

    def decode_microphone(self, packet: bytes) -> bytes:
        frame_size = 960
        output = (ctypes.c_int16 * frame_size)()
        encoded = (ctypes.c_ubyte * len(packet)).from_buffer_copy(packet)
        samples = self._lib.opus_decode(
            self._decoder, encoded, len(packet), output, frame_size, 0
        )
        if samples < 0:
            raise VoiceError(f"Opus microphone decode failed: {samples}")
        return bytes(output)[: samples * 2]

    def encode_speech(self, pcm: bytes) -> list[bytes]:
        frame_bytes = (
            self.SPEECH_SAMPLE_RATE * self.SPEECH_FRAME_DURATION_MS // 1000 * 2
        )
        packets: list[bytes] = []
        for offset in range(0, len(pcm), frame_bytes):
            frame = pcm[offset : offset + frame_bytes]
            if len(frame) < frame_bytes:
                frame += b"\x00" * (frame_bytes - len(frame))
            samples = (ctypes.c_int16 * (frame_bytes // 2)).from_buffer_copy(frame)
            output = (ctypes.c_ubyte * 4096)()
            encoded_bytes = self._lib.opus_encode(
                self._encoder, samples, frame_bytes // 2, output, len(output)
            )
            if encoded_bytes < 0:
                raise VoiceError(f"Opus speech encode failed: {encoded_bytes}")
            packets.append(bytes(output[:encoded_bytes]))
        return packets

    def _set_encoder_option(self, request: int, value: int) -> None:
        result = self._lib.opus_encoder_ctl(
            self._encoder,
            ctypes.c_int(request),
            ctypes.c_int(value),
        )
        if result != 0:
            raise VoiceError(
                f"failed to configure Opus encoder request {request}: {result}"
            )

    def close(self) -> None:
        decoder = getattr(self, "_decoder", None)
        encoder = getattr(self, "_encoder", None)
        if decoder:
            self._lib.opus_decoder_destroy(decoder)
            self._decoder = None
        if encoder:
            self._lib.opus_encoder_destroy(encoder)
            self._encoder = None

    def __del__(self) -> None:
        self.close()

    @staticmethod
    def _load_library() -> ctypes.CDLL:
        candidates = [
            ctypes.util.find_library("opus"),
            "/opt/homebrew/lib/libopus.dylib",
            "/usr/local/lib/libopus.dylib",
            "libopus.so.0",
            "libopus.so",
        ]
        for candidate in candidates:
            if not candidate:
                continue
            if candidate.startswith("/") and not Path(candidate).exists():
                continue
            try:
                return ctypes.CDLL(candidate)
            except OSError:
                continue
        raise VoiceError(
            "libopus was not found; install it with Homebrew (`brew install opus`)"
        )

    def _configure_library(self) -> None:
        self._lib.opus_decoder_create.argtypes = [
            ctypes.c_int32,
            ctypes.c_int,
            ctypes.POINTER(ctypes.c_int),
        ]
        self._lib.opus_decoder_create.restype = ctypes.c_void_p
        self._lib.opus_decoder_destroy.argtypes = [ctypes.c_void_p]
        self._lib.opus_decode.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_ubyte),
            ctypes.c_int32,
            ctypes.POINTER(ctypes.c_int16),
            ctypes.c_int,
            ctypes.c_int,
        ]
        self._lib.opus_decode.restype = ctypes.c_int
        self._lib.opus_encoder_create.argtypes = [
            ctypes.c_int32,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.POINTER(ctypes.c_int),
        ]
        self._lib.opus_encoder_create.restype = ctypes.c_void_p
        self._lib.opus_encoder_destroy.argtypes = [ctypes.c_void_p]
        self._lib.opus_encoder_ctl.argtypes = [ctypes.c_void_p, ctypes.c_int]
        self._lib.opus_encoder_ctl.restype = ctypes.c_int
        self._lib.opus_encode.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_int16),
            ctypes.c_int,
            ctypes.POINTER(ctypes.c_ubyte),
            ctypes.c_int32,
        ]
        self._lib.opus_encode.restype = ctypes.c_int32


@dataclass
class VoiceState:
    mode: VoiceMode = VoiceMode.STOPPED
    enabled: bool = False
    user_id: str = "user-2"
    turn_id: int = 0
    wake_word: str = ""
    awake: bool = False
    last_wake_keyword: str | None = None
    wake_detected_at: datetime | None = None
    wake_detection_count: int = 0
    last_wake_child_voice: bool | None = None
    last_wake_pitch_hz: float | None = None
    last_wake_voiced_ratio: float | None = None
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
    latency_ms: dict[str, float] = field(default_factory=dict)
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def snapshot(self) -> dict[str, object]:
        return {
            "mode": self.mode.value,
            "enabled": self.enabled,
            "user_id": self.user_id,
            "turn_id": self.turn_id,
            "wake_word": self.wake_word,
            "awake": self.awake,
            "last_wake_keyword": self.last_wake_keyword,
            "wake_detected_at": self.wake_detected_at,
            "wake_detection_count": self.wake_detection_count,
            "last_wake_child_voice": self.last_wake_child_voice,
            "last_wake_pitch_hz": self.last_wake_pitch_hz,
            "last_wake_voiced_ratio": self.last_wake_voiced_ratio,
            "last_heard_transcript": self.last_heard_transcript,
            "speaker_identity": self.speaker_identity,
            "speaker_identity_confidence": self.speaker_identity_confidence,
            "speaker_identity_reason": self.speaker_identity_reason,
            "speaker_identity_at": self.speaker_identity_at,
            "transcript": self.transcript,
            "response_text": self.response_text,
            "error": self.error,
            "audio_rms": self.audio_rms,
            "audio_peak_rms": self.audio_peak_rms,
            "latency_ms": self.latency_ms,
            "updated_at": self.updated_at,
        }


class VoiceSessionManager:
    """One in-memory voice session. Raw microphone and synthesized audio are never persisted."""

    def __init__(
        self,
        settings: Settings,
        repository: RobotRepository,
        gateway: StackChanGateway,
        provider: VoiceProvider | None = None,
        codec: OpusCodec | None = None,
        wake_detector: WakeWordDetector | None = None,
        avatar_controller: AvatarController | None = None,
    ) -> None:
        self.settings = settings
        self.repository = repository
        self.gateway = gateway
        self.provider = provider
        self.codec = codec
        self.wake_detector = wake_detector
        self.avatar_controller = avatar_controller
        self.state = VoiceState(user_id=settings.voice_user_id)
        self.state.wake_word = settings.voice_wake_word
        self._pre_roll: deque[bytes] = deque(maxlen=5)
        self._wake_audio: deque[bytes] = deque(maxlen=24)
        self._utterance: list[bytes] = []
        self._speaking_detected = False
        self._silence_ms = 0
        self._noise_rms = 90.0
        self._ignore_audio_until = 0.0
        self._wake_deadline: float | None = None
        self._history: deque[dict[str, str]] = deque(maxlen=12)
        self._task: asyncio.Task[None] | None = None
        self._idle_animation_task: asyncio.Task[None] | None = None
        self._wake_callback: (
            Callable[[ChildVoiceEvidence], Awaitable[None]] | None
        ) = None
        self._child_voice_classifier = ChildVoiceClassifier(
            minimum_pitch_hz=settings.child_identity_minimum_pitch_hz
        )
        self._base_user_id = settings.voice_user_id
        self._lock = asyncio.Lock()

    def set_wake_callback(
        self,
        callback: Callable[[ChildVoiceEvidence], Awaitable[None]] | None,
    ) -> None:
        self._wake_callback = callback

    def _schedule_wake_callback(
        self, evidence: ChildVoiceEvidence
    ) -> asyncio.Task[None] | None:
        if self._wake_callback is not None:
            return asyncio.create_task(self._run_wake_callback(evidence))
        return None

    async def _run_wake_callback(self, evidence: ChildVoiceEvidence) -> None:
        assert self._wake_callback is not None
        try:
            await self._wake_callback(evidence)
        except Exception as exc:
            logger.warning("wake callback failed: %s", exc)

    def identify_speaker(
        self, user_id: str, *, confidence: float, reason: str
    ) -> None:
        user = self.repository.get_user(user_id)
        if not user["enabled"]:
            return
        if self.state.user_id != user_id:
            self._history.clear()
        self.state.user_id = user_id
        self.state.speaker_identity = str(user["display_name"])
        self.state.speaker_identity_confidence = round(confidence, 3)
        self.state.speaker_identity_reason = reason
        self.state.speaker_identity_at = datetime.now(timezone.utc)
        self.state.updated_at = datetime.now(timezone.utc)

    def clear_inferred_speaker(self) -> None:
        if self.state.speaker_identity is not None:
            self._history.clear()
            self.state.user_id = self._base_user_id
        self.state.speaker_identity = None
        self.state.speaker_identity_confidence = None
        self.state.speaker_identity_reason = None
        self.state.speaker_identity_at = None

    @property
    def is_capturing_speech(self) -> bool:
        return self._speaking_detected

    def _ensure_codec(self) -> None:
        if self.codec is None:
            self.codec = OpusCodec()

    def _ensure_provider(self) -> None:
        if self.provider is None:
            self.provider = LocalDeepSeekVoiceProvider(self.settings)

    def _ensure_wake_detector(self) -> None:
        if self.settings.voice_kws_enabled and self.wake_detector is None:
            try:
                self.wake_detector = SherpaWakeWordDetector(self.settings)
            except (OSError, RuntimeError, ValueError) as exc:
                raise VoiceError(f"wake-word detector failed to start: {exc}") from exc

    async def start(self, user_id: str | None = None) -> dict[str, object]:
        target_user = user_id or self.state.user_id
        user = self.repository.get_user(target_user)
        if not user["enabled"]:
            raise VoiceError(f"user {target_user} is disabled")
        self._ensure_codec()
        self._ensure_wake_detector()
        if not await self.gateway.is_online():
            raise DeviceOfflineError("device is offline")
        async with self._lock:
            if target_user != self.state.user_id:
                self._history.clear()
            self.state.enabled = True
            self._base_user_id = target_user
            self.state.user_id = target_user
            self.clear_inferred_speaker()
            self.state.awake = not bool(self.settings.voice_wake_word)
            self.state.last_wake_keyword = None
            self.state.wake_detected_at = None
            self.state.last_wake_child_voice = None
            self.state.last_wake_pitch_hz = None
            self.state.last_wake_voiced_ratio = None
            self.state.last_heard_transcript = None
            self._wake_deadline = None
            self._set_mode(self._idle_mode())
            self.state.error = None
            self.state.transcript = None
            self.state.response_text = None
            self.state.audio_rms = 0
            self.state.audio_peak_rms = 0
            self.state.latency_ms = {}
            self._clear_audio()
            if self.wake_detector is not None:
                self.wake_detector.reset()
            self._history.clear()
            self._ignore_audio_until = time.monotonic() + 0.35
        try:
            await self.gateway.send(MessageType.START_AUDIO_STREAM)
            await self._show_avatar("neutral")
        except DeviceOfflineError:
            self.state.enabled = False
            self._set_mode(VoiceMode.STOPPED)
            raise
        return self.state.snapshot()

    async def stop(self) -> dict[str, object]:
        async with self._lock:
            self.state.enabled = False
            if self._task and not self._task.done():
                self._task.cancel()
            self._task = None
            self._clear_audio()
            self._history.clear()
            self.state.awake = False
            self._wake_deadline = None
            self.clear_inferred_speaker()
            self.state.error = None
            self._set_mode(VoiceMode.STOPPED)
        if await self.gateway.is_online():
            await self.gateway.send(MessageType.STOP_AUDIO_STREAM)
            await self._show_avatar("neutral")
        return self.state.snapshot()

    async def interrupt(self) -> dict[str, object]:
        async with self._lock:
            if self._task and not self._task.done():
                self._task.cancel()
            self._task = None
            self._clear_audio()
            self._set_mode(self._idle_mode())
        if self.state.enabled and await self.gateway.is_online():
            await self.gateway.send(MessageType.STOP_AUDIO_STREAM)
            await self.gateway.send(MessageType.START_AUDIO_STREAM)
            self._ignore_audio_until = time.monotonic() + 0.5
        return self.state.snapshot()

    async def on_device_connected(self) -> None:
        if self.settings.voice_auto_start:
            try:
                await self.start(self.settings.voice_user_id)
            except (VoiceError, DeviceOfflineError):
                self.state.error = "voice auto-start failed"
                self._set_mode(VoiceMode.ERROR)

    async def on_device_disconnected(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
        self._task = None
        self._clear_audio()
        self._history.clear()
        self.state.enabled = False
        self.state.awake = False
        self._wake_deadline = None
        self.clear_inferred_speaker()
        self._set_mode(VoiceMode.STOPPED)

    async def ingest_opus(self, payload: bytes) -> None:
        if not self.state.enabled or self.state.mode not in {
            VoiceMode.LISTENING,
            VoiceMode.WAITING_FOR_WAKE_WORD,
            VoiceMode.TRANSCRIBING,
            VoiceMode.THINKING,
        }:
            return
        kws_only = self.state.mode in {
            VoiceMode.TRANSCRIBING,
            VoiceMode.THINKING,
        }
        self._expire_wake_session()
        if time.monotonic() < self._ignore_audio_until:
            return
        self._ensure_codec()
        assert self.codec is not None
        pcm = self.codec.decode_microphone(payload)
        self._wake_audio.append(pcm)
        if self.wake_detector is not None:
            keyword = self.wake_detector.accept_pcm(pcm)
            if keyword:
                if self._task is not None and not self._task.done():
                    task = self._task
                    self._task = None
                    task.cancel()
                    try:
                        await task
                    except asyncio.CancelledError:
                        pass
                self._activate_wake_session()
                self.state.last_wake_keyword = keyword
                self.state.wake_detected_at = datetime.now(timezone.utc)
                self.state.wake_detection_count += 1
                self.state.latency_ms = {
                    "kws_frame": self.wake_detector.last_frame_latency_ms
                }
                logger.info(
                    "wake word detected keyword=%s frame_ms=%.1f",
                    keyword,
                    self.wake_detector.last_frame_latency_ms,
                )
                evidence = self._classify_wake_audio()
                await self._acknowledge_wake_word(evidence)
                return
        if kws_only:
            return
        self._pre_roll.append(pcm)
        rms = audioop.rms(pcm, 2)
        self.state.audio_rms = rms
        self.state.audio_peak_rms = max(self.state.audio_peak_rms, rms)
        if not self._speaking_detected:
            self._noise_rms = max(20.0, self._noise_rms * 0.96 + rms * 0.04)
        start_threshold = min(1100, max(280, round(self._noise_rms * 3.0)))
        continue_threshold = min(700, max(170, round(self._noise_rms * 1.7)))
        speech_threshold = continue_threshold if self._speaking_detected else start_threshold
        if rms >= speech_threshold:
            if not self._speaking_detected:
                self._utterance.extend(self._pre_roll)
                self._speaking_detected = True
            else:
                self._utterance.append(pcm)
            self._silence_ms = 0
        elif self._speaking_detected:
            self._utterance.append(pcm)
            self._silence_ms += 60
            duration_ms = len(self._utterance) * 60
            required_silence_ms = (
                max(720, self.settings.voice_silence_ms)
                if duration_ms < 900
                else self.settings.voice_silence_ms
            )
            if self._silence_ms >= required_silence_ms:
                self._finish_utterance()
        if len(self._utterance) * 60 >= self.settings.voice_max_speech_seconds * 1000:
            self._finish_utterance()

    async def voice_activity(self, speaking: bool) -> None:
        # The CoreS3 WebRTC VAD can end a phrase between Chinese syllables.
        # Host-side energy VAD owns phrase boundaries; the device event remains
        # protocol-compatible telemetry only until acoustic echo cancellation lands.
        return

    async def submit_text(self, transcript: str) -> dict[str, object]:
        self._ensure_codec()
        if not await self.gateway.is_online():
            raise DeviceOfflineError("device is offline")
        if self._task and not self._task.done():
            raise VoiceError("a voice turn is already running")
        self.state.response_text = None
        self.state.error = None
        self._task = asyncio.create_task(
            self._run_turn(transcript.strip(), None, enforce_wake=False)
        )
        await self._task
        return self.state.snapshot()

    def _finish_utterance(self) -> None:
        duration_ms = len(self._utterance) * 60
        pcm = b"".join(self._utterance)
        self._clear_audio()
        if duration_ms < self.settings.voice_min_speech_ms or not pcm:
            return
        pcm = self._normalize_pcm(pcm)
        if self._task and not self._task.done():
            return
        self.state.transcript = None
        self.state.response_text = None
        self.state.error = None
        self._task = asyncio.create_task(self._run_turn(None, pcm))

    async def _run_turn(
        self,
        transcript: str | None,
        pcm: bytes | None,
        *,
        enforce_wake: bool = True,
    ) -> None:
        microphone_paused = False
        sleep_after_reply = False
        wake_only_ack = False
        turn_started = time.perf_counter()
        self.state.latency_ms = {}
        try:
            self._ensure_provider()
            assert self.provider is not None
            if transcript is None:
                self._set_mode(VoiceMode.TRANSCRIBING)
                await self._show_avatar("listening")
                transcript = await self.provider.transcribe(self._wav(pcm or b""))
                self._record_latency("asr", turn_started)
            original_transcript = transcript
            self.state.last_heard_transcript = original_transcript
            direct_answer: str | None = None
            if self.settings.voice_wake_word and enforce_wake:
                self._expire_wake_session()
                wake_command = self._extract_wake_command(transcript)
                wake_reacquire_requested = wake_command is not None
                if not self.state.awake:
                    if wake_command is None:
                        self.state.transcript = None
                        self.state.response_text = None
                        self.state.error = None
                        self._set_mode(self._idle_mode())
                        return
                    self._activate_wake_session()
                    transcript = wake_command
                    if not transcript:
                        direct_answer = "我在，你说吧。"
                        wake_only_ack = True
                elif wake_command is not None:
                    transcript = wake_command
                    if not transcript:
                        direct_answer = "我在，你说吧。"
                        wake_only_ack = True
                if wake_reacquire_requested:
                    self.clear_inferred_speaker()
                    evidence = self._child_voice_classifier.classify(pcm or b"")
                    self._record_child_voice_evidence(evidence)
                    self._schedule_wake_callback(evidence)

                if transcript and self._is_sleep_phrase(transcript):
                    direct_answer = "好的，需要我时再叫我吧。"
                    sleep_after_reply = True

            self.state.turn_id += 1
            self.state.transcript = transcript or original_transcript
            self._set_mode(VoiceMode.THINKING)
            await self._show_avatar("thinking")
            instructions = self._instructions()
            answer = ""
            first_segment = True
            async for segment in self._response_segments(
                instructions, transcript, direct_answer
            ):
                segment = self._clean_spoken_answer(segment)
                if not segment:
                    continue
                if first_segment:
                    self._record_latency("first_text", turn_started)
                speech_pcm = (
                    self._load_wake_ack_pcm()
                    if wake_only_ack and first_segment
                    else None
                )
                if speech_pcm is None:
                    speech_pcm = await self.provider.synthesize(segment)
                if first_segment:
                    self._record_latency("first_audio_ready", turn_started)
                speech_pcm = self._maximize_speech_pcm(speech_pcm)
                if not microphone_paused:
                    self._set_mode(VoiceMode.SPEAKING)
                    await self._show_speaking_frame(0)
                    await self.gateway.send(MessageType.STOP_AUDIO_STREAM)
                    microphone_paused = True
                    self._clear_audio()
                separator = (
                    " "
                    if answer
                    and re.search(r"[A-Za-z0-9][.!?]?$", answer)
                    and re.match(r"[A-Za-z0-9]", segment)
                    else ""
                )
                answer += separator + segment
                self.state.response_text = answer
                await self.gateway.send_json(
                    MessageType.TEXT_MESSAGE,
                    {"name": "爱莉", "content": answer[:240]},
                )
                assert self.codec is not None
                packets = self.codec.encode_speech(speech_pcm)
                frame_bytes = (
                    OpusCodec.SPEECH_SAMPLE_RATE
                    * OpusCodec.SPEECH_FRAME_DURATION_MS
                    // 1000
                    * 2
                )
                avatar_interval_frames = max(
                    1, round(100 / OpusCodec.SPEECH_FRAME_DURATION_MS)
                )
                for packet_index, packet in enumerate(packets):
                    if packet_index % avatar_interval_frames == 0:
                        start = packet_index * frame_bytes
                        end = min(
                            len(speech_pcm),
                            (packet_index + avatar_interval_frames) * frame_bytes,
                        )
                        await self._show_speaking_frame(
                            self._mouth_level(speech_pcm[start:end])
                        )
                    await self.gateway.send(MessageType.OPUS, packet)
                    if first_segment:
                        self._record_latency("first_audio_sent", turn_started)
                        first_segment = False
                    await asyncio.sleep(OpusCodec.SPEECH_FRAME_DURATION_MS / 1000)
            await self._show_speaking_frame(0)
            if not answer:
                raise VoiceError("DeepSeek returned no spoken text")
            if direct_answer is None:
                self._history.extend(
                    (
                        {"role": "user", "content": transcript},
                        {"role": "assistant", "content": answer},
                    )
                )
            await asyncio.sleep(0.24)
            if self.state.enabled and await self.gateway.is_online():
                self._clear_audio()
                await self.gateway.send(MessageType.START_AUDIO_STREAM)
                self._ignore_audio_until = time.monotonic() + 1.2
                microphone_paused = False
            if sleep_after_reply:
                self.state.awake = False
                self._wake_deadline = None
                self._history.clear()
            elif self.state.awake:
                self._activate_wake_session()
            self.state.error = None
            self._record_latency("turn_total", turn_started)
            logger.info("voice turn latency_ms=%s", self.state.latency_ms)
            self._set_mode(self._idle_mode())
            await self._show_avatar("listening" if self.state.awake else "neutral")
        except asyncio.CancelledError:
            self._set_mode(self._idle_mode())
            raise
        except NoSpeechDetected:
            self.state.transcript = None
            self.state.response_text = None
            self.state.error = None
            self._clear_audio()
            self._ignore_audio_until = time.monotonic() + 0.5
            self._set_mode(self._idle_mode())
        except Exception as exc:
            logger.exception("voice turn failed")
            self.state.error = str(exc)[:240]
            self._clear_audio()
            if self.state.enabled and await self.gateway.is_online():
                # A transient ASR, model, or TTS failure must not permanently
                # disable wake-word listening. Keep the error visible while
                # returning the state machine to an ingestible mode.
                self._ignore_audio_until = time.monotonic() + 0.5
                self._set_mode(self._idle_mode())
                await self._show_avatar("concerned")
            else:
                self._set_mode(VoiceMode.ERROR)
        finally:
            if microphone_paused and self.state.enabled and await self.gateway.is_online():
                self._clear_audio()
                try:
                    await self.gateway.send(MessageType.START_AUDIO_STREAM)
                    self._ignore_audio_until = time.monotonic() + 1.2
                except DeviceOfflineError:
                    pass

    async def _response_segments(
        self,
        instructions: str,
        transcript: str,
        direct_answer: str | None,
    ) -> AsyncIterator[str]:
        assert self.provider is not None
        if direct_answer is not None:
            yield direct_answer
            return
        stream = getattr(self.provider, "answer_segments", None)
        if stream is not None:
            async for segment in stream(
                instructions, transcript, list(self._history)
            ):
                yield segment
            return
        yield await self.provider.answer(
            instructions, transcript, list(self._history)
        )

    @staticmethod
    def _clean_spoken_answer(text: str) -> str:
        text = re.sub(r"https?://\S+", "", text)
        text = re.sub(r"[*_`#]+", "", text)
        text = re.sub(
            "[\U0001F300-\U0001FAFF\u2600-\u27BF]", "", text
        )
        return re.sub(r"\s+", " ", text).strip()

    def _idle_mode(self) -> VoiceMode:
        if not self.state.enabled:
            return VoiceMode.STOPPED
        if self.settings.voice_wake_word and not self.state.awake:
            return VoiceMode.WAITING_FOR_WAKE_WORD
        return VoiceMode.LISTENING

    def _activate_wake_session(self) -> None:
        self.state.awake = True
        self._wake_deadline = (
            time.monotonic() + self.settings.voice_wake_session_seconds
        )

    def _expire_wake_session(self) -> None:
        if (
            self.state.awake
            and self.settings.voice_wake_word
            and self._wake_deadline is not None
            and time.monotonic() >= self._wake_deadline
        ):
            self.state.awake = False
            self._wake_deadline = None
            self._history.clear()
            self.clear_inferred_speaker()
            if self.state.mode == VoiceMode.LISTENING:
                self._set_mode(VoiceMode.WAITING_FOR_WAKE_WORD)

    def _extract_wake_command(self, transcript: str) -> str | None:
        aliases = (self.settings.voice_wake_word, *self.settings.voice_wake_aliases)
        text = transcript.strip()
        for alias in aliases:
            normalized_alias = self._normalize_phrase(alias)
            if not normalized_alias:
                continue
            for end in range(1, len(text) + 1):
                normalized_prefix = self._normalize_phrase(text[:end])
                if normalized_prefix == normalized_alias:
                    return text[end:].lstrip(" 　,，。.!?！？、:：;；-")
                if len(normalized_prefix) > len(normalized_alias):
                    break
        return None

    def _is_sleep_phrase(self, transcript: str) -> bool:
        normalized = self._normalize_phrase(transcript)
        return any(
            normalized == self._normalize_phrase(phrase)
            for phrase in self.settings.voice_sleep_phrases
        )

    @staticmethod
    def _normalize_phrase(text: str) -> str:
        return "".join(
            char.lower() for char in text if char.isalnum() or "\u3400" <= char <= "\u9fff"
        )

    def _instructions(self) -> str:
        preview = self.repository.prompt_preview()
        user = self.repository.get_user(self.state.user_id)
        memories = self.repository.list_memories(
            self.state.user_id, include_pending=False
        )[:8]
        memory_text = "\n".join(f"- {item['content']}" for item in memories) or "- 无已确认记忆"
        identity_instruction = ""
        if self.state.speaker_identity:
            identity_instruction = (
                f"\n本轮已确认对话者是{self.state.speaker_identity}。"
                f"可以在自然合适时称呼一次“{self.state.speaker_identity}”，"
                "不要每句重复，也不要提及识别算法。"
            )
        return (
            f"{preview['system_prompt']}\n\n"
            "当前是面对面语音对话。像熟悉的家人一样自然回应，不要重复用户的问题，"
            "不要用“当然可以”“很高兴帮助你”等客套开场。默认只说 1–2 个短句，"
            "用自然停顿的标点；需要时再问一个简短的跟进问题。不输出表情符号、"
            "Markdown、URL、JSON 或内部过程。\n"
            f"当前用户：{user['display_name']}；角色：{user['role']}；语言偏好：{user['locale']}。\n"
            f"仅可使用该用户自己的已确认记忆：\n{memory_text}"
            f"{identity_instruction}"
        )

    def _set_mode(self, mode: VoiceMode) -> None:
        self.state.mode = mode
        self.state.updated_at = datetime.now(timezone.utc)
        if (
            mode == VoiceMode.WAITING_FOR_WAKE_WORD
            and self.state.enabled
            and self.settings.avatar_idle_animation_enabled
        ):
            self._start_idle_animation()
        else:
            self._stop_idle_animation()

    def _start_idle_animation(self) -> None:
        if self.avatar_controller is None or (
            self._idle_animation_task is not None
            and not self._idle_animation_task.done()
        ):
            return
        try:
            self._idle_animation_task = asyncio.create_task(
                self._idle_animation_loop()
            )
        except RuntimeError:
            self._idle_animation_task = None

    def _stop_idle_animation(self) -> None:
        task = self._idle_animation_task
        self._idle_animation_task = None
        if task is not None and not task.done():
            task.cancel()

    async def _idle_animation_loop(self) -> None:
        gestures = ("blink", "look_left", "look_right", "hair_touch")
        weights = (0.55, 0.15, 0.15, 0.15)
        minimum, maximum = sorted(
            (
                max(1.0, self.settings.avatar_idle_min_seconds),
                max(1.0, self.settings.avatar_idle_max_seconds),
            )
        )
        current_task = asyncio.current_task()
        try:
            while (
                self.state.enabled
                and self.state.mode == VoiceMode.WAITING_FOR_WAKE_WORD
            ):
                await asyncio.sleep(random.uniform(minimum, maximum))
                if (
                    not self.state.enabled
                    or self.state.mode != VoiceMode.WAITING_FOR_WAKE_WORD
                ):
                    break
                if not await self.gateway.is_online():
                    continue
                assert self.avatar_controller is not None
                gesture = random.choices(gestures, weights=weights, k=1)[0]
                await self.avatar_controller.play_idle_gesture(gesture)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("idle avatar animation stopped: %s", exc)
        finally:
            if self._idle_animation_task is current_task:
                self._idle_animation_task = None

    def _record_latency(self, stage: str, started: float) -> None:
        self.state.latency_ms[stage] = round(
            (time.perf_counter() - started) * 1000,
            1,
        )

    def _load_wake_ack_pcm(self) -> bytes | None:
        path = self.settings.voice_wake_ack_pcm
        if not path.is_file():
            return None
        try:
            with wave.open(str(path), "rb") as wav:
                if (
                    wav.getnchannels() != 1
                    or wav.getsampwidth() != 2
                    or wav.getframerate() != OpusCodec.SPEECH_SAMPLE_RATE
                    or wav.getcomptype() != "NONE"
                ):
                    logger.warning(
                        "wake acknowledgement has an unsupported format: %s", path
                    )
                    return None
                pcm = wav.readframes(wav.getnframes())
        except (OSError, EOFError, wave.Error):
            logger.warning("wake acknowledgement could not be read: %s", path)
            return None
        return pcm or None

    async def _acknowledge_wake_word(
        self, evidence: ChildVoiceEvidence
    ) -> None:
        """Give immediate audible feedback, then start a fresh command capture."""
        assert self.codec is not None
        cached_pcm = self._load_wake_ack_pcm()
        self._set_mode(VoiceMode.SPEAKING)
        await self._show_avatar("listening")
        self._clear_audio()
        await self.gateway.send(MessageType.STOP_AUDIO_STREAM)
        wake_task = self._schedule_wake_callback(evidence)
        await self.gateway.send_json(
            MessageType.TEXT_MESSAGE,
            {"name": "爱莉", "content": "我在，你说吧。"},
        )
        if cached_pcm:
            packets = self.codec.encode_speech(cached_pcm)
            frame_bytes = (
                OpusCodec.SPEECH_SAMPLE_RATE
                * OpusCodec.SPEECH_FRAME_DURATION_MS
                // 1000
                * 2
            )
            avatar_interval_frames = max(
                1, round(100 / OpusCodec.SPEECH_FRAME_DURATION_MS)
            )
            for packet_index, packet in enumerate(packets):
                if packet_index % avatar_interval_frames == 0:
                    start = packet_index * frame_bytes
                    end = min(
                        len(cached_pcm),
                        (packet_index + avatar_interval_frames) * frame_bytes,
                    )
                    await self._show_speaking_frame(
                        self._mouth_level(cached_pcm[start:end])
                    )
                await self.gateway.send(MessageType.OPUS, packet)
                await asyncio.sleep(OpusCodec.SPEECH_FRAME_DURATION_MS / 1000)
            await self._show_speaking_frame(0)
        await asyncio.sleep(0.12)
        if wake_task is not None and not wake_task.done():
            try:
                await asyncio.wait_for(asyncio.shield(wake_task), timeout=0.8)
            except asyncio.TimeoutError:
                pass
        await self.gateway.send(MessageType.START_AUDIO_STREAM)
        self._clear_audio()
        self._ignore_audio_until = time.monotonic() + 0.35
        self._activate_wake_session()
        self._set_mode(self._idle_mode())
        await self._show_avatar("listening")

    def _classify_wake_audio(self) -> ChildVoiceEvidence:
        pcm = b"".join(self._wake_audio)
        self._wake_audio.clear()
        evidence = self._child_voice_classifier.classify(pcm)
        self._record_child_voice_evidence(evidence)
        return evidence

    def _record_child_voice_evidence(
        self, evidence: ChildVoiceEvidence
    ) -> None:
        self.state.last_wake_child_voice = evidence.is_child
        self.state.last_wake_pitch_hz = evidence.median_pitch_hz
        self.state.last_wake_voiced_ratio = evidence.voiced_ratio

    async def _show_avatar(self, emotion: str) -> None:
        if self.avatar_controller is None:
            return
        try:
            await self.avatar_controller.show(emotion)
        except Exception as exc:
            # A missing or transient display asset must never break speech.
            logger.warning("avatar update failed for %s: %s", emotion, exc)

    async def _show_speaking_frame(self, level: int) -> None:
        if self.avatar_controller is None:
            return
        try:
            await self.avatar_controller.show_speaking_frame(level)
        except Exception as exc:
            # Animation is decorative and must never interrupt audio playback.
            logger.warning(
                "speaking avatar update failed for level %s: %s", level, exc
            )

    @staticmethod
    def _mouth_level(pcm: bytes) -> int:
        if not pcm:
            return 0
        rms = audioop.rms(pcm, 2)
        if rms < 900:
            return 0
        if rms < 5000:
            return 1
        return 2

    def _clear_audio(self) -> None:
        self._pre_roll.clear()
        self._utterance.clear()
        self._speaking_detected = False
        self._silence_ms = 0

    @staticmethod
    def _normalize_pcm(pcm: bytes) -> bytes:
        if not pcm:
            return pcm
        centered = audioop.bias(pcm, 2, -audioop.avg(pcm, 2))
        peak = audioop.max(centered, 2)
        if peak < 100:
            return centered
        gain = min(12.0, 24000 / peak)
        return audioop.mul(centered, 2, gain)

    @staticmethod
    def _maximize_speech_pcm(pcm: bytes) -> bytes:
        """Peak-normalize synthesized speech to safe 16-bit full scale."""
        if not pcm:
            return pcm
        peak = audioop.max(pcm, 2)
        if peak < 1:
            return pcm
        gain = min(8.0, 32700 / peak)
        return audioop.mul(pcm, 2, gain)

    @staticmethod
    def _wav(pcm: bytes) -> bytes:
        output = io.BytesIO()
        with wave.open(output, "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(16000)
            wav.writeframes(pcm)
        return output.getvalue()
