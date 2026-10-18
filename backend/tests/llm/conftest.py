"""Fakes for app.db and app.services.portfolio so chat tests never touch real services."""

from __future__ import annotations

import itertools
from typing import Any

import pytest

from app.llm import service


class FakeApiError(Exception):
    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.message = message


class FakeDb:
    def __init__(self) -> None:
        self.messages: list[dict[str, Any]] = []
        self._ids = itertools.count(1)

    def insert_chat_message(self, role: str, content: str, actions: dict | None) -> dict:
        n = next(self._ids)
        row = {
            "id": f"msg-{n}",
            "role": role,
            "content": content,
            "actions": actions,
            "created_at": f"2026-10-13T00:00:{n:02d}+00:00",
        }
        self.messages.append(row)
        return row

    def list_chat_messages(self, limit: int = 50) -> list[dict]:
        return list(self.messages[-limit:])


class FakePortfolio:
    def __init__(self) -> None:
        self.cash = 10000.0
        self.prices = {"AAPL": 190.0, "TSLA": 250.0}
        self.held: dict[str, float] = {}
        self.watchlist = ["AAPL", "TSLA"]
        self.calls: list[tuple] = []

    async def execute_trade(self, ticker: str, side: str, quantity: float) -> dict:
        self.calls.append(("trade", ticker, side, quantity))
        if quantity <= 0:
            raise FakeApiError(400, "Quantity must be greater than 0")
        price = self.prices.get(ticker)
        if price is None:
            raise FakeApiError(409, "price not yet available, retry")
        if side == "buy":
            if price * quantity > self.cash:
                raise FakeApiError(400, "Insufficient cash")
            self.cash -= price * quantity
            self.held[ticker] = self.held.get(ticker, 0) + quantity
        else:
            if self.held.get(ticker, 0) < quantity:
                raise FakeApiError(400, "Insufficient shares")
            self.cash += price * quantity
            self.held[ticker] -= quantity
        return {
            "id": "t1",
            "ticker": ticker,
            "side": side,
            "quantity": quantity,
            "price": price,
            "executed_at": "2026-10-13T00:00:00+00:00",
        }

    async def add_to_watchlist(self, ticker: str) -> dict:
        self.calls.append(("add", ticker))
        if not ticker.isalpha():
            raise FakeApiError(400, f"Invalid ticker {ticker!r}")
        if ticker not in self.watchlist:
            self.watchlist.append(ticker)
        return {"ticker": ticker, "added_at": "2026-10-13T00:00:00+00:00", "price": None}

    async def remove_from_watchlist(self, ticker: str) -> None:
        self.calls.append(("remove", ticker))
        if ticker not in self.watchlist:
            raise FakeApiError(404, f"{ticker} is not on the watchlist")
        self.watchlist.remove(ticker)

    def get_portfolio(self) -> dict:
        return {
            "cash_balance": self.cash,
            "positions_value": 0.0,
            "total_value": self.cash,
            "unrealized_pnl": 0.0,
            "unrealized_pnl_percent": 0.0,
            "positions": [],
        }

    def get_watchlist(self) -> list[dict]:
        return [
            {"ticker": t, "price": self.prices.get(t), "day_change_percent": 0.5}
            for t in self.watchlist
        ]


@pytest.fixture
def fake_db(monkeypatch) -> FakeDb:
    db = FakeDb()
    monkeypatch.setattr(service, "_db", lambda: db)
    return db


@pytest.fixture
def fake_portfolio(monkeypatch) -> FakePortfolio:
    portfolio = FakePortfolio()
    monkeypatch.setattr(service, "_portfolio", lambda: portfolio)
    return portfolio


@pytest.fixture
def mock_mode(monkeypatch) -> None:
    monkeypatch.setenv("LLM_MOCK", "true")


@pytest.fixture
def live_mode(monkeypatch) -> None:
    monkeypatch.setenv("LLM_MOCK", "false")
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
