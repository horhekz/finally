"""Structured-output models for the FinAlly chat assistant (PLAN §9)."""

from __future__ import annotations

import copy
import json
import logging
import re
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError, field_validator

logger = logging.getLogger(__name__)


class TradeRequest(BaseModel):
    """A trade the LLM asks to execute. Validation of amounts happens at execution."""

    ticker: str
    side: Literal["buy", "sell"]
    quantity: float

    @field_validator("ticker", mode="before")
    @classmethod
    def _strip_ticker(cls, v: Any) -> Any:
        return v.strip().upper() if isinstance(v, str) else v

    @field_validator("side", mode="before")
    @classmethod
    def _lower_side(cls, v: Any) -> Any:
        return v.strip().lower() if isinstance(v, str) else v


class WatchlistChange(BaseModel):
    """A watchlist modification the LLM asks to make."""

    ticker: str
    action: Literal["add", "remove"]

    @field_validator("ticker", mode="before")
    @classmethod
    def _strip_ticker(cls, v: Any) -> Any:
        return v.strip().upper() if isinstance(v, str) else v

    @field_validator("action", mode="before")
    @classmethod
    def _lower_action(cls, v: Any) -> Any:
        return v.strip().lower() if isinstance(v, str) else v


class ChatResponse(BaseModel):
    """The JSON object the LLM must return."""

    message: str
    trades: list[TradeRequest] = Field(default_factory=list)
    watchlist_changes: list[WatchlistChange] = Field(default_factory=list)


class ResponseParseError(ValueError):
    """The LLM output could not be turned into a ChatResponse."""


_FENCE_RE = re.compile(r"^\s*```(?:json)?\s*(.*?)\s*```\s*$", re.DOTALL)


def parse_response(raw: str | None) -> ChatResponse:
    """Parse the LLM's raw content into a ChatResponse.

    Tolerant of code fences, missing optional lists and individual malformed action
    items (those are dropped with a warning). Raises ResponseParseError if there is
    no usable JSON object with a non-empty ``message``.
    """
    if not raw or not raw.strip():
        raise ResponseParseError("empty response")
    text = raw.strip()
    fenced = _FENCE_RE.match(text)
    if fenced:
        text = fenced.group(1)
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ResponseParseError(f"invalid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ResponseParseError("response is not a JSON object")

    message = data.get("message")
    if not isinstance(message, str) or not message.strip():
        raise ResponseParseError("missing 'message'")

    trades = _parse_items(data.get("trades"), TradeRequest)
    changes = _parse_items(data.get("watchlist_changes"), WatchlistChange)
    return ChatResponse(message=message.strip(), trades=trades, watchlist_changes=changes)


def _parse_items[M: BaseModel](items: Any, model: type[M]) -> list[M]:
    if items is None:
        return []
    if not isinstance(items, list):
        logger.warning("LLM returned non-list for %s: %r", model.__name__, items)
        return []
    parsed: list[M] = []
    for item in items:
        try:
            parsed.append(model.model_validate(item))
        except ValidationError:
            logger.warning("Dropping malformed %s from LLM: %r", model.__name__, item)
    return parsed


def response_format() -> dict[str, Any]:
    """JSON-schema response_format for strict structured outputs.

    Strict mode requires every property to be listed in ``required`` and
    ``additionalProperties: false`` on every object, so the Pydantic schema is
    tightened here; the parser above still tolerates missing lists.
    """
    schema = copy.deepcopy(ChatResponse.model_json_schema())

    def tighten(node: Any) -> None:
        if isinstance(node, dict):
            if node.get("type") == "object" and "properties" in node:
                node["required"] = list(node["properties"].keys())
                node["additionalProperties"] = False
                for prop in node["properties"].values():
                    prop.pop("default", None)
            for value in node.values():
                tighten(value)
        elif isinstance(node, list):
            for value in node:
                tighten(value)

    tighten(schema)
    return {
        "type": "json_schema",
        "json_schema": {"name": "finally_chat_response", "strict": True, "schema": schema},
    }
