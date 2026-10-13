"""Unit tests for app.services.portfolio, with a controllable price cache."""

from __future__ import annotations

from pathlib import Path

import pytest

from app import db
from app.market import PriceCache
from app.services import portfolio
from app.services.errors import ApiError, TradeRejected


class FakeMarket:
    """Stands in for MarketDataService: prices come from a dict, sync is recorded."""

    def __init__(self, cache: PriceCache, prices: dict[str, float]) -> None:
        self.cache = cache
        self.prices = prices
        self.synced: list[tuple[set[str], set[str]]] = []

    async def ensure_price(self, ticker: str, timeout: float = 3.0) -> float | None:
        price = self.prices.get(ticker)
        if price is not None:
            self.cache.update(ticker, price)
        return price

    async def sync(self, watchlist: set[str], held: set[str]) -> None:
        self.synced.append((watchlist, held))


@pytest.fixture
def setup(app_env: Path):
    db.init_db(app_env)
    cache = PriceCache()
    market = FakeMarket(cache, {"AAPL": 100.0, "MSFT": 50.0})
    portfolio.configure(market, cache)  # type: ignore[arg-type]
    yield market, cache
    portfolio.configure(None, None)


def test_empty_portfolio(setup) -> None:
    pf = portfolio.get_portfolio()
    assert pf["total_value"] == 10000.0 and pf["positions"] == []


async def test_valuation_and_weights(setup) -> None:
    market, cache = setup
    await portfolio.execute_trade("AAPL", "buy", 10)  # $1,000
    await portfolio.execute_trade("MSFT", "buy", 20)  # $1,000
    cache.update("AAPL", 110.0)
    pf = portfolio.get_portfolio()
    assert pf["cash_balance"] == 8000.0
    assert pf["positions_value"] == 2100.0
    assert pf["total_value"] == 10100.0
    assert pf["unrealized_pnl"] == 100.0
    assert pf["unrealized_pnl_percent"] == 5.0
    aapl = next(p for p in pf["positions"] if p["ticker"] == "AAPL")
    assert aapl["unrealized_pnl"] == 100.0
    assert aapl["unrealized_pnl_percent"] == 10.0
    assert aapl["weight"] == pytest.approx(1100 / 10100, abs=1e-6)
    assert sum(p["weight"] for p in pf["positions"]) == pytest.approx(2100 / 10100, abs=1e-5)


async def test_missing_price_falls_back_to_avg_cost(setup) -> None:
    _, cache = setup
    await portfolio.execute_trade("AAPL", "buy", 10)
    cache.remove("AAPL")
    [pos] = portfolio.get_portfolio()["positions"]
    assert pos["current_price"] is None
    assert pos["market_value"] == 1000.0
    assert pos["unrealized_pnl"] == 0.0


async def test_trade_syncs_market_and_records_snapshot(setup) -> None:
    market, _ = setup
    before = len(db.list_snapshots())
    await portfolio.execute_trade("AAPL", "buy", 1)
    assert market.synced[-1][1] == {"AAPL"}
    assert len(db.list_snapshots()) == before + 1


@pytest.mark.parametrize(
    ("ticker", "side", "qty"),
    [
        ("AAPL", "short", 1),
        ("AAPL", None, 1),
        ("AAPL", "buy", 0),
        ("AAPL", "buy", 0.00004),
        ("AAPL", "buy", float("nan")),
        ("AAPL", "buy", float("inf")),
        ("AAPL", "buy", None),
        ("", "buy", 1),
        (None, "buy", 1),
    ],
)
async def test_execute_trade_validation(setup, ticker, side, qty) -> None:
    with pytest.raises(TradeRejected) as exc:
        await portfolio.execute_trade(ticker, side, qty)
    assert exc.value.status_code == 400


async def test_side_is_case_insensitive(setup) -> None:
    trade = await portfolio.execute_trade("aapl", " BUY ", "1.5")
    assert trade["side"] == "buy" and trade["quantity"] == 1.5


async def test_no_price_is_409(setup) -> None:
    with pytest.raises(TradeRejected) as exc:
        await portfolio.execute_trade("ZZZ", "buy", 1)
    assert exc.value.status_code == 409


async def test_db_trade_error_is_400(setup) -> None:
    with pytest.raises(TradeRejected) as exc:
        await portfolio.execute_trade("AAPL", "buy", 1000)
    assert exc.value.status_code == 400
    assert "Insufficient cash" in exc.value.message


async def test_watchlist_service(setup) -> None:
    market, _ = setup
    item, created = await portfolio.add_watchlist_ticker("pypl")
    assert created and item["ticker"] == "PYPL" and item["price"] is None
    assert "PYPL" in market.synced[-1][0]
    item2 = await portfolio.add_to_watchlist("PYPL")
    assert item2["added_at"] == item["added_at"]
    await portfolio.remove_from_watchlist("PYPL")
    with pytest.raises(ApiError) as exc:
        await portfolio.remove_from_watchlist("PYPL")
    assert exc.value.status_code == 404
    with pytest.raises(ApiError) as exc:
        await portfolio.add_to_watchlist("not valid")
    assert exc.value.status_code == 400


def test_history_downsampled_to_500(setup, monkeypatch) -> None:
    rows = [{"total_value": float(i), "recorded_at": f"t{i}"} for i in range(1234)]
    monkeypatch.setattr(db, "list_snapshots", lambda since_iso=None: rows)
    hist = portfolio.get_history()
    assert len(hist) == 500
    assert hist[0] == rows[0] and hist[-1] == rows[-1]
    values = [h["total_value"] for h in hist]
    assert values == sorted(values)


def test_history_small_passthrough_and_window(setup) -> None:
    db.insert_snapshot(123.0)
    hist = portfolio.get_history()
    assert hist[-1]["total_value"] == 123.0


def test_unconfigured_market_raises(app_env) -> None:
    portfolio.configure(None, None)
    with pytest.raises(RuntimeError):
        portfolio.get_market()
    with pytest.raises(RuntimeError):
        portfolio.get_cache()
