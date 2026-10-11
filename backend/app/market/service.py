"""MarketDataService: the one place that decides which tickers are tracked."""

from __future__ import annotations

import asyncio
import time

from .cache import PriceCache
from .interface import MarketDataSource
from .tickers import normalize_ticker


class MarketDataService:
    """Owns the source and cache. Tracked tickers = watchlist ∪ held positions.

    Routes never call source.add_ticker/remove_ticker directly; they call sync()
    or ensure_price() so a held ticker is never evicted from the cache.
    """

    def __init__(self, source: MarketDataSource, cache: PriceCache) -> None:
        self.source = source
        self.cache = cache
        self._lock = asyncio.Lock()  # serialises sync() calls from concurrent requests

    async def start(
        self,
        watchlist: set[str],
        held: set[str],
        initial_prices: dict[str, float] | None = None,
    ) -> None:
        await self.source.start(sorted(watchlist | held), initial_prices=initial_prices)

    async def stop(self) -> None:
        await self.source.stop()

    def tracked(self) -> set[str]:
        return set(self.source.get_tickers())

    async def sync(self, watchlist: set[str], held: set[str]) -> None:
        """Make the tracked set equal watchlist ∪ held. Call after any watchlist change or trade."""
        async with self._lock:
            wanted = {normalize_ticker(t) for t in watchlist | held}
            current = self.tracked()
            for ticker in sorted(wanted - current):
                await self.source.add_ticker(ticker)
            for ticker in sorted(current - wanted):
                await self.source.remove_ticker(ticker)

    async def ensure_price(self, ticker: str, timeout: float = 3.0) -> float | None:
        """Track `ticker` if needed and wait briefly for its first price.

        Simulator: returns at once. Massive live: usually within one poll (~1 s).
        Massive EOD / unknown symbol: may return None → caller answers 409.
        The ticker stays tracked; the next sync() drops it if nobody needs it.
        """
        ticker = normalize_ticker(ticker)
        if ticker not in self.tracked():
            await self.source.add_ticker(ticker)
        deadline = time.monotonic() + timeout
        while (price := self.cache.get_price(ticker)) is None and time.monotonic() < deadline:
            await asyncio.sleep(0.1)
        return price
