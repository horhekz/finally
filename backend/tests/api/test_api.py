"""REST API tests against a real app (tmp DB, seeded simulator)."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import db
from app.market import DEFAULT_TICKERS
from app.services import portfolio

POSITION_KEYS = {
    "ticker",
    "quantity",
    "avg_cost",
    "current_price",
    "market_value",
    "unrealized_pnl",
    "unrealized_pnl_percent",
    "weight",
}
WATCH_KEYS = {
    "ticker",
    "added_at",
    "price",
    "previous_price",
    "reference_price",
    "day_change_percent",
    "direction",
}


def buy(client: TestClient, ticker: str = "AAPL", quantity: float = 1, side: str = "buy"):
    return client.post(
        "/api/portfolio/trade", json={"ticker": ticker, "quantity": quantity, "side": side}
    )


# --- Health & portfolio ------------------------------------------------------


def test_health(client: TestClient) -> None:
    resp = client.get("/api/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_fresh_portfolio(client: TestClient) -> None:
    body = client.get("/api/portfolio").json()
    assert body == {
        "cash_balance": 10000.0,
        "positions_value": 0.0,
        "total_value": 10000.0,
        "unrealized_pnl": 0.0,
        "unrealized_pnl_percent": 0.0,
        "positions": [],
    }


# --- Trades ------------------------------------------------------------------


def test_buy_updates_cash_and_positions(client: TestClient) -> None:
    resp = buy(client, "aapl", 2)
    assert resp.status_code == 200
    body = resp.json()
    trade = body["trade"]
    assert set(trade) == {"id", "ticker", "side", "quantity", "price", "executed_at"}
    assert trade["ticker"] == "AAPL" and trade["side"] == "buy" and trade["quantity"] == 2
    pf = body["portfolio"]
    assert pf["cash_balance"] == pytest.approx(10000 - 2 * trade["price"], abs=0.01)
    [pos] = pf["positions"]
    assert set(pos) == POSITION_KEYS
    assert pos["ticker"] == "AAPL" and pos["quantity"] == 2
    assert pos["avg_cost"] == pytest.approx(trade["price"])
    assert pos["current_price"] is not None
    assert pf["total_value"] == pytest.approx(pf["cash_balance"] + pf["positions_value"], abs=0.02)
    assert pos["weight"] == pytest.approx(pos["market_value"] / pf["total_value"], abs=1e-4)


def test_sell_all_removes_position(client: TestClient) -> None:
    buy(client, "MSFT", 3)
    resp = buy(client, "MSFT", 3, side="sell")
    assert resp.status_code == 200
    assert resp.json()["portfolio"]["positions"] == []


def test_fractional_quantity_rounded_to_4dp(client: TestClient) -> None:
    resp = buy(client, "AAPL", 0.123456)
    assert resp.status_code == 200
    assert resp.json()["trade"]["quantity"] == 0.1235


@pytest.mark.parametrize(
    "payload",
    [
        {"ticker": "AAPL", "quantity": 1, "side": "hold"},
        {"ticker": "AAPL", "quantity": 0, "side": "buy"},
        {"ticker": "AAPL", "quantity": -1, "side": "buy"},
        {"ticker": "AAPL", "quantity": 0.00001, "side": "buy"},
        {"ticker": "AAPL", "quantity": "lots", "side": "buy"},
        {"ticker": "AAPL", "side": "buy"},
        {"ticker": "123!", "quantity": 1, "side": "buy"},
        {"quantity": 1, "side": "buy"},
    ],
)
def test_trade_validation_400(client: TestClient, payload: dict) -> None:
    resp = client.post("/api/portfolio/trade", json=payload)
    assert resp.status_code == 400
    assert isinstance(resp.json()["error"], str)


def test_malformed_json_is_400(client: TestClient) -> None:
    resp = client.post(
        "/api/portfolio/trade", content="not json", headers={"content-type": "application/json"}
    )
    assert resp.status_code == 400
    assert "error" in resp.json()


def test_insufficient_cash_400(client: TestClient) -> None:
    resp = buy(client, "NVDA", 100000)
    assert resp.status_code == 400
    assert "Insufficient cash" in resp.json()["error"]
    assert client.get("/api/portfolio").json()["cash_balance"] == 10000.0


def test_insufficient_shares_400(client: TestClient) -> None:
    resp = buy(client, "AAPL", 1, side="sell")
    assert resp.status_code == 400
    assert "Insufficient shares" in resp.json()["error"]


def test_no_price_409(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    async def no_price(ticker: str, timeout: float = 3.0) -> None:
        return None

    monkeypatch.setattr(portfolio.get_market(), "ensure_price", no_price)
    resp = buy(client, "PYPL", 1)
    assert resp.status_code == 409
    assert resp.json() == {"error": "Price for PYPL not yet available, retry"}
    assert db.list_trades() == []


def test_trade_untracked_ticker_tracks_but_does_not_watch(client: TestClient) -> None:
    resp = buy(client, "PYPL", 1)
    assert resp.status_code == 200
    assert "PYPL" in portfolio.get_market().tracked()
    tickers = [t["ticker"] for t in client.get("/api/watchlist").json()["tickers"]]
    assert "PYPL" not in tickers
    buy(client, "PYPL", 1, side="sell")
    assert "PYPL" not in portfolio.get_market().tracked()


def test_trade_records_snapshot(client: TestClient) -> None:
    before = len(client.get("/api/portfolio/history").json()["snapshots"])
    buy(client, "AAPL", 1)
    after = client.get("/api/portfolio/history").json()["snapshots"]
    assert len(after) == before + 1


# --- History -----------------------------------------------------------------


def test_history_has_startup_snapshot(client: TestClient) -> None:
    snaps = client.get("/api/portfolio/history").json()["snapshots"]
    assert len(snaps) >= 1
    assert set(snaps[0]) == {"total_value", "recorded_at"}
    assert snaps[0]["total_value"] == 10000.0


# --- Watchlist ---------------------------------------------------------------


def test_default_watchlist_with_prices(client: TestClient) -> None:
    items = client.get("/api/watchlist").json()["tickers"]
    assert [i["ticker"] for i in items] == list(DEFAULT_TICKERS)
    for item in items:
        assert set(item) == WATCH_KEYS
        assert item["price"] is not None
        assert item["reference_price"] is not None


def test_watchlist_add_dup_remove(client: TestClient) -> None:
    resp = client.post("/api/watchlist", json={"ticker": " pypl "})
    assert resp.status_code == 201
    item = resp.json()
    assert set(item) == WATCH_KEYS
    assert item["ticker"] == "PYPL" and item["price"] is not None
    assert "PYPL" in portfolio.get_market().tracked()

    dup = client.post("/api/watchlist", json={"ticker": "PYPL"})
    assert dup.status_code == 200
    assert dup.json()["added_at"] == item["added_at"]

    assert client.delete("/api/watchlist/pypl").status_code == 204
    assert "PYPL" not in portfolio.get_market().tracked()
    missing = client.delete("/api/watchlist/PYPL")
    assert missing.status_code == 404
    assert missing.json() == {"error": "PYPL is not on the watchlist"}


@pytest.mark.parametrize("payload", [{"ticker": "bad ticker!"}, {"ticker": ""}, {}])
def test_watchlist_add_invalid_400(client: TestClient, payload: dict) -> None:
    resp = client.post("/api/watchlist", json=payload)
    assert resp.status_code == 400
    assert "error" in resp.json()


def test_watchlist_delete_invalid_ticker_400(client: TestClient) -> None:
    resp = client.delete("/api/watchlist/bad!!")
    assert resp.status_code == 400
    assert "error" in resp.json()


def test_held_ticker_stays_priced_after_watchlist_removal(client: TestClient) -> None:
    buy(client, "AAPL", 1)
    assert client.delete("/api/watchlist/AAPL").status_code == 204
    assert "AAPL" in portfolio.get_market().tracked()
    [pos] = client.get("/api/portfolio").json()["positions"]
    assert pos["current_price"] is not None
    tickers = [t["ticker"] for t in client.get("/api/watchlist").json()["tickers"]]
    assert "AAPL" not in tickers
    # Selling the last share untracks it, since it is no longer watched either.
    buy(client, "AAPL", 1, side="sell")
    assert "AAPL" not in portfolio.get_market().tracked()


# --- Persistence -------------------------------------------------------------


def test_positions_persist_and_stay_priced_across_restart(
    make_client: Callable[[], TestClient],
) -> None:
    first = make_client()
    buy(first, "PYPL", 2)
    first.__exit__(None, None, None)
    second = make_client()
    [pos] = second.get("/api/portfolio").json()["positions"]
    assert pos["ticker"] == "PYPL" and pos["quantity"] == 2
    assert pos["current_price"] is not None  # held tickers are tracked at startup


# --- Reset -------------------------------------------------------------------


def test_reset_disabled_404(client: TestClient) -> None:
    resp = client.post("/api/reset")
    assert resp.status_code == 404
    assert "error" in resp.json()


def test_reset_enabled(make_client: Callable[[], TestClient], monkeypatch) -> None:
    monkeypatch.setenv("ENABLE_RESET", "true")
    client = make_client()
    buy(client, "PYPL", 1)
    client.delete("/api/watchlist/TSLA")
    resp = client.post("/api/reset")
    assert resp.status_code == 200
    assert resp.json() == {"status": "reset"}
    pf = client.get("/api/portfolio").json()
    assert pf["cash_balance"] == 10000.0 and pf["positions"] == []
    tickers = [t["ticker"] for t in client.get("/api/watchlist").json()["tickers"]]
    assert tickers == list(DEFAULT_TICKERS)
    assert portfolio.get_market().tracked() == set(DEFAULT_TICKERS)
    snaps = client.get("/api/portfolio/history").json()["snapshots"]
    assert len(snaps) == 1 and snaps[0]["total_value"] == 10000.0


# --- Static mount & routing --------------------------------------------------


def test_static_mount_after_api_routes(
    make_client: Callable[[], TestClient], tmp_path: Path, monkeypatch
) -> None:
    static = tmp_path / "static"
    (static / "_next").mkdir(parents=True)
    (static / "index.html").write_text("<html>FinAlly</html>", encoding="utf-8")
    (static / "_next" / "app.js").write_text("console.log(1)", encoding="utf-8")
    monkeypatch.setenv("STATIC_DIR", str(static))
    client = make_client()

    root = client.get("/")
    assert root.status_code == 200 and "FinAlly" in root.text
    assert client.get("/_next/app.js").text == "console.log(1)"
    assert client.get("/api/health").json() == {"status": "ok"}
    assert client.get("/api/portfolio").status_code == 200
    assert client.get("/api/does-not-exist").status_code == 404


def test_no_static_dir_serves_api_only(client: TestClient) -> None:
    assert client.get("/").status_code == 404
    assert client.get("/api/health").status_code == 200


def test_stream_route_registered(client: TestClient) -> None:
    paths = {getattr(r, "path", None) for r in client.app.routes}
    assert "/api/stream/prices" in paths


def test_chat_routes_registered(client: TestClient) -> None:
    paths = {getattr(r, "path", None) for r in client.app.routes}
    assert {"/api/chat", "/api/chat/history"} <= paths
