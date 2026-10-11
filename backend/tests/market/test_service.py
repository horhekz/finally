"""Tests for MarketDataService (design doc §11)."""

import pytest

from app.market import InvalidTickerError, MarketDataService, PriceCache
from app.market.interface import MarketDataSource
from app.market.simulator import SimulatorDataSource


class NeverPricedSource(MarketDataSource):
    """Tracks tickers but never writes a price (like Massive for an unknown symbol)."""

    def __init__(self) -> None:
        self.tickers: list[str] = []
        self.started_with: tuple[list[str], dict | None] | None = None

    async def start(self, tickers, initial_prices=None):
        self.started_with = (tickers, initial_prices)
        self.tickers = list(tickers)

    async def stop(self):
        pass

    async def add_ticker(self, ticker):
        if ticker not in self.tickers:
            self.tickers.append(ticker)

    async def remove_ticker(self, ticker):
        self.tickers = [t for t in self.tickers if t != ticker]

    def get_tickers(self):
        return list(self.tickers)


@pytest.fixture
async def svc():
    cache = PriceCache()
    service = MarketDataService(SimulatorDataSource(cache, seed=1), cache)
    yield service
    await service.stop()


async def test_start_tracks_union_of_watchlist_and_held(svc):
    await svc.start({"AAPL", "MSFT"}, {"PYPL"}, initial_prices={"PYPL": 71.2})
    assert svc.tracked() == {"AAPL", "MSFT", "PYPL"}
    assert svc.cache.get_price("PYPL") == 71.2


async def test_start_passes_sorted_union_to_source():
    src = NeverPricedSource()
    service = MarketDataService(src, PriceCache())
    await service.start({"MSFT", "AAPL"}, {"AAPL", "PYPL"}, initial_prices={"PYPL": 70.0})
    assert src.started_with == (["AAPL", "MSFT", "PYPL"], {"PYPL": 70.0})


async def test_service_keeps_held_tickers(svc):
    await svc.start({"AAPL", "MSFT"}, set())
    await svc.sync(watchlist={"MSFT"}, held={"AAPL"})
    assert svc.tracked() == {"AAPL", "MSFT"} and "AAPL" in svc.cache
    await svc.sync(watchlist={"MSFT"}, held=set())
    assert svc.tracked() == {"MSFT"} and "AAPL" not in svc.cache


async def test_sync_adds_and_normalises(svc):
    await svc.start({"AAPL"}, set())
    await svc.sync(watchlist={"aapl", " tsla "}, held={"pypl"})
    assert svc.tracked() == {"AAPL", "TSLA", "PYPL"}
    assert all(t in svc.cache for t in ("AAPL", "TSLA", "PYPL"))


async def test_sync_rejects_invalid_ticker(svc):
    await svc.start({"AAPL"}, set())
    with pytest.raises(InvalidTickerError):
        await svc.sync(watchlist={"$$$"}, held=set())
    assert svc.tracked() == {"AAPL"}


async def test_ensure_price_tracks_and_prices_immediately(svc):
    await svc.start({"AAPL"}, set())
    price = await svc.ensure_price("pypl")
    assert price is not None and price > 0
    assert "PYPL" in svc.tracked()


async def test_ensure_price_for_tracked_ticker(svc):
    await svc.start({"AAPL"}, set())
    assert await svc.ensure_price("AAPL") == 190.0


async def test_ensure_price_times_out_with_none():
    src = NeverPricedSource()
    service = MarketDataService(src, PriceCache())
    await service.start(set(), set())
    assert await service.ensure_price("ZZZZ", timeout=0.2) is None
    assert "ZZZZ" in service.tracked()  # stays tracked until the next sync()
    await service.sync(set(), set())
    assert service.tracked() == set()
