"""Chat orchestration with faked db/portfolio services and a faked LLM call."""

import asyncio

import pytest

from app.llm import service
from app.llm.models import ChatResponse, ResponseParseError, TradeRequest, WatchlistChange

# Stands in for litellm.exceptions.Timeout, which the service matches by class name.
Timeout = type("Timeout", (Exception,), {})


@pytest.fixture
def llm_calls(monkeypatch, live_mode):
    """Patch the single LLM call; set `.reply` to a ChatResponse or an exception."""

    class Recorder:
        reply: ChatResponse | Exception = ChatResponse(message="Hello there.")
        calls: list = []

    recorder = Recorder()
    recorder.calls = []

    async def fake_call_llm(messages):
        recorder.calls.append(messages)
        if isinstance(recorder.reply, Exception):
            raise recorder.reply
        return recorder.reply

    monkeypatch.setattr(service, "call_llm", fake_call_llm)
    return recorder


# --- mock mode -------------------------------------------------------------


async def test_mock_greeting_stores_both_messages(mock_mode, fake_db, fake_portfolio):
    result = await service.handle_chat_message("hello")
    assert result["role"] == "assistant"
    assert result["content"] == "Mock: Hello! I'm FinAlly, your AI trading assistant."
    assert result["actions"] == {"trades": [], "watchlist_changes": []}
    assert set(result) == {"id", "role", "content", "actions", "created_at"}
    assert [(m["role"], m["content"]) for m in fake_db.messages] == [
        ("user", "hello"),
        ("assistant", result["content"]),
    ]
    assert fake_db.messages[0]["actions"] is None


async def test_mock_buy_executes_trade(mock_mode, fake_db, fake_portfolio):
    result = await service.handle_chat_message("buy some apple")
    assert result["actions"]["trades"] == [
        {"ticker": "AAPL", "side": "buy", "quantity": 1, "status": "executed", "price": 190.0}
    ]
    assert fake_portfolio.held == {"AAPL": 1}
    assert fake_db.messages[-1]["actions"] == result["actions"]


async def test_mock_sell_without_shares_fails(mock_mode, fake_db, fake_portfolio):
    result = await service.handle_chat_message("sell")
    assert result["content"] == "Mock: selling 1 share of AAPL."
    trade = result["actions"]["trades"][0]
    assert trade["status"] == "failed"
    assert trade["error"] == "Insufficient shares"
    assert "price" not in trade


async def test_mock_add_and_remove_watchlist(mock_mode, fake_db, fake_portfolio):
    added = await service.handle_chat_message("add pypl")
    assert added["actions"]["watchlist_changes"] == [
        {"ticker": "PYPL", "action": "add", "status": "executed"}
    ]
    assert "PYPL" in fake_portfolio.watchlist
    removed = await service.handle_chat_message("remove pypl")
    assert removed["actions"]["watchlist_changes"][0]["status"] == "executed"
    assert "PYPL" not in fake_portfolio.watchlist
    missing = await service.handle_chat_message("remove zzz")
    change = missing["actions"]["watchlist_changes"][0]
    assert change == {
        "ticker": "ZZZ",
        "action": "remove",
        "status": "failed",
        "error": "ZZZ is not on the watchlist",
    }


async def test_mock_mode_never_calls_llm(mock_mode, fake_db, fake_portfolio, monkeypatch):
    async def boom(messages):
        raise AssertionError("LLM must not be called in mock mode")

    monkeypatch.setattr(service, "call_llm", boom)
    await service.handle_chat_message("hi")


# --- live path (LLM faked) ------------------------------------------------


async def test_exactly_one_llm_call_with_context_and_history(llm_calls, fake_db, fake_portfolio):
    fake_db.insert_chat_message("user", "buy 100 tsla", None)
    fake_db.insert_chat_message(
        "assistant",
        "Buying.",
        {
            "trades": [
                {
                    "ticker": "TSLA",
                    "side": "buy",
                    "quantity": 100,
                    "status": "failed",
                    "error": "Insufficient cash",
                }
            ],
            "watchlist_changes": [],
        },
    )
    await service.handle_chat_message("what happened?")
    assert len(llm_calls.calls) == 1
    messages = llm_calls.calls[0]
    assert messages[0]["role"] == "system" and "FinAlly" in messages[0]["content"]
    assert "Cash: $10,000.00" in messages[1]["content"]
    assert "TSLA: price $250.00" in messages[1]["content"]
    assert messages[2] == {"role": "user", "content": "buy 100 tsla"}
    assert "Insufficient cash" in messages[3]["content"]
    assert messages[-1] == {"role": "user", "content": "what happened?"}
    # The new user message is not duplicated in the history part.
    assert sum(m["content"] == "what happened?" for m in messages) == 1


async def test_history_limited_to_20(llm_calls, fake_db, fake_portfolio):
    for i in range(30):
        fake_db.insert_chat_message("user", f"m{i}", None)
    await service.handle_chat_message("latest")
    messages = llm_calls.calls[0]
    history = messages[2:-1]
    assert len(history) == 20
    assert history[0]["content"] == "m10"


async def test_mixed_success_and_failure(llm_calls, fake_db, fake_portfolio):
    llm_calls.reply = ChatResponse(
        message="Done.",
        trades=[
            TradeRequest(ticker="AAPL", side="buy", quantity=10),
            TradeRequest(ticker="TSLA", side="buy", quantity=1000),  # insufficient cash
            TradeRequest(ticker="PYPL", side="buy", quantity=1),  # no price
            TradeRequest(ticker="AAPL", side="buy", quantity=0),
        ],
        watchlist_changes=[
            WatchlistChange(ticker="NVDA", action="add"),
            WatchlistChange(ticker="BAD1", action="add"),
        ],
    )
    result = await service.handle_chat_message("do it")
    trades = result["actions"]["trades"]
    assert [t["status"] for t in trades] == ["executed", "failed", "failed", "failed"]
    assert trades[0]["price"] == 190.0
    assert trades[1]["error"] == "Insufficient cash"
    assert trades[2]["error"] == "price not yet available, retry"
    assert trades[3]["error"] == "Quantity must be greater than 0"
    changes = result["actions"]["watchlist_changes"]
    assert [c["status"] for c in changes] == ["executed", "failed"]
    assert changes[1]["error"].startswith("Invalid ticker")
    # Trades run first, in order, then watchlist changes.
    assert [c[0] for c in fake_portfolio.calls] == ["trade"] * 4 + ["add", "add"]
    assert result["content"] == "Done."


async def test_unexpected_exception_in_action_is_contained(llm_calls, fake_db, fake_portfolio):
    async def explode(ticker, side, quantity):
        raise RuntimeError("db is on fire")

    fake_portfolio.execute_trade = explode
    llm_calls.reply = ChatResponse(
        message="ok", trades=[TradeRequest(ticker="AAPL", side="buy", quantity=1)]
    )
    result = await service.handle_chat_message("buy")
    trade = result["actions"]["trades"][0]
    assert trade["status"] == "failed"
    assert "db is on fire" not in trade["error"]


@pytest.mark.parametrize(
    "exc,expected",
    [
        (asyncio.TimeoutError(), service.TIMEOUT_MESSAGE),
        (Timeout("litellm timed out"), service.TIMEOUT_MESSAGE),
        (ResponseParseError("invalid JSON"), service.PARSE_ERROR_MESSAGE),
        (RuntimeError("502 from OpenRouter"), service.GENERIC_ERROR_MESSAGE),
    ],
)
async def test_llm_failures_become_friendly_stored_reply(
    llm_calls, fake_db, fake_portfolio, exc, expected
):
    llm_calls.reply = exc
    result = await service.handle_chat_message("hi")
    assert result["content"] == expected
    assert result["actions"] == {"trades": [], "watchlist_changes": []}
    assert fake_db.messages[-1]["content"] == expected
    assert fake_portfolio.calls == []


async def test_missing_api_key_path(monkeypatch, fake_db, fake_portfolio):
    monkeypatch.setenv("LLM_MOCK", "false")
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    result = await service.handle_chat_message("hi")
    assert result["content"] == service.NOT_CONFIGURED_MESSAGE
    assert result["actions"] == {"trades": [], "watchlist_changes": []}
    assert len(fake_db.messages) == 2


async def test_context_failures_do_not_break_chat(llm_calls, fake_db, fake_portfolio):
    def broken():
        raise RuntimeError("no cache yet")

    fake_portfolio.get_portfolio = broken
    fake_portfolio.get_watchlist = broken
    result = await service.handle_chat_message("hi")
    assert result["content"] == "Hello there."
    assert "Portfolio: unavailable" in llm_calls.calls[0][1]["content"]


def test_get_history_formats_rows(fake_db):
    fake_db.insert_chat_message("user", "hi", None)
    fake_db.insert_chat_message("assistant", "yo", {"trades": [], "watchlist_changes": []})
    history = service.get_history()
    assert [m["role"] for m in history] == ["user", "assistant"]
    assert set(history[0]) == {"id", "role", "content", "actions", "created_at"}


def test_get_history_last_50(fake_db):
    for i in range(60):
        fake_db.insert_chat_message("user", str(i), None)
    history = service.get_history()
    assert len(history) == 50 and history[0]["content"] == "10"
