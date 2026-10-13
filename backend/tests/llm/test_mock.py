"""LLM_MOCK triggers (team contract §6)."""

import pytest

from app.llm.mock import mock_response

GREETING = "Mock: Hello! I'm FinAlly, your AI trading assistant."


def test_buy_trigger():
    r = mock_response("Please BUY something")
    assert r.message == "Mock: buying 1 share of AAPL."
    assert [(t.ticker, t.side, t.quantity) for t in r.trades] == [("AAPL", "buy", 1)]
    assert r.watchlist_changes == []


def test_sell_trigger():
    r = mock_response("sell it all")
    assert r.message == "Mock: selling 1 share of AAPL."
    assert [(t.ticker, t.side, t.quantity) for t in r.trades] == [("AAPL", "sell", 1)]


def test_buy_wins_over_sell_and_add():
    r = mock_response("add pypl then sell and buy")
    assert r.trades[0].side == "buy"


@pytest.mark.parametrize(
    "text,ticker",
    [("add pypl to my watchlist", "PYPL"), ("Please Add brk.b", "BRK.B"), ("add x", "X")],
)
def test_add_trigger(text, ticker):
    r = mock_response(text)
    assert r.message == f"Mock: adding {ticker} to your watchlist."
    assert [(c.ticker, c.action) for c in r.watchlist_changes] == [(ticker, "add")]
    assert r.trades == []


def test_remove_trigger():
    r = mock_response("remove nflx please")
    assert r.message == "Mock: removing NFLX from your watchlist."
    assert [(c.ticker, c.action) for c in r.watchlist_changes] == [("NFLX", "remove")]


def test_add_requires_word_boundary_and_letter():
    assert mock_response("padd abc").watchlist_changes == []
    assert mock_response("add 123").watchlist_changes == []


@pytest.mark.parametrize("text", ["hello", "how is my portfolio?", ""])
def test_default_greeting(text):
    r = mock_response(text)
    assert r.message == GREETING
    assert r.trades == [] and r.watchlist_changes == []
