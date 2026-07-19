from __future__ import annotations

import asyncio
import audioop
import ctypes
import ctypes.util
import io
import json
import re
import shutil
import tempfile
import time
import wave
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Protocol
from pathlib import Path

import httpx

from .gateway import DeviceOfflineError, MessageType, StackChanGateway
from .repository import RobotRepository
from .settings import Settings


class VoiceError(RuntimeError):
    pass


class NoSpeechDetected(VoiceError):
    pass


class VoiceMode(str, Enum):
    STOPPED = "stopped"
    LISTENING = "listening"
    TRANSCRIBING = "transcribing"
    THINKING = "thinking"
    SPEAKING = "speaking"
    ERROR = "error"


class VoiceProvider(Protocol):
    async def transcribe(self, wav_audio: bytes) -> str: ...

    async def answer(self, instructions: str, transcript: str) -> str: ...

    async def synthesize(self, text: str) -> bytes: ...


class LocalDeepSeekVoiceProvider:
    """Local ASR/TTS with DeepSeek receiving text only."""

    def __init__(self, settings: Settings) -> None:
        if not settings.deepseek_api_key:
            raise VoiceError("DEEPSEEK_API_KEY is required for voice conversations")
        self.api_key = settings.deepseek_api_key
        self.base_url = settings.deepseek_base_url.rstrip("/")
        self.model = settings.deepseek_model
        self.whisper_binary = settings.voice_whisper_binary
        self.whisper_model = settings.voice_whisper_model
        self.zh_voice = settings.voice_zh_name
        self.en_voice = settings.voice_en_name

        if shutil.which(self.whisper_binary) is None:
            raise VoiceError(f"local Whisper binary was not found: {self.whisper_binary}")
        if not self.whisper_model.is_file():
            raise VoiceError(f"local Whisper model was not found: {self.whisper_model}")
        if shutil.which("say") is None or shutil.which("ffmpeg") is None:
            raise VoiceError("local speech synthesis requires macOS say and ffmpeg")

    @property
    def headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.api_key}"}

    async def transcribe(self, wav_audio: bytes) -> str:
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
        if (
            not compact
            or compact.upper() in {"[BLANK_AUDIO]", "[NO SPEECH]", "(BLANK AUDIO)"}
            or re.search(r"[\uac00-\ud7af]", compact)
            or re.search(r"字幕.{0,4}(製作|制作|提供)", compact)
        ):
            raise NoSpeechDetected("no Chinese or English speech detected")
        return compact

    async def answer(self, instructions: str, transcript: str) -> str:
        async with httpx.AsyncClient(timeout=60) as client:
            response = await client.post(
                f"{self.base_url}/chat/completions",
                headers={**self.headers, "Content-Type": "application/json"},
                json={
                    "model": self.model,
                    "messages": [
                        {"role": "system", "content": instructions},
                        {"role": "user", "content": transcript},
                    ],
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

    async def synthesize(self, text: str) -> bytes:
        voice = self.zh_voice if re.search(r"[\u3400-\u9fff]", text) else self.en_voice
        with tempfile.TemporaryDirectory(prefix="stackchan-tts-") as temp_dir:
            aiff_path = Path(temp_dir) / "speech.aiff"
            say_process = await asyncio.create_subprocess_exec(
                "say",
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

            convert_process = await asyncio.create_subprocess_exec(
                "ffmpeg",
                "-nostdin",
                "-v",
                "error",
                "-i",
                str(aiff_path),
                "-f",
                "s16le",
                "-acodec",
                "pcm_s16le",
                "-ar",
                "24000",
                "-ac",
                "1",
                "pipe:1",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            pcm, convert_stderr = await convert_process.communicate()
            if convert_process.returncode != 0:
                detail = convert_stderr.decode("utf-8", errors="replace").strip()[-240:]
                raise VoiceError(f"local audio conversion failed: {detail}")
        if not pcm:
            raise VoiceError("local speech synthesis returned no audio")
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
        frame_bytes = 24000 * 60 // 1000 * 2
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
    transcript: str | None = None
    response_text: str | None = None
    error: str | None = None
    audio_rms: int = 0
    audio_peak_rms: int = 0
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def snapshot(self) -> dict[str, object]:
        return {
            "mode": self.mode.value,
            "enabled": self.enabled,
            "user_id": self.user_id,
            "turn_id": self.turn_id,
            "transcript": self.transcript,
            "response_text": self.response_text,
            "error": self.error,
            "audio_rms": self.audio_rms,
            "audio_peak_rms": self.audio_peak_rms,
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
    ) -> None:
        self.settings = settings
        self.repository = repository
        self.gateway = gateway
        self.provider = provider
        self.codec = codec
        self.state = VoiceState(user_id=settings.voice_user_id)
        self._pre_roll: deque[bytes] = deque(maxlen=5)
        self._utterance: list[bytes] = []
        self._speaking_detected = False
        self._silence_ms = 0
        self._ignore_audio_until = 0.0
        self._task: asyncio.Task[None] | None = None
        self._lock = asyncio.Lock()

    def _ensure_codec(self) -> None:
        if self.codec is None:
            self.codec = OpusCodec()

    def _ensure_provider(self) -> None:
        if self.provider is None:
            self.provider = LocalDeepSeekVoiceProvider(self.settings)

    async def start(self, user_id: str | None = None) -> dict[str, object]:
        target_user = user_id or self.state.user_id
        user = self.repository.get_user(target_user)
        if not user["enabled"]:
            raise VoiceError(f"user {target_user} is disabled")
        self._ensure_codec()
        if not await self.gateway.is_online():
            raise DeviceOfflineError("device is offline")
        async with self._lock:
            self.state.enabled = True
            self.state.user_id = target_user
            self._set_mode(VoiceMode.LISTENING)
            self.state.error = None
            self.state.transcript = None
            self.state.response_text = None
            self.state.audio_rms = 0
            self.state.audio_peak_rms = 0
            self._clear_audio()
            self._ignore_audio_until = time.monotonic() + 0.35
        try:
            await self.gateway.send(MessageType.START_AUDIO_STREAM)
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
            self.state.error = None
            self._set_mode(VoiceMode.STOPPED)
        if await self.gateway.is_online():
            await self.gateway.send(MessageType.STOP_AUDIO_STREAM)
        return self.state.snapshot()

    async def interrupt(self) -> dict[str, object]:
        async with self._lock:
            if self._task and not self._task.done():
                self._task.cancel()
            self._task = None
            self._clear_audio()
            self._set_mode(VoiceMode.LISTENING if self.state.enabled else VoiceMode.STOPPED)
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
        self.state.enabled = False
        self._set_mode(VoiceMode.STOPPED)

    async def ingest_opus(self, payload: bytes) -> None:
        if not self.state.enabled or self.state.mode != VoiceMode.LISTENING:
            return
        if time.monotonic() < self._ignore_audio_until:
            return
        self._ensure_codec()
        assert self.codec is not None
        pcm = self.codec.decode_microphone(payload)
        self._pre_roll.append(pcm)
        rms = audioop.rms(pcm, 2)
        self.state.audio_rms = rms
        self.state.audio_peak_rms = max(self.state.audio_peak_rms, rms)
        if rms >= 350:
            if not self._speaking_detected:
                self._utterance.extend(self._pre_roll)
                self._speaking_detected = True
            else:
                self._utterance.append(pcm)
            self._silence_ms = 0
        elif self._speaking_detected:
            self._utterance.append(pcm)
            self._silence_ms += 60
            if self._silence_ms >= self.settings.voice_silence_ms:
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
        self.state.turn_id += 1
        self.state.response_text = None
        self.state.error = None
        self._task = asyncio.create_task(self._run_turn(transcript.strip(), None))
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
        self.state.turn_id += 1
        self.state.transcript = None
        self.state.response_text = None
        self.state.error = None
        self._task = asyncio.create_task(self._run_turn(None, pcm))

    async def _run_turn(self, transcript: str | None, pcm: bytes | None) -> None:
        microphone_paused = False
        try:
            self._ensure_provider()
            assert self.provider is not None
            if transcript is None:
                self._set_mode(VoiceMode.TRANSCRIBING)
                transcript = await self.provider.transcribe(self._wav(pcm or b""))
            self.state.transcript = transcript
            self._set_mode(VoiceMode.THINKING)
            instructions = self._instructions()
            answer = await self.provider.answer(instructions, transcript)
            self.state.response_text = answer
            self._set_mode(VoiceMode.SPEAKING)
            await self.gateway.send(MessageType.STOP_AUDIO_STREAM)
            microphone_paused = True
            self._clear_audio()
            await self.gateway.send_json(
                MessageType.TEXT_MESSAGE,
                {"name": "小栈", "content": answer[:240]},
            )
            speech_pcm = await self.provider.synthesize(answer)
            assert self.codec is not None
            for packet in self.codec.encode_speech(speech_pcm):
                await self.gateway.send(MessageType.OPUS, packet)
                await asyncio.sleep(0.06)
            await asyncio.sleep(0.24)
            if self.state.enabled and await self.gateway.is_online():
                self._clear_audio()
                await self.gateway.send(MessageType.START_AUDIO_STREAM)
                self._ignore_audio_until = time.monotonic() + 1.2
                microphone_paused = False
            self.state.error = None
            self._set_mode(VoiceMode.LISTENING if self.state.enabled else VoiceMode.STOPPED)
        except asyncio.CancelledError:
            self._set_mode(VoiceMode.LISTENING if self.state.enabled else VoiceMode.STOPPED)
            raise
        except NoSpeechDetected:
            self.state.transcript = None
            self.state.response_text = None
            self.state.error = None
            self._clear_audio()
            self._ignore_audio_until = time.monotonic() + 0.5
            self._set_mode(VoiceMode.LISTENING if self.state.enabled else VoiceMode.STOPPED)
        except Exception as exc:
            self.state.error = str(exc)[:240]
            self._set_mode(VoiceMode.ERROR)
        finally:
            if microphone_paused and self.state.enabled and await self.gateway.is_online():
                self._clear_audio()
                try:
                    await self.gateway.send(MessageType.START_AUDIO_STREAM)
                    self._ignore_audio_until = time.monotonic() + 1.2
                except DeviceOfflineError:
                    pass

    def _instructions(self) -> str:
        preview = self.repository.prompt_preview()
        user = self.repository.get_user(self.state.user_id)
        memories = self.repository.list_memories(
            self.state.user_id, include_pending=False
        )[:8]
        memory_text = "\n".join(f"- {item['content']}" for item in memories) or "- 无已确认记忆"
        return (
            f"{preview['system_prompt']}\n\n"
            "当前是语音对话。只输出适合直接朗读的回答，不输出 Markdown、URL、JSON 或内部过程。\n"
            f"当前用户：{user['display_name']}；角色：{user['role']}；语言偏好：{user['locale']}。\n"
            f"仅可使用该用户自己的已确认记忆：\n{memory_text}"
        )

    def _set_mode(self, mode: VoiceMode) -> None:
        self.state.mode = mode
        self.state.updated_at = datetime.now(timezone.utc)

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
    def _wav(pcm: bytes) -> bytes:
        output = io.BytesIO()
        with wave.open(output, "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(16000)
            wav.writeframes(pcm)
        return output.getvalue()
