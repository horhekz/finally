"""Chat orchestration: context -> one LLM call -> execute actions -> store (PLAN §9)."""

from __future__ import annotations

import asyncio
import logging
from types import ModuleType
from typing import Any

from .client import LLMNotConfiguredError, call_llm, llm_mock_enabled
from .mock import mock_response
from .models import ChatResponse, ResponseParseError
from .prompt import HISTORY_LIMIT, build_messages

logger = logging.getLogger(__name__)

NOT_CONFIGURED_MESSAGE = (
    "AI chat isn't configured yet: set OPENROUTER_API_KEY in .env and restart. "
    "Everything else (trading, watchlist, charts) works normally."
)
TIMEOUT_MESSAGE = "Sorry, the AI took too long to respond. Please try again."
PARSE_ERROR_MESSAGE = "Sorry, I couldn't understand the AI's response. Please try again."
GENERIC_ERROR_MESSAGE = "Sorry, I couldn't reach the AI service right now. Please try again."

HISTORY_API_LIMIT = 50


def _portfolio() -> ModuleType:
    from app.services import portfolio

    return portfolio


def _db() -> ModuleType:
    from app import db

    return db


def empty_actions() -> dict[str, list[dict[str, Any]]]:
    return {"trades": [], "watchlist_changes": []}


async def handle_chat_message(user_message: str) -> dict[str, Any]:
    """Process one user message and return the stored assistant row (contract §5)."""
    db = _db()
    history = _load_history(db)
    portfolio_ctx, watchlist_ctx = _load_context()

    db.insert_chat_message("user", user_message, None)

    reply = await get_reply(user_message, history, portfolio_ctx, watchlist_ctx)
    if isinstance(reply, str):
        content, actions = reply, empty_actions()
    else:
        content = reply.message
        actions = await execute_actions(reply)

    row = db.insert_chat_message("assistant", content, actions)
    return format_message(row)


def get_history() -> list[dict[str, Any]]:
    """Last 50 stored messages, ascending (GET /api/chat/history)."""
    return [format_message(r) for r in _db().list_chat_messages(limit=HISTORY_API_LIMIT)]


async def get_reply(
    user_message: str,
    history: list[dict[str, Any]],
    portfolio_ctx: dict[str, Any] | None,
    watchlist_ctx: list[dict[str, Any]] | None,
) -> ChatResponse | str:
    """One LLM call (or the mock). Returns the parsed reply, or a friendly failure text."""
    if llm_mock_enabled():
        return mock_response(user_message)
    messages = build_messages(user_message, history, portfolio_ctx, watchlist_ctx)
    try:
        return await call_llm(messages)
    except LLMNotConfiguredError:
        logger.warning("Chat requested but OPENROUTER_API_KEY is not set")
        return NOT_CONFIGURED_MESSAGE
    except (TimeoutError, asyncio.TimeoutError):
        logger.warning("LLM call timed out")
        return TIMEOUT_MESSAGE
    except ResponseParseError as exc:
        logger.warning("Unparseable LLM response: %s", exc)
        return PARSE_ERROR_MESSAGE
    except Exception as exc:
        # litellm.exceptions.Timeout and friends, without importing litellm here.
        if "timeout" in type(exc).__name__.lower():
            logger.warning("LLM call timed out: %s", exc)
            return TIMEOUT_MESSAGE
        logger.exception("LLM call failed")
        return GENERIC_ERROR_MESSAGE


async def execute_actions(reply: ChatResponse) -> dict[str, list[dict[str, Any]]]:
    """Run the requested trades, then watchlist changes, recording each outcome."""
    portfolio = _portfolio()
    actions = empty_actions()

    for trade in reply.trades:
        entry: dict[str, Any] = {
            "ticker": trade.ticker,
            "side": trade.side,
            "quantity": trade.quantity,
        }
        try:
            result = await portfolio.execute_trade(trade.ticker, trade.side, trade.quantity)
            entry["ticker"] = result.get("ticker", trade.ticker)
            entry["quantity"] = result.get("quantity", trade.quantity)
            entry["status"] = "executed"
            entry["price"] = result.get("price")
        except Exception as exc:
            entry["status"] = "failed"
            entry["error"] = _error_text(exc)
        actions["trades"].append(entry)

    for change in reply.watchlist_changes:
        entry = {"ticker": change.ticker, "action": change.action}
        try:
            if change.action == "add":
                item = await portfolio.add_to_watchlist(change.ticker)
                if isinstance(item, dict) and item.get("ticker"):
                    entry["ticker"] = item["ticker"]
            else:
                await portfolio.remove_from_watchlist(change.ticker)
            entry["status"] = "executed"
        except Exception as exc:
            entry["status"] = "failed"
            entry["error"] = _error_text(exc)
        actions["watchlist_changes"].append(entry)

    return actions


def format_message(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row.get("id"),
        "role": row.get("role"),
        "content": row.get("content"),
        "actions": row.get("actions"),
        "created_at": row.get("created_at"),
    }


def _error_text(exc: Exception) -> str:
    """ApiError carries a user-facing ``message``; anything else is unexpected."""
    message = getattr(exc, "message", None)
    if isinstance(message, str) and message:
        return message
    if isinstance(exc, ValueError) and str(exc):
        return str(exc)
    logger.exception("Unexpected error executing chat action")
    return "Unexpected error executing this action"


def _load_history(db: ModuleType) -> list[dict[str, Any]]:
    try:
        return db.list_chat_messages(limit=HISTORY_LIMIT)
    except Exception:
        logger.exception("Failed to load chat history")
        return []


def _load_context() -> tuple[dict[str, Any] | None, list[dict[str, Any]] | None]:
    portfolio = _portfolio()
    try:
        portfolio_ctx = portfolio.get_portfolio()
    except Exception:
        logger.exception("Failed to load portfolio for chat context")
        portfolio_ctx = None
    try:
        watchlist_ctx = portfolio.get_watchlist()
    except Exception:
        logger.exception("Failed to load watchlist for chat context")
        watchlist_ctx = None
    return portfolio_ctx, watchlist_ctx
