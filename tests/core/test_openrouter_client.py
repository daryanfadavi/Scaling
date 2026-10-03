"""OpenRouterClient behaviour, against a mock HTTP transport (no network, no key)."""

import json

import httpx2
import pytest

import controller.llm_client as client_mod
import controller.openrouter_client as openrouter_mod
from controller.llm_client import LLMClient
from controller.openrouter_client import OPENROUTER_URL, OpenRouterClient, parse_upstream

MODEL = "some-vendor/some-model"
ENV_VARS = (
    "ANTHROPIC_API_KEY",
    "SCALE_MODEL",
    "OPENROUTER_API_KEY",
    "OPENROUTER_MODEL",
    "OPENROUTER_UPSTREAM",
)


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    """Never see a real key or .env, and never really sleep between retries."""
    monkeypatch.setattr(client_mod, "load_dotenv", lambda *a, **k: None)
    for name in ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(openrouter_mod.time, "sleep", lambda seconds: None)


def ok_reply(text='{"action": "NO_CHANGE"}'):
    return httpx2.Response(
        200,
        json={
            "provider": "Fireworks",
            "choices": [{"message": {"content": text}, "finish_reason": "stop"}],
        },
    )


def error_reply(status, message="something went wrong"):
    return httpx2.Response(status, json={"error": {"code": status, "message": message}})


def make_client(monkeypatch, replies, **kwargs):
    """A client with a key whose HTTP layer answers with `replies`, in order.

    Returns (client, requests): `requests` collects every request that was sent.
    """
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
    kwargs.setdefault("model", MODEL)
    client = OpenRouterClient(**kwargs)
    requests = []
    remaining = list(replies)

    def handler(request):
        requests.append(request)
        return remaining.pop(0)

    client._http = httpx2.Client(transport=httpx2.MockTransport(handler))
    return client, requests


# ------------------------------------------------------------ configuration


def test_no_key_fails_gracefully(tmp_path):
    client = OpenRouterClient(model=MODEL, cache_dir=tmp_path)
    assert client.complete("sys", "hello") is None
    assert client.call_log[-1]["ok"] is False
    assert "OPENROUTER_API_KEY" in client.call_log[-1]["error"]


def test_no_model_fails_gracefully(monkeypatch):
    client, requests = make_client(monkeypatch, [], model=None)
    assert client.model is None
    assert client.complete("sys", "hello") is None
    assert "OPENROUTER_MODEL" in client.call_log[-1]["error"]
    assert requests == []


def test_model_comes_from_openrouter_env_not_scale_model(monkeypatch):
    monkeypatch.setenv("SCALE_MODEL", "claude-something")
    assert OpenRouterClient().model is None
    monkeypatch.setenv("OPENROUTER_MODEL", MODEL)
    assert OpenRouterClient().model == MODEL
    assert OpenRouterClient(model="other/model").model == "other/model"


def test_upstream_from_argument_or_env(monkeypatch):
    assert OpenRouterClient(model=MODEL).upstream is None
    monkeypatch.setenv("OPENROUTER_UPSTREAM", "Fireworks, Together")
    assert OpenRouterClient(model=MODEL).upstream == ["Fireworks", "Together"]
    assert OpenRouterClient(model=MODEL, upstream=["Groq"]).upstream == ["Groq"]


def test_parse_upstream():
    assert parse_upstream("Fireworks") == ["Fireworks"]
    assert parse_upstream(" A , B ,") == ["A", "B"]
    assert parse_upstream("") is None
    assert parse_upstream(None) is None


# ------------------------------------------------------------ the request


def test_request_shape(monkeypatch):
    client, requests = make_client(monkeypatch, [ok_reply()], upstream=["Fireworks"])
    response = client.complete("sys", "hello", use_cache=False)
    assert response.text == '{"action": "NO_CHANGE"}'
    assert response.cached is False and response.model == MODEL

    request = requests[0]
    assert str(request.url) == OPENROUTER_URL
    assert request.headers["Authorization"] == "Bearer sk-or-test"
    sent = json.loads(request.content)
    assert sent["model"] == MODEL
    assert sent["messages"] == [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "hello"},
    ]
    assert sent["temperature"] == 0.0
    assert sent["max_tokens"] == client.max_tokens
    assert sent["provider"] == {"order": ["Fireworks"], "allow_fallbacks": False}


def test_optional_fields_are_left_out(monkeypatch):
    client, requests = make_client(monkeypatch, [ok_reply()], temperature=None)
    client.complete("sys", "hello", use_cache=False)
    sent = json.loads(requests[0].content)
    assert "temperature" not in sent
    assert "provider" not in sent  # unpinned


# ------------------------------------------------------------ cache


def test_success_is_cached_and_replayed(monkeypatch, tmp_path):
    client, requests = make_client(monkeypatch, [ok_reply()], cache_dir=tmp_path)
    first = client.complete("sys", "hello")
    second = client.complete("sys", "hello")
    assert second.cached is True and second.text == first.text
    assert second.latency_s == first.latency_s
    assert len(requests) == 1  # served from the cache

    entry = json.loads(next(tmp_path.glob("*.json")).read_text())
    assert entry["provider"] == "openrouter" and entry["model"] == MODEL


def test_offline_cache_miss_makes_no_request(monkeypatch, tmp_path):
    client, requests = make_client(monkeypatch, [], cache_dir=tmp_path, offline=True)
    assert client.complete("sys", "hello") is None
    assert "offline" in client.call_log[-1]["error"]
    assert requests == []


def test_cache_key_is_separate_from_anthropic_and_per_upstream():
    plain = OpenRouterClient(model=MODEL)
    assert plain.cache_key("s", "p") != LLMClient(model=MODEL).cache_key("s", "p")
    assert plain.cache_key("s", "p") != OpenRouterClient(
        model=MODEL, upstream=["Fireworks"]
    ).cache_key("s", "p")
    assert plain.cache_key("s", "p") != OpenRouterClient(
        model=MODEL, repeat_index=1
    ).cache_key("s", "p")
    assert plain.cache_key("s", "p") == OpenRouterClient(model=MODEL).cache_key("s", "p")


# ------------------------------------------------------------ failures and retries


def test_auth_failure_is_not_retried(monkeypatch):
    client, requests = make_client(monkeypatch, [error_reply(401)])
    assert client.complete("sys", "hello") is None
    assert "authentication failed" in client.call_log[-1]["error"]
    assert len(requests) == 1


def test_unknown_model_gives_a_hint(monkeypatch):
    client, requests = make_client(
        monkeypatch, [error_reply(400, f"{MODEL} is not a valid model ID")]
    )
    assert client.complete("sys", "hello") is None
    assert "OPENROUTER_MODEL" in client.call_log[-1]["error"]
    assert len(requests) == 1


def test_rate_limit_is_retried(monkeypatch):
    client, requests = make_client(monkeypatch, [error_reply(429), ok_reply("ok")])
    assert client.complete("sys", "hello").text == "ok"
    assert len(requests) == 2


def test_server_error_gives_up_after_max_retries(monkeypatch):
    client, requests = make_client(monkeypatch, [error_reply(500)] * 3)
    assert client.complete("sys", "hello") is None
    assert "API error 500" in client.call_log[-1]["error"]
    assert len(requests) == client.max_retries + 1


def test_connection_problem_is_retried(monkeypatch):
    client, _ = make_client(monkeypatch, [])
    attempts = []

    def handler(request):
        attempts.append(request)
        if len(attempts) == 1:
            raise httpx2.ConnectError("boom")
        return ok_reply("ok")

    client._http = httpx2.Client(transport=httpx2.MockTransport(handler))
    assert client.complete("sys", "hello").text == "ok"
    assert len(attempts) == 2


def test_error_inside_a_200_reply_is_a_failure(monkeypatch):
    reply = httpx2.Response(200, json={"error": {"code": 400, "message": "nope"}})
    client, _ = make_client(monkeypatch, [reply])
    assert client.complete("sys", "hello") is None
    assert "nope" in client.call_log[-1]["error"]


@pytest.mark.parametrize("content", [None, "", "   "])
def test_empty_reply_is_a_failure_and_not_cached(monkeypatch, tmp_path, content):
    client, _ = make_client(monkeypatch, [ok_reply(content)], cache_dir=tmp_path)
    assert client.complete("sys", "hello") is None
    assert "empty reply" in client.call_log[-1]["error"]
    assert not any(tmp_path.glob("*.json"))


def test_garbage_body_is_a_failure(monkeypatch):
    client, _ = make_client(monkeypatch, [httpx2.Response(200, text="<html>")])
    assert client.complete("sys", "hello") is None
