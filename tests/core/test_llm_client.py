"""LLMClient behaviour that doesn't need a network or an API key."""

import json

import pytest

import controller.llm_client as client_mod
from controller.llm_client import LLMClient


@pytest.fixture(autouse=True)
def no_real_key(monkeypatch):
    """Make sure these tests never see a real key (env var or .env file)."""
    monkeypatch.setattr(client_mod, "load_dotenv", lambda *a, **k: None)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("SCALE_MODEL", raising=False)


def test_default_model_and_env_override(monkeypatch):
    assert LLMClient().model == client_mod.DEFAULT_MODEL
    monkeypatch.setenv("SCALE_MODEL", "some-other-model")
    assert LLMClient().model == "some-other-model"


def test_no_key_fails_gracefully(tmp_path):
    client = LLMClient(cache_dir=tmp_path)
    assert client.complete("sys", "hello") is None
    assert client.call_log[-1]["ok"] is False


def test_offline_cache_miss_returns_none(tmp_path):
    client = LLMClient(cache_dir=tmp_path, offline=True)
    assert client.complete("sys", "hello") is None
    assert "offline" in client.call_log[-1]["error"]


def test_cache_key_depends_on_repeat_index_and_prompt():
    a = LLMClient(repeat_index=0)
    b = LLMClient(repeat_index=1)
    assert a.cache_key("s", "p") != b.cache_key("s", "p")
    assert a.cache_key("s", "p") != a.cache_key("s", "q")
    assert a.cache_key("s", "p") == LLMClient(repeat_index=0).cache_key("s", "p")


def test_cache_key_format_is_stable():
    """Changing how the key is built would silently orphan everyone's cache."""
    client = LLMClient(model="claude-haiku-4-5-20251001", repeat_index=0)
    assert (
        client.cache_key("s", "p")
        == "c02d77bdf744f2fac25e207b3a4b63534db3e35b18eb49bfc79a4d0748a9946a"
    )


def test_offline_replays_cached_response_with_original_latency(tmp_path):
    client = LLMClient(cache_dir=tmp_path, offline=True)
    key = client.cache_key("sys", "hello")
    (tmp_path / f"{key}.json").write_text(
        json.dumps({"text": "cached!", "latency_s": 0.8})
    )
    response = client.complete("sys", "hello")
    assert response.text == "cached!"
    assert response.cached is True
    assert response.latency_s == 0.8


class _FakeMessages:
    def __init__(self):
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        block = type("Block", (), {"type": "text", "text": '{"action": "NO_CHANGE"}'})()
        return type("Resp", (), {"content": [block], "stop_reason": "end_turn"})()


class _FakeAnthropic:
    def __init__(self):
        self.messages = _FakeMessages()


def test_api_path_sends_temperature_and_caches(tmp_path):
    client = LLMClient(cache_dir=tmp_path)
    fake = _FakeAnthropic()
    client._client = fake  # skip the real SDK client

    first = client.complete("sys", "hello")
    assert first.text == '{"action": "NO_CHANGE"}' and first.cached is False
    sent = fake.messages.calls[0]
    assert sent["model"] == client.model
    assert sent["system"] == "sys"
    assert sent["extra_body"] == {"temperature": 0.0}
    assert "temperature" not in sent  # the 1.x SDK rejects it as a keyword

    second = client.complete("sys", "hello")
    assert second.cached is True and second.latency_s == first.latency_s
    assert len(fake.messages.calls) == 1  # served from the cache


def test_real_sdk_sends_temperature_on_the_wire():
    """Run the real SDK against a mock HTTP transport, to catch SDK signature changes."""
    import anthropic
    import httpx2

    seen = {}

    def handler(request):
        seen.update(json.loads(request.content))
        return httpx2.Response(
            200,
            json={
                "id": "msg_test",
                "type": "message",
                "role": "assistant",
                "model": seen["model"],
                "content": [{"type": "text", "text": "ok"}],
                "stop_reason": "end_turn",
                "stop_sequence": None,
                "usage": {"input_tokens": 1, "output_tokens": 1},
            },
        )

    client = LLMClient()
    client._client = anthropic.Anthropic(
        api_key="sk-test",
        max_retries=0,
        http_client=anthropic.DefaultHttpxClient(
            transport=httpx2.MockTransport(handler)
        ),
    )
    response = client.complete("sys", "hello", use_cache=False)
    assert response.text == "ok"
    assert seen["temperature"] == 0.0
    assert seen["model"] == client.model
    assert seen["system"] == "sys"
