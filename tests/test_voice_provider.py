import asyncio
from pathlib import Path

import httpx
import pytest

from stackchan_control.settings import PROJECT_ROOT, Settings
from stackchan_control.voice import (
    LocalDeepSeekVoiceProvider,
    NoSpeechDetected,
)


class FakeDeepSeekClient:
    request_json: dict[str, object] | None = None
    request_url: str | None = None

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None

    async def post(self, url: str, **kwargs):
        type(self).request_url = url
        type(self).request_json = kwargs["json"]
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "你好，我是波西。"}}]},
            request=httpx.Request("POST", url),
        )


class FakeNeuralTtsClient:
    request_json: dict[str, object] | None = None

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None

    async def post(self, url: str, **kwargs):
        type(self).request_json = kwargs["json"]
        return httpx.Response(
            200,
            content=b"RIFF-fake-wav",
            request=httpx.Request("POST", url),
        )


def test_deepseek_text_provider_uses_v4_without_thinking(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    model_path = tmp_path / "ggml-small.bin"
    model_path.write_bytes(b"model")
    monkeypatch.setattr("stackchan_control.voice.shutil.which", lambda _: "/usr/bin/tool")
    monkeypatch.setattr("stackchan_control.voice.httpx.AsyncClient", FakeDeepSeekClient)
    settings = Settings(
        db_path=tmp_path / "voice.db",
        seed_character_dir=PROJECT_ROOT / "config" / "seed_character",
        web_dir=PROJECT_ROOT / "web",
        deepseek_api_key="secret",
        deepseek_model="deepseek-v4-flash",
        voice_whisper_model=model_path,
    )

    provider = LocalDeepSeekVoiceProvider(settings)
    answer = asyncio.run(provider.answer("system rules", "你好"))

    assert answer == "你好，我是波西。"
    assert FakeDeepSeekClient.request_url == "https://api.deepseek.com/chat/completions"
    assert FakeDeepSeekClient.request_json == {
        "model": "deepseek-v4-flash",
        "messages": [
            {"role": "system", "content": "system rules"},
            {"role": "user", "content": "你好"},
        ],
        "thinking": {"type": "disabled"},
        "max_tokens": 240,
        "stream": False,
    }


@pytest.mark.parametrize(
    "text",
    ["", "[BLANK_AUDIO]", "MBC 뉴스 이덕영입니다.", "(字幕製作:貝爾)"],
)
def test_local_whisper_rejects_silence_and_out_of_scope_hallucinations(text: str):
    with pytest.raises(NoSpeechDetected):
        LocalDeepSeekVoiceProvider._clean_transcript(text)


def test_local_whisper_accepts_chinese_and_english():
    assert LocalDeepSeekVoiceProvider._clean_transcript("  你好， StackChan  ") == "你好， StackChan"
    assert LocalDeepSeekVoiceProvider._clean_transcript("HelloHello") == "Hello"


def test_local_whisper_rejects_repeated_out_of_scope_characters():
    with pytest.raises(NoSpeechDetected):
        LocalDeepSeekVoiceProvider._clean_transcript("ლლლლლლლ")


def test_deepseek_messages_include_bounded_conversation_history():
    messages = LocalDeepSeekVoiceProvider._messages(
        "system rules",
        "那第二个呢？",
        [
            {"role": "user", "content": "给我两个选择"},
            {"role": "assistant", "content": "第一个是画画，第二个是拼图。"},
        ],
    )

    assert [message["role"] for message in messages] == [
        "system",
        "user",
        "assistant",
        "user",
    ]
    assert messages[-1]["content"] == "那第二个呢？"


def test_streaming_answer_is_split_on_natural_sentence_boundaries():
    segments, tail = LocalDeepSeekVoiceProvider._pop_spoken_segments(
        "我们先画一只猫。然后给它加上帽子！还有一点"
    )

    assert segments == ["我们先画一只猫。", "然后给它加上帽子！"]
    assert tail == "还有一点"


def test_local_neural_tts_uses_qwen_serena_and_bounded_tokens(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    model_path = tmp_path / "ggml-small.bin"
    model_path.write_bytes(b"model")
    monkeypatch.setattr("stackchan_control.voice.shutil.which", lambda _: "/usr/bin/tool")
    monkeypatch.setattr("stackchan_control.voice.httpx.AsyncClient", FakeNeuralTtsClient)

    async def fake_convert(_provider, audio: bytes) -> bytes:
        assert audio == b"RIFF-fake-wav"
        return b"pcm"

    monkeypatch.setattr(
        LocalDeepSeekVoiceProvider, "_convert_audio_to_pcm", fake_convert
    )
    settings = Settings(
        db_path=tmp_path / "voice.db",
        seed_character_dir=PROJECT_ROOT / "config" / "seed_character",
        web_dir=PROJECT_ROOT / "web",
        deepseek_api_key="secret",
        voice_whisper_model=model_path,
    )

    provider = LocalDeepSeekVoiceProvider(settings)
    pcm = asyncio.run(provider._synthesize_neural("你好，我是波西。"))

    assert pcm == b"pcm"
    assert FakeNeuralTtsClient.request_json is not None
    assert FakeNeuralTtsClient.request_json["voice"] == "Vivian"
    assert FakeNeuralTtsClient.request_json["speed"] == 1.08
    assert FakeNeuralTtsClient.request_json["lang_code"] == "Chinese"
    assert FakeNeuralTtsClient.request_json["max_tokens"] == 48
