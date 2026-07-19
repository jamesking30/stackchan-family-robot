import time
from pathlib import Path

from fastapi.testclient import TestClient

from stackchan_control.app import create_app
from stackchan_control.gateway import MessageType, pack_frame, unpack_frame
from stackchan_control.settings import PROJECT_ROOT, Settings


ADMIN_HEADERS = {"X-Robot-Admin-Key": "admin-secret"}
DEVICE_HEADERS = {"Authorization": "device-secret"}
WS_PATH = "/stackChan/ws?deviceType=StackChan"


class FakeVoiceProvider:
    def __init__(self):
        self.instructions = ""
        self.transcript = ""

    async def transcribe(self, wav_audio: bytes) -> str:
        assert wav_audio.startswith(b"RIFF")
        return "你好，小栈"

    async def answer(self, instructions: str, transcript: str) -> str:
        self.instructions = instructions
        self.transcript = transcript
        return "你好！很高兴见到你。"

    async def synthesize(self, text: str) -> bytes:
        assert text == "你好！很高兴见到你。"
        return b"\x00\x00" * 1440


class FakeOpusCodec:
    def decode_microphone(self, packet: bytes) -> bytes:
        assert packet == b"microphone-opus"
        return (1000).to_bytes(2, "little", signed=True) * 960

    def encode_speech(self, pcm: bytes) -> list[bytes]:
        assert pcm
        return [b"speaker-opus"]


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
            assert response.json()["mode"] == "listening"
            assert unpack_frame(websocket.receive_bytes()).message_type == MessageType.START_AUDIO_STREAM

            websocket.send_bytes(pack_frame(MessageType.VOICE_ACTIVITY, b"\x01"))
            for _ in range(6):
                websocket.send_bytes(pack_frame(MessageType.OPUS, b"microphone-opus"))
            websocket.send_bytes(pack_frame(MessageType.VOICE_ACTIVITY, b"\x00"))

            deadline = time.monotonic() + 2
            state = {}
            while time.monotonic() < deadline:
                state = client.get("/v1/voice/state", headers=ADMIN_HEADERS).json()
                if state.get("response_text"):
                    break
                time.sleep(0.01)

            text_frame = unpack_frame(websocket.receive_bytes())
            audio_frame = unpack_frame(websocket.receive_bytes())
            assert text_frame.message_type == MessageType.TEXT_MESSAGE
            assert audio_frame.message_type == MessageType.OPUS
            assert audio_frame.payload == b"speaker-opus"
            assert state["transcript"] == "你好，小栈"
            assert state["response_text"] == "你好！很高兴见到你。"
            assert provider.transcript == "你好，小栈"
            assert "角色：unassigned" in provider.instructions
            assert "家庭与儿童安全优先级" in provider.instructions

            stopped = client.post("/v1/voice/stop", headers=ADMIN_HEADERS).json()
            assert stopped["mode"] == "stopped"
            assert unpack_frame(websocket.receive_bytes()).message_type == MessageType.STOP_AUDIO_STREAM


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
