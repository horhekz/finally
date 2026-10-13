"""LiteLLM -> OpenRouter -> Cerebras call with structured output."""

from __future__ import annotations

import asyncio
import os
from typing import Any

from .models import ChatResponse, parse_response, response_format

MODEL = "openrouter/openai/gpt-oss-120b"
EXTRA_BODY = {"provider": {"order": ["cerebras"]}}
DEFAULT_TIMEOUT = 30.0
# Far more than a reply needs. Without it OpenRouter reserves the provider default (40,960 tokens)
# per request, and when the key's remaining credit can't cover that it quietly routes away from
# Cerebras to providers whose structured outputs come back garbled.
MAX_OUTPUT_TOKENS = 4000


class LLMNotConfiguredError(RuntimeError):
    """OPENROUTER_API_KEY is missing."""


def llm_mock_enabled() -> bool:
    return os.environ.get("LLM_MOCK", "").strip().lower() in ("1", "true", "yes", "on")


def _timeout() -> float:
    try:
        return max(1.0, float(os.environ.get("LLM_TIMEOUT", DEFAULT_TIMEOUT)))
    except ValueError:
        return DEFAULT_TIMEOUT


async def call_llm(messages: list[dict[str, Any]]) -> ChatResponse:
    """Make exactly one LLM call and parse the structured response.

    Raises LLMNotConfiguredError, asyncio.TimeoutError, ResponseParseError, or whatever
    LiteLLM raises; the caller turns all of these into a friendly reply.
    """
    api_key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    if not api_key:
        raise LLMNotConfiguredError("OPENROUTER_API_KEY is not set")

    # Imported lazily: litellm is slow to import and unused in mock mode.
    from litellm import acompletion

    timeout = _timeout()
    response = await asyncio.wait_for(
        acompletion(
            model=MODEL,
            messages=messages,
            response_format=response_format(),
            reasoning_effort="low",
            extra_body=EXTRA_BODY,
            max_tokens=MAX_OUTPUT_TOKENS,
            api_key=api_key,
            timeout=timeout,
            num_retries=0,
        ),
        timeout=timeout + 5,
    )
    return parse_response(response.choices[0].message.content)
