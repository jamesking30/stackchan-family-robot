import json
import time
import wave
from pathlib import Path

from fastapi.testclient import TestClient

from stackchan_control.app import create_app
from stackchan_control.gateway import MessageType, pack_frame, unpack_frame
from stackchan_control.settings import PROJECT_ROOT, Settings
from stackchan_control.voice import OpusCodec, VoiceSessionManager


ADMIN_HEADERS = {"X-Robot-Admin-Key": "admin-secret"}
DEVICE_HEADERS = {"Authorization": "device-secret"}
WS_PATH = "/stackChan/ws?deviceType=StackChan"


def write_pcm_wav(path: Path, pcm: bytes, sample_rate: int = 24000) -> None:
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(pcm)


class FakeVoiceProvider:
    def __init__(self, transcript: str = "爱莉，你好"):
        self.instructions = ""
        self.transcript = ""
        self.transcribed_text = transcript
        self.transcribe_calls = 0
        self.answer_calls = 0
        self.synthesized_text = ""

    async def transcribe(self, wav_audio: bytes) -> str:
        assert wav_audio.startswith(b"RIFF")
        self.transcribe_calls += 1
        return self.transcribed_text

    async def answer(
        self,
        instructions: str,
        transcript: str,
        history: list[dict[str, str]] | None = None,
    ) -> str:
        self.answer_calls += 1
        self.instructions = instructions
        self.transcript = transcript
        return "你好！很高兴见到你。"

    async def synthesize(self, text: str) -> bytes:
        self.synthesized_text = text
        return b"\x00\x00" * 1440


class FakeOpusCodec:
    def decode_microphone(self, packet: bytes) -> bytes:
        if packet == b"silence-opus":
            return b"\x00\x00" * 960
        assert packet == b"microphone-opus"
        return (1000).to_bytes(2, "little", signed=True) * 960

    def encode_speech(self, pcm: bytes) -> list[bytes]:
        assert pcm
        return [b"speaker-opus"]


class FakeWakeDetector:
    last_frame_latency_ms = 3.2

    def __init__(self):
        self.triggered = False

    def accept_pcm(self, pcm: bytes) -> str | None:
        if not self.triggered:
            self.triggered = True
            return "爱莉"
        return None

    def reset(self) -> None:
        self.triggered = False


def test_bilingual_voice_turn_stays_in_memory_and_reaches_robot(tmp_path: Path):
    provider = FakeVoiceProvider()
    settings = Settings(
        db_path=tmp_path / "voice.db",
        seed_character_dir=PROJECT_ROOT / "config" / "seed_character",
        web_dir=PROJECT_ROOT / "web",
        admin_api_key="admin-secret",
        device_api_key="device-secret",
        gateway_heartbeat_seconds=1,
        gateway_timeout_seconds=3,
        voice_min_speech_ms=300,
    )
    with TestClient(
        create_app(settings, voice_provider=provider, voice_codec=FakeOpusCodec())
    ) as client:
        with client.websocket_connect(WS_PATH, headers=DEVICE_HEADERS) as websocket:
            response = client.post(
                "/v1/voice/start",
                headers=ADMIN_HEADERS,
                json={"user_id": "user-2"},
            )
            assert response.status_code == 200
            assert response.json()["mode"] == "waiting_for_wake_word"
            assert response.json()["wake_word"] == "爱莉"
            assert response.json()["awake"] is False
            assert unpack_frame(websocket.receive_bytes()).message_type == MessageType.START_AUDIO_STREAM
            time.sleep(0.4)

            websocket.send_bytes(pack_frame(MessageType.VOICE_ACTIVITY, b"\x01"))
            for _ in range(6):
                websocket.send_bytes(pack_frame(MessageType.OPUS, b"microphone-opus"))
            for _ in range(13):
                websocket.send_bytes(pack_frame(MessageType.OPUS, b"silence-opus"))
            websocket.send_bytes(pack_frame(MessageType.VOICE_ACTIVITY, b"\x00"))

            deadline = time.monotonic() + 2
            state = {}
            while time.monotonic() < deadline:
                state = client.get("/v1/voice/state", headers=ADMIN_HEADERS).json()
                if state.get("response_text"):
                    break
                time.sleep(0.01)

            microphone_pause = unpack_frame(websocket.receive_bytes())
            text_frame = unpack_frame(websocket.receive_bytes())
            audio_frame = unpack_frame(websocket.receive_bytes())
            microphone_resume = unpack_frame(websocket.receive_bytes())
            assert microphone_pause.message_type == MessageType.STOP_AUDIO_STREAM
            assert text_frame.message_type == MessageType.TEXT_MESSAGE
            assert audio_frame.message_type == MessageType.OPUS
            assert audio_frame.payload == b"speaker-opus"
            assert microphone_resume.message_type == MessageType.START_AUDIO_STREAM
            assert state["transcript"] == "你好"
            assert state["response_text"] == "你好！很高兴见到你。"
            assert state["awake"] is True
            assert provider.transcript == "你好"
            assert "角色：unassigned" in provider.instructions
            assert "内容与行动边界" in provider.instructions

            stopped = client.post("/v1/voice/stop", headers=ADMIN_HEADERS).json()
            assert stopped["mode"] == "stopped"
            assert unpack_frame(websocket.receive_bytes()).message_type == MessageType.STOP_AUDIO_STREAM


def test_background_speech_does_not_reach_deepseek(tmp_path: Path):
    provider = FakeVoiceProvider("今天晚上吃什么")
    settings = Settings(
        db_path=tmp_path / "wake-gate.db",
        seed_character_dir=PROJECT_ROOT / "config" / "seed_character",
        web_dir=PROJECT_ROOT / "web",
        admin_api_key="admin-secret",
        device_api_key="device-secret",
        voice_min_speech_ms=300,
    )
    with TestClient(
        create_app(settings, voice_provider=provider, voice_codec=FakeOpusCodec())
    ) as client:
        with client.websocket_connect(WS_PATH, headers=DEVICE_HEADERS) as websocket:
            state = client.post(
                "/v1/voice/start",
                headers=ADMIN_HEADERS,
                json={"user_id": "user-2"},
            ).json()
            assert state["mode"] == "waiting_for_wake_word"
            assert unpack_frame(websocket.receive_bytes()).message_type == MessageType.START_AUDIO_STREAM
            time.sleep(0.4)

            for _ in range(6):
                websocket.send_bytes(pack_frame(MessageType.OPUS, b"microphone-opus"))
            for _ in range(13):
                websocket.send_bytes(pack_frame(MessageType.OPUS, b"silence-opus"))

            deadline = time.monotonic() + 2
            while time.monotonic() < deadline and provider.transcribe_calls == 0:
                time.sleep(0.01)
            while time.monotonic() < deadline:
                state = client.get("/v1/voice/state", headers=ADMIN_HEADERS).json()
                if provider.transcribe_calls and state["mode"] == "waiting_for_wake_word":
                    break
                time.sleep(0.01)

            assert provider.transcribe_calls == 1
            assert state["awake"] is False
            assert state["turn_id"] == 0
            assert state["transcript"] is None
            assert state["response_text"] is None
            assert provider.answer_calls == 0


def test_streaming_keyword_spotter_acks_before_full_transcription(tmp_path: Path):
    detector = FakeWakeDetector()
    ack_path = tmp_path / "wake-ack-v2.wav"
    write_pcm_wav(ack_path, b"\x01\x00" * 320)
    settings = Settings(
        db_path=tmp_path / "kws.db",
        seed_character_dir=PROJECT_ROOT / "config" / "seed_character",
        web_dir=PROJECT_ROOT / "web",
        admin_api_key="admin-secret",
        device_api_key="device-secret",
        voice_kws_enabled=True,
        voice_wake_ack_pcm=ack_path,
    )
    with TestClient(
        create_app(
            settings,
            voice_provider=FakeVoiceProvider(),
            voice_codec=FakeOpusCodec(),
            wake_detector=detector,
        )
    ) as client:
        wake_callbacks: list[str] = []

        async def on_wake():
            wake_callbacks.append("called")

        client.app.state.voice.set_wake_callback(on_wake)
        with client.websocket_connect(WS_PATH, headers=DEVICE_HEADERS) as websocket:
            client.post(
                "/v1/voice/start",
                headers=ADMIN_HEADERS,
                json={"user_id": "user-2"},
            )
            assert unpack_frame(websocket.receive_bytes()).message_type == MessageType.START_AUDIO_STREAM
            time.sleep(0.4)

            websocket.send_bytes(pack_frame(MessageType.OPUS, b"microphone-opus"))
            microphone_pause = unpack_frame(websocket.receive_bytes())
            feedback = unpack_frame(websocket.receive_bytes())
            audio = unpack_frame(websocket.receive_bytes())
            microphone_resume = unpack_frame(websocket.receive_bytes())
            assert microphone_pause.message_type == MessageType.STOP_AUDIO_STREAM
            assert feedback.message_type == MessageType.TEXT_MESSAGE
            assert json.loads(feedback.payload)["content"] == "我在，你说吧。"
            assert audio.message_type == MessageType.OPUS
            assert audio.payload == b"speaker-opus"
            assert microphone_resume.message_type == MessageType.START_AUDIO_STREAM

            state = client.get("/v1/voice/state", headers=ADMIN_HEADERS).json()
            assert state["awake"] is True
            assert state["last_wake_keyword"] == "爱莉"
            assert state["mode"] == "listening"
            assert state["latency_ms"]["kws_frame"] == 3.2
            deadline = time.monotonic() + 1
            while time.monotonic() < deadline and not wake_callbacks:
                time.sleep(0.01)
            assert wake_callbacks == ["called"]

            client.post("/v1/voice/stop", headers=ADMIN_HEADERS)
            assert unpack_frame(websocket.receive_bytes()).message_type == MessageType.STOP_AUDIO_STREAM


def test_wake_word_variants_and_sleep_phrases(tmp_path: Path):
    settings = Settings(
        db_path=tmp_path / "wake-parser.db",
        seed_character_dir=PROJECT_ROOT / "config" / "seed_character",
        web_dir=PROJECT_ROOT / "web",
    )
    manager = VoiceSessionManager(settings, None, None)  # type: ignore[arg-type]

    assert manager._extract_wake_command("爱莉！今天天气怎么样") == "今天天气怎么样"
    assert manager._extract_wake_command("艾莉 讲个故事") == "讲个故事"
    assert manager._extract_wake_command("Ai Li, hello") == "hello"
    assert manager._extract_wake_command("Ali!") == ""
    assert manager._extract_wake_command("Ally, tell me a story") == "tell me a story"
    assert manager._extract_wake_command("我在说爱莉") is None
    assert manager._is_sleep_phrase("休息吧！") is True


def test_spoken_answer_removes_markup_urls_and_emoji():
    cleaned = VoiceSessionManager._clean_spoken_answer(
        "**好的** 😊 详见 https://example.com/test"
    )

    assert cleaned == "好的 详见"


def test_voice_requires_online_device_and_admin_key(tmp_path: Path):
    settings = Settings(
        db_path=tmp_path / "voice-offline.db",
        seed_character_dir=PROJECT_ROOT / "config" / "seed_character",
        web_dir=PROJECT_ROOT / "web",
        admin_api_key="admin-secret",
        device_api_key="device-secret",
    )
    with TestClient(
        create_app(settings, voice_provider=FakeVoiceProvider(), voice_codec=FakeOpusCodec())
    ) as client:
        assert client.get("/v1/voice/state").status_code == 401
        response = client.post(
            "/v1/voice/start",
            headers=ADMIN_HEADERS,
            json={"user_id": "user-2"},
        )
        assert response.status_code == 409


def test_quiet_microphone_pcm_is_centered_and_amplified():
    pcm = b"".join(
        sample.to_bytes(2, "little", signed=True) for sample in (-1000, 1000) * 32
    )

    normalized = VoiceSessionManager._normalize_pcm(pcm)

    assert int.from_bytes(normalized[:2], "little", signed=True) == -12000
    assert int.from_bytes(normalized[2:4], "little", signed=True) == 12000


def test_synthesized_speech_is_peak_normalized_to_full_scale():
    pcm = b"".join(
        sample.to_bytes(2, "little", signed=True) for sample in (-10000, 10000) * 32
    )

    maximized = VoiceSessionManager._maximize_speech_pcm(pcm)

    assert 32690 <= max(
        abs(int.from_bytes(maximized[index : index + 2], "little", signed=True))
        for index in range(0, len(maximized), 2)
    ) <= 32700


def test_mouth_level_tracks_speech_energy():
    def pcm(sample: int) -> bytes:
        return sample.to_bytes(2, "little", signed=True) * 2400

    assert VoiceSessionManager._mouth_level(pcm(0)) == 0
    assert VoiceSessionManager._mouth_level(pcm(2000)) == 1
    assert VoiceSessionManager._mouth_level(pcm(9000)) == 2


def test_cached_wake_ack_is_loaded_from_runtime_cache(tmp_path: Path):
    cached_pcm = b"\x01\x00" * 320
    ack_path = tmp_path / "wake-ack-v2.wav"
    write_pcm_wav(ack_path, cached_pcm)
    settings = Settings(
        db_path=tmp_path / "wake-cache.db",
        seed_character_dir=PROJECT_ROOT / "config" / "seed_character",
        web_dir=PROJECT_ROOT / "web",
        voice_wake_ack_pcm=ack_path,
    )
    manager = VoiceSessionManager(settings, None, None)  # type: ignore[arg-type]

    assert manager._load_wake_ack_pcm() == cached_pcm


def test_cached_wake_ack_rejects_wrong_sample_rate(tmp_path: Path):
    ack_path = tmp_path / "wake-ack-v2.wav"
    write_pcm_wav(ack_path, b"\x01\x00" * 320, sample_rate=16000)
    settings = Settings(
        db_path=tmp_path / "wake-cache.db",
        seed_character_dir=PROJECT_ROOT / "config" / "seed_character",
        web_dir=PROJECT_ROOT / "web",
        voice_wake_ack_pcm=ack_path,
    )
    manager = VoiceSessionManager(settings, None, None)  # type: ignore[arg-type]

    assert manager._load_wake_ack_pcm() is None


def test_opus_speech_uses_twenty_millisecond_frames():
    codec = OpusCodec()
    try:
        pcm_100ms = b"\x00\x00" * (24000 // 10)
        packets = codec.encode_speech(pcm_100ms)
    finally:
        codec.close()

    assert len(packets) == 5
    assert all(packets)
