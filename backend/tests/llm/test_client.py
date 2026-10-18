"""call_llm: request shape, parsing and failure modes, with litellm faked out."""

import asyncio
import sys
import types
from types import SimpleNamespace

import pytest

from app.llm import client
from app.llm.client import EXTRA_BODY, MODEL, LLMNotConfiguredError, call_llm
from app.llm.models import ResponseParseError


def _response(content):
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])


@pytest.fixture
def fake_litellm(monkeypatch):
    """Install a fake `litellm` module; returns the list of captured call kwargs."""
    calls = []
    module = types.ModuleType("litellm")
    module.next_content = '{"message": "ok", "trades": [], "watchlist_changes": []}'

    async def acompletion(**kwargs):
        calls.append(kwargs)
        return _response(module.next_content)

    module.acompletion = acompletion
    monkeypatch.setitem(sys.modules, "litellm", module)
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    return module, calls


async def test_missing_key_raises_without_calling(monkeypatch, fake_litellm):
    _, calls = fake_litellm
    monkeypatch.setenv("OPENROUTER_API_KEY", "  ")
    with pytest.raises(LLMNotConfiguredError):
        await call_llm([{"role": "user", "content": "hi"}])
    assert calls == []


async def test_request_uses_cerebras_structured_output(fake_litellm):
    _, calls = fake_litellm
    result = await call_llm([{"role": "user", "content": "hi"}])
    assert result.message == "ok"
    assert len(calls) == 1
    kwargs = calls[0]
    assert kwargs["model"] == MODEL == "openrouter/openai/gpt-oss-120b"
    assert kwargs["extra_body"] == EXTRA_BODY == {"provider": {"order": ["cerebras"]}}
    assert kwargs["response_format"]["type"] == "json_schema"
    assert kwargs["reasoning_effort"] == "low"
    assert kwargs["api_key"] == "test-key"
    assert kwargs["timeout"] == client.DEFAULT_TIMEOUT
    assert kwargs["max_tokens"] == client.MAX_OUTPUT_TOKENS


async def test_timeout_env_override(monkeypatch, fake_litellm):
    _, calls = fake_litellm
    monkeypatch.setenv("LLM_TIMEOUT", "7")
    await call_llm([])
    assert calls[0]["timeout"] == 7.0
    monkeypatch.setenv("LLM_TIMEOUT", "junk")
    await call_llm([])
    assert calls[1]["timeout"] == client.DEFAULT_TIMEOUT


async def test_invalid_json_raises_parse_error(fake_litellm):
    module, _ = fake_litellm
    module.next_content = "Sure! I'll buy that for you."
    with pytest.raises(ResponseParseError):
        await call_llm([])


async def test_none_content_raises_parse_error(fake_litellm):
    module, _ = fake_litellm
    module.next_content = None
    with pytest.raises(ResponseParseError):
        await call_llm([])


async def test_hung_call_times_out(monkeypatch, fake_litellm):
    module, _ = fake_litellm

    async def hang(**kwargs):
        await asyncio.sleep(60)

    module.acompletion = hang
    monkeypatch.setattr(client, "_timeout", lambda: -4.95)  # wait_for gets 0.05s
    with pytest.raises(asyncio.TimeoutError):
        await call_llm([])


@pytest.mark.parametrize(
    "value,expected",
    [("true", True), ("TRUE", True), ("1", True), ("false", False), ("", False)],
)
def test_llm_mock_enabled(monkeypatch, value, expected):
    monkeypatch.setenv("LLM_MOCK", value)
    assert client.llm_mock_enabled() is expected
