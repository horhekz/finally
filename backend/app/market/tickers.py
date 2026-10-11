"""Ticker symbol validation and normalisation."""

from __future__ import annotations

import re

# AAPL, BRK.B, BF-B. Starts with a letter, at most 10 chars.
TICKER_RE = re.compile(r"[A-Z][A-Z0-9.\-]{0,9}")

DEFAULT_TICKERS: tuple[str, ...] = (
    "AAPL", "GOOGL", "MSFT", "AMZN", "TSLA", "NVDA", "META", "JPM", "V", "NFLX",
)


class InvalidTickerError(ValueError):
    """Raised for a malformed ticker symbol. API routes map this to HTTP 400."""


def normalize_ticker(raw: str) -> str:
    """Strip + uppercase, then validate. ' aapl ' -> 'AAPL'."""
    ticker = (raw or "").strip().upper()
    if not TICKER_RE.fullmatch(ticker):
        raise InvalidTickerError(f"Invalid ticker symbol: {raw!r}")
    return ticker
