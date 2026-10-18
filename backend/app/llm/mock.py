"""Deterministic responses for LLM_MOCK=true (team contract §6)."""

from __future__ import annotations

import re

from .models import ChatResponse, TradeRequest, WatchlistChange

_ADD_RE = re.compile(r"\badd ([a-z][a-z0-9.\-]*)")
_REMOVE_RE = re.compile(r"\bremove ([a-z][a-z0-9.\-]*)")


def mock_response(user_message: str) -> ChatResponse:
    """First matching trigger wins, checked against the lowercased message."""
    text = user_message.lower()
    if "buy" in text:
        return ChatResponse(
            message="Mock: buying 1 share of AAPL.",
            trades=[TradeRequest(ticker="AAPL", side="buy", quantity=1)],
        )
    if "sell" in text:
        return ChatResponse(
            message="Mock: selling 1 share of AAPL.",
            trades=[TradeRequest(ticker="AAPL", side="sell", quantity=1)],
        )
    if match := _ADD_RE.search(text):
        ticker = match.group(1).upper()
        return ChatResponse(
            message=f"Mock: adding {ticker} to your watchlist.",
            watchlist_changes=[WatchlistChange(ticker=ticker, action="add")],
        )
    if match := _REMOVE_RE.search(text):
        ticker = match.group(1).upper()
        return ChatResponse(
            message=f"Mock: removing {ticker} from your watchlist.",
            watchlist_changes=[WatchlistChange(ticker=ticker, action="remove")],
        )
    return ChatResponse(message="Mock: Hello! I'm FinAlly, your AI trading assistant.")
