"""Thread-safe in-memory price cache."""

from __future__ import annotations

import time
from threading import Lock

from .models import PriceUpdate


class PriceCache:
    """Latest price per ticker. One writer (the active data source), many readers.

    Writers: SimulatorDataSource or MassiveDataSource.
    Readers: SSE stream, portfolio valuation, trade execution, LLM context.
    """

    def __init__(self) -> None:
        self._prices: dict[str, PriceUpdate] = {}
        self._lock = Lock()
        self._version = 0  # bumped on every mutation (update AND remove)

    def update(
        self,
        ticker: str,
        price: float,
        timestamp: float | None = None,
        reference_price: float | None = None,
    ) -> PriceUpdate:
        """Record a new price and return the resulting PriceUpdate.

        - First update for a ticker: previous_price == price (direction 'flat').
        - reference_price is sticky: None keeps the stored value; if nothing is
          stored yet, the first price seen becomes the reference.
        """
        with self._lock:
            prev = self._prices.get(ticker)
            if reference_price is None:
                reference_price = prev.reference_price if prev else price
            update = PriceUpdate(
                ticker=ticker,
                price=round(price, 2),
                previous_price=prev.price if prev else round(price, 2),
                timestamp=timestamp if timestamp is not None else time.time(),
                reference_price=round(reference_price, 2) if reference_price else None,
            )
            self._prices[ticker] = update
            self._version += 1
            return update

    def get(self, ticker: str) -> PriceUpdate | None:
        """Latest update for one ticker, or None if it has no price."""
        with self._lock:
            return self._prices.get(ticker)

    def get_price(self, ticker: str) -> float | None:
        """Just the latest price, or None."""
        update = self.get(ticker)
        return update.price if update else None

    def get_all(self) -> dict[str, PriceUpdate]:
        """Shallow copy — safe to iterate without holding the lock."""
        with self._lock:
            return dict(self._prices)

    def remove(self, ticker: str) -> None:
        """Evict a ticker. Bumps the version only if something was removed."""
        with self._lock:
            if self._prices.pop(ticker, None) is not None:
                self._version += 1  # so SSE pushes a payload without this ticker

    @property
    def version(self) -> int:
        """Monotonic mutation counter, used by SSE for change detection."""
        with self._lock:
            return self._version

    def __len__(self) -> int:
        with self._lock:
            return len(self._prices)

    def __contains__(self, ticker: str) -> bool:
        with self._lock:
            return ticker in self._prices
