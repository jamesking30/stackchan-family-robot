import asyncio
from pathlib import Path

import httpx
import pytest

from stackchan_control.settings import PROJECT_ROOT, Settings
from stackchan_control.voice import LocalDeepSeekVoiceProvider


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
            json={"choices": [{"message": {"content": "你好，我是小栈。"}}]},
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

    assert answer == "你好，我是小栈。"
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
