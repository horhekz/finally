"""Abstract interface for market data sources."""

from __future__ import annotations

from abc import ABC, abstractmethod


class MarketDataSource(ABC):
    """Contract for market data providers.

    Implementations push prices into a shared PriceCache on their own schedule.
    Downstream code never asks the source for a price — it reads the cache.

    Every implementation guarantees:
      * start() returns with the cache already holding a price for every
        starting ticker it can price (simulator: always; Massive: whatever the
        first poll returned).
      * Ticker arguments are normalised (normalize_ticker) on the way in.
      * add_ticker() on a tracked ticker and remove_ticker() on an unknown one
        are no-ops.
      * remove_ticker() also evicts the ticker from the cache.
      * Errors inside the background task are logged, never raised; the last
        good price stays in the cache.
      * stop() is idempotent; after it returns the source never writes again.

    Lifecycle:
        source = create_market_data_source(cache)
        await source.start(["AAPL", "MSFT"], initial_prices={"PYPL": 71.2})
        await source.add_ticker("tsla")      # -> "TSLA"
        await source.remove_ticker("MSFT")   # also evicted from the cache
        await source.stop()
    """

    @abstractmethod
    async def start(
        self, tickers: list[str], initial_prices: dict[str, float] | None = None
    ) -> None:
        """Begin producing prices. Call exactly once.

        initial_prices is a hint for sources that invent prices (the simulator
        starts those tickers there instead of at a seed). Real sources ignore it.
        """

    @abstractmethod
    async def stop(self) -> None:
        """Cancel the background task. Safe to call more than once."""

    @abstractmethod
    async def add_ticker(self, ticker: str) -> None:
        """Start tracking a ticker. No-op if already tracked."""

    @abstractmethod
    async def remove_ticker(self, ticker: str) -> None:
        """Stop tracking a ticker and evict it from the cache. No-op if unknown."""

    @abstractmethod
    def get_tickers(self) -> list[str]:
        """Currently tracked tickers."""
