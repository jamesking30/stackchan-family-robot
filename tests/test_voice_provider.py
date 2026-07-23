import asyncio
from pathlib import Path

import httpx
import pytest

from stackchan_control.settings import PROJECT_ROOT, Settings
from stackchan_control.voice import (
    LocalDeepSeekVoiceProvider,
    NoSpeechDetected,
    VoiceError,
    resolve_local_executable,
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
            json={"choices": [{"message": {"content": "你好，我是爱莉。"}}]},
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


class FakeGptSovitsClient:
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
            content=b"RIFF-gpt-sovits-wav",
            request=httpx.Request("POST", url),
        )


class FakeWhisperServerClient:
    request_files: dict[str, tuple[str, bytes, str]] | None = None
    request_data: dict[str, str] | None = None

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None

    async def post(self, url: str, **kwargs):
        type(self).request_files = kwargs["files"]
        type(self).request_data = kwargs["data"]
        return httpx.Response(
            200,
            json={"text": "爱莉你好\n"},
            request=httpx.Request("POST", url),
        )


def test_resolve_local_executable_uses_homebrew_when_path_is_minimal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    homebrew_bin = tmp_path / "opt" / "homebrew" / "bin"
    homebrew_bin.mkdir(parents=True)
    executable = homebrew_bin / "whisper-cli"
    executable.write_text("#!/bin/sh\n", encoding="utf-8")
    monkeypatch.setattr("stackchan_control.voice.shutil.which", lambda _: None)
    monkeypatch.setattr(
        "stackchan_control.voice.Path",
        lambda value: (
            executable.parent
            if value == "/opt/homebrew/bin"
            else Path(value)
        ),
    )

    assert resolve_local_executable("whisper-cli") == str(executable)


def test_resolve_local_executable_rejects_missing_absolute_path(tmp_path: Path):
    with pytest.raises(VoiceError, match="local executable was not found"):
        resolve_local_executable(str(tmp_path / "missing"))


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

    assert answer == "你好，我是爱莉。"
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


def test_local_openai_compatible_model_can_replace_deepseek(
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
        local_llm_enabled=True,
        local_llm_base_url="http://127.0.0.1:8080/v1",
        local_llm_model="local-model",
        voice_whisper_model=model_path,
    )

    answer = asyncio.run(
        LocalDeepSeekVoiceProvider(settings).answer("system rules", "你好")
    )

    assert answer == "你好，我是爱莉。"
    assert FakeDeepSeekClient.request_url == (
        "http://127.0.0.1:8080/v1/chat/completions"
    )
    assert FakeDeepSeekClient.request_json["model"] == "local-model"
    assert "thinking" not in FakeDeepSeekClient.request_json


@pytest.mark.parametrize(
    "text",
    ["", "[BLANK_AUDIO]", "MBC 뉴스 이덕영입니다.", "(字幕製作:貝爾)"],
)
def test_local_whisper_rejects_silence_and_out_of_scope_hallucinations(text: str):
    with pytest.raises(NoSpeechDetected):
        LocalDeepSeekVoiceProvider._clean_transcript(text)


def test_persistent_whisper_server_reuses_loaded_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    model_path = tmp_path / "ggml-small.bin"
    model_path.write_bytes(b"model")
    monkeypatch.setattr("stackchan_control.voice.shutil.which", lambda _: "/usr/bin/tool")
    monkeypatch.setattr(
        "stackchan_control.voice.httpx.AsyncClient", FakeWhisperServerClient
    )
    settings = Settings(
        db_path=tmp_path / "voice.db",
        seed_character_dir=PROJECT_ROOT / "config" / "seed_character",
        web_dir=PROJECT_ROOT / "web",
        deepseek_api_key="secret",
        voice_whisper_model=model_path,
    )

    transcript = asyncio.run(
        LocalDeepSeekVoiceProvider(settings).transcribe(b"RIFF-fake-wav")
    )

    assert transcript == "爱莉你好"
    assert FakeWhisperServerClient.request_data == {
        "response_format": "json",
        "language": "auto",
        "temperature": "0.0",
    }


def test_local_whisper_accepts_chinese_and_english():
    assert LocalDeepSeekVoiceProvider._clean_transcript("  你好， StackChan  ") == "你好， StackChan"
    assert LocalDeepSeekVoiceProvider._clean_transcript("HelloHello") == "Hello"


@pytest.mark.parametrize("text", ["( 開箱 )", "（环境音乐）", "[music]"])
def test_local_whisper_rejects_parenthesized_sound_descriptions(text: str):
    with pytest.raises(NoSpeechDetected):
        LocalDeepSeekVoiceProvider._clean_transcript(text)


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


def test_streaming_answer_coalesces_short_sentences_for_continuous_tts():
    segments, tail = LocalDeepSeekVoiceProvider._pop_spoken_segments(
        "我们先画一只猫。然后给它加上帽子！还有一点"
    )

    assert segments == []
    assert tail == "我们先画一只猫。然后给它加上帽子！还有一点"


def test_streaming_answer_coalesces_comma_clauses_to_avoid_tts_gaps():
    segments, tail = LocalDeepSeekVoiceProvider._pop_spoken_segments(
        "让我先看看你刚刚提到的那个问题，然后我们一起决定。"
    )

    assert segments == []
    assert tail == "让我先看看你刚刚提到的那个问题，然后我们一起决定。"


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
    pcm = asyncio.run(provider._synthesize_neural("你好，我是爱莉。"))

    assert pcm == b"pcm"
    assert FakeNeuralTtsClient.request_json is not None
    assert FakeNeuralTtsClient.request_json["voice"] == "Vivian"
    assert FakeNeuralTtsClient.request_json["speed"] == 1.10
    assert FakeNeuralTtsClient.request_json["lang_code"] == "Chinese"
    assert FakeNeuralTtsClient.request_json["max_tokens"] == 48


def test_gpt_sovits_uses_trained_voice_reference(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    model_path = tmp_path / "ggml-small.bin"
    model_path.write_bytes(b"model")
    reference_path = tmp_path / "reference.wav"
    reference_path.write_bytes(b"RIFF-reference")
    monkeypatch.setattr("stackchan_control.voice.shutil.which", lambda _: "/usr/bin/tool")
    monkeypatch.setattr("stackchan_control.voice.httpx.AsyncClient", FakeGptSovitsClient)

    async def fake_convert(_provider, audio: bytes) -> bytes:
        assert audio == b"RIFF-gpt-sovits-wav"
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
        voice_gpt_sovits_ref_audio=reference_path,
    )

    provider = LocalDeepSeekVoiceProvider(settings)
    pcm = asyncio.run(provider._synthesize_gpt_sovits("你好，我是爱莉。"))

    assert pcm == b"pcm"
    assert FakeGptSovitsClient.request_url == "http://127.0.0.1:9880/tts"
    assert FakeGptSovitsClient.request_json is not None
    assert FakeGptSovitsClient.request_json["text_lang"] == "zh"
    assert FakeGptSovitsClient.request_json["prompt_text"] == (
        "所以你今天就来见我了吗？哇，真令人开心呢。"
    )
    assert FakeGptSovitsClient.request_json["speed_factor"] == 1.10
