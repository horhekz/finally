"""Integration tests for SimulatorDataSource."""

import asyncio

import pytest

from app.market.cache import PriceCache
from app.market.simulator import TRADING_SECONDS_PER_YEAR, SimulatorDataSource
from app.market.tickers import InvalidTickerError


@pytest.mark.asyncio
class TestSimulatorDataSource:
    """Integration tests for the SimulatorDataSource."""

    async def test_start_populates_cache(self):
        """Test that start() immediately populates the cache."""
        cache = PriceCache()
        source = SimulatorDataSource(price_cache=cache, update_interval=0.1)
        await source.start(["AAPL", "GOOGL"])

        # Cache should have seed prices immediately (before first loop tick)
        assert cache.get("AAPL") is not None
        assert cache.get("GOOGL") is not None

        await source.stop()

    async def test_prices_update_over_time(self):
        """Test that prices are updated periodically."""
        cache = PriceCache()
        source = SimulatorDataSource(price_cache=cache, update_interval=0.05)
        await source.start(["AAPL"])

        initial_version = cache.version
        await asyncio.sleep(0.3)  # Several update cycles

        # Version should have incremented (prices updated)
        assert cache.version > initial_version

        await source.stop()

    async def test_stop_is_clean(self):
        """Test that stop() is clean and idempotent."""
        cache = PriceCache()
        source = SimulatorDataSource(price_cache=cache, update_interval=0.1)
        await source.start(["AAPL"])
        await source.stop()
        # Double stop should not raise
        await source.stop()

    async def test_add_ticker(self):
        """Test adding a ticker dynamically."""
        cache = PriceCache()
        source = SimulatorDataSource(price_cache=cache, update_interval=0.1)
        await source.start(["AAPL"])

        await source.add_ticker("TSLA")
        assert "TSLA" in source.get_tickers()
        assert cache.get("TSLA") is not None

        await source.stop()

    async def test_remove_ticker(self):
        """Test removing a ticker."""
        cache = PriceCache()
        source = SimulatorDataSource(price_cache=cache, update_interval=0.1)
        await source.start(["AAPL", "TSLA"])

        await source.remove_ticker("TSLA")
        assert "TSLA" not in source.get_tickers()
        assert cache.get("TSLA") is None

        await source.stop()

    async def test_get_tickers(self):
        """Test getting the list of active tickers."""
        cache = PriceCache()
        source = SimulatorDataSource(price_cache=cache, update_interval=0.1)
        await source.start(["AAPL", "GOOGL"])

        tickers = source.get_tickers()
        assert set(tickers) == {"AAPL", "GOOGL"}

        await source.stop()

    async def test_empty_start(self):
        """Test starting with no tickers."""
        cache = PriceCache()
        source = SimulatorDataSource(price_cache=cache, update_interval=0.1)
        await source.start([])

        assert len(cache) == 0
        assert source.get_tickers() == []

        await source.stop()

    async def test_exception_resilience(self):
        """Test that simulator continues running after errors."""
        cache = PriceCache()
        source = SimulatorDataSource(price_cache=cache, update_interval=0.05)

        # Start with a valid ticker
        await source.start(["AAPL"])

        # Wait for some updates
        await asyncio.sleep(0.15)

        # Task should still be running
        assert source._task is not None
        assert not source._task.done()

        await source.stop()

    async def test_custom_update_interval(self):
        """Test using a custom update interval."""
        cache = PriceCache()
        source = SimulatorDataSource(price_cache=cache, update_interval=0.01)
        await source.start(["AAPL"])

        initial_version = cache.version
        await asyncio.sleep(0.05)  # Should get ~5 updates

        # Should have multiple updates with fast interval
        assert cache.version > initial_version + 2

        await source.stop()

    async def test_custom_event_probability(self):
        """Test creating source with custom event probability."""
        cache = PriceCache()
        # Very high event probability for testing
        source = SimulatorDataSource(
            price_cache=cache, update_interval=0.1, event_probability=1.0
        )
        await source.start(["AAPL"])

        # Just verify it starts and stops cleanly
        await asyncio.sleep(0.2)
        await source.stop()


async def test_simulator_source_contract():
    """Design doc §8.5 guarantees 2-5 and 8 on the async wrapper."""
    cache = PriceCache()
    src = SimulatorDataSource(cache, update_interval=0.05, seed=7)
    await src.start(["aapl", "PYPL", "AAPL"], initial_prices={"pypl": 70.0})
    assert src.get_tickers() == ["AAPL", "PYPL"]  # normalised + de-duplicated
    assert cache.get("AAPL").price == 190.0
    assert cache.get("AAPL").reference_price == 190.0
    assert cache.get_price("PYPL") == 70.0  # initial_prices hint honoured
    await src.add_ticker("nflx")
    assert cache.get_price("NFLX") == 600.0  # priced before add_ticker returns
    assert cache.get("NFLX").reference_price == 600.0
    await src.remove_ticker("AAPL")
    assert "AAPL" not in cache and "AAPL" not in src.get_tickers()
    await src.stop()
    v = cache.version
    await asyncio.sleep(0.15)
    assert cache.version == v  # no writes after stop
    await src.stop()  # idempotent


async def test_add_ticker_twice_does_not_reset_price():
    cache = PriceCache()
    src = SimulatorDataSource(cache, update_interval=0.01, seed=3)
    await src.start(["AAPL"])
    await asyncio.sleep(0.1)
    await src.stop()
    moved = cache.get_price("AAPL")
    await src.add_ticker("AAPL")
    assert cache.get_price("AAPL") == moved


async def test_reference_price_survives_ticks():
    cache = PriceCache()
    src = SimulatorDataSource(cache, update_interval=0.01, seed=3)
    await src.start(["AAPL"])
    await asyncio.sleep(0.1)
    await src.stop()
    update = cache.get("AAPL")
    assert update.reference_price == 190.0
    assert update.day_change == round(update.price - 190.0, 4)


async def test_invalid_ticker_rejected():
    cache = PriceCache()
    src = SimulatorDataSource(cache, update_interval=0.1)
    with pytest.raises(InvalidTickerError):
        await src.start(["AAPL", "$$$"])
    src2 = SimulatorDataSource(cache, update_interval=0.1)
    await src2.start(["AAPL"])
    try:
        with pytest.raises(InvalidTickerError):
            await src2.add_ticker("1ABC")
    finally:
        await src2.stop()


async def test_seeded_sources_produce_identical_paths():
    paths = []
    for _ in range(2):
        cache = PriceCache()
        src = SimulatorDataSource(cache, update_interval=0.5, seed=11)
        await src.start(["AAPL", "ZZZZ"])
        await src.stop()
        paths.append((cache.get_price("ZZZZ"), [src._sim.step() for _ in range(10)]))
    assert paths[0] == paths[1]


async def test_dt_derived_from_update_interval():
    src = SimulatorDataSource(PriceCache(), update_interval=1.0)
    await src.start(["AAPL"])
    await src.stop()
    assert src._sim._dt == 1.0 / TRADING_SECONDS_PER_YEAR
