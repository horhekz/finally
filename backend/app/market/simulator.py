"""GBM-based market simulator."""

from __future__ import annotations

import asyncio
import logging
import math

import numpy as np

from .cache import PriceCache
from .interface import MarketDataSource
from .seed_prices import (
    CORRELATION_GROUPS,
    CROSS_GROUP_CORR,
    DEFAULT_PARAMS,
    INTRA_FINANCE_CORR,
    INTRA_TECH_CORR,
    SEED_PRICES,
    TICKER_PARAMS,
    TSLA_CORR,
)
from .tickers import normalize_ticker

logger = logging.getLogger(__name__)

TRADING_SECONDS_PER_YEAR = 252 * 6.5 * 3600  # 5,896,800


class GBMSimulator:
    """Correlated Geometric Brownian Motion with occasional jump events.

        S(t+dt) = S(t) * exp((mu - sigma^2/2) * dt + sigma * sqrt(dt) * Z)

    Pure and synchronous: no asyncio, no I/O, no cache. All randomness comes
    from the injected numpy Generator, so a fixed seed gives a fixed path.
    """

    TRADING_SECONDS_PER_YEAR = TRADING_SECONDS_PER_YEAR
    DEFAULT_DT = 0.5 / TRADING_SECONDS_PER_YEAR  # one 500 ms tick ≈ 8.48e-8 years

    def __init__(
        self,
        tickers: list[str],
        dt: float = DEFAULT_DT,
        event_probability: float = 0.001,
        rng: np.random.Generator | None = None,
        initial_prices: dict[str, float] | None = None,
    ) -> None:
        self._dt = dt
        self._sqrt_dt = math.sqrt(dt)
        self._event_prob = event_probability
        self._rng = rng if rng is not None else np.random.default_rng()

        self._tickers: list[str] = []
        self._prices: dict[str, float] = {}  # full precision; rounded only on output
        self._start_prices: dict[str, float] = {}  # reference for "day change"
        self._params: dict[str, dict[str, float]] = {}
        self._cholesky: np.ndarray | None = None

        initial_prices = initial_prices or {}
        for ticker in tickers:
            self._add_ticker_internal(ticker, initial_prices.get(ticker))
        self._rebuild_cholesky()

    # --- Public API ---

    def step(self) -> dict[str, float]:
        """Advance every ticker one tick. Returns {ticker: price rounded to cents}."""
        n = len(self._tickers)
        if n == 0:
            return {}

        z = self._rng.standard_normal(n)
        if self._cholesky is not None:
            z = self._cholesky @ z
        events = self._rng.random(n) < self._event_prob

        result: dict[str, float] = {}
        for i, ticker in enumerate(self._tickers):
            mu = self._params[ticker]["mu"]
            sigma = self._params[ticker]["sigma"]
            drift = (mu - 0.5 * sigma**2) * self._dt
            diffusion = sigma * self._sqrt_dt * z[i]
            price = self._prices[ticker] * math.exp(drift + diffusion)

            if events[i]:  # 2-5% jump, random sign
                shock = self._rng.uniform(0.02, 0.05) * (1 if self._rng.random() < 0.5 else -1)
                price *= 1 + shock
                logger.debug("Event on %s: %+.1f%%", ticker, shock * 100)

            self._prices[ticker] = price
            result[ticker] = round(price, 2)
        return result

    def add_ticker(self, ticker: str, initial_price: float | None = None) -> None:
        """Add a ticker (no-op if present). Rebuilds the correlation matrix."""
        if ticker in self._prices:
            return
        self._add_ticker_internal(ticker, initial_price)
        self._rebuild_cholesky()

    def remove_ticker(self, ticker: str) -> None:
        """Remove a ticker (no-op if unknown). Rebuilds the correlation matrix."""
        if ticker not in self._prices:
            return
        self._tickers.remove(ticker)
        del self._prices[ticker], self._start_prices[ticker], self._params[ticker]
        self._rebuild_cholesky()

    def get_price(self, ticker: str) -> float | None:
        """Current price rounded to cents, or None if not tracked."""
        price = self._prices.get(ticker)
        return round(price, 2) if price is not None else None

    def get_start_price(self, ticker: str) -> float | None:
        """Price the ticker started at (the "day change" reference)."""
        return self._start_prices.get(ticker)

    def get_tickers(self) -> list[str]:
        """Currently simulated tickers, in insertion order."""
        return list(self._tickers)

    # --- Internals ---

    def _add_ticker_internal(self, ticker: str, initial_price: float | None) -> None:
        if ticker in self._prices:
            return
        price = initial_price or SEED_PRICES.get(ticker) or float(self._rng.uniform(50.0, 300.0))
        self._tickers.append(ticker)
        self._prices[ticker] = price
        self._start_prices[ticker] = round(price, 2)
        self._params[ticker] = dict(TICKER_PARAMS.get(ticker, DEFAULT_PARAMS))

    def _rebuild_cholesky(self) -> None:
        """Factor the n x n correlation matrix. Only runs when the ticker set changes."""
        n = len(self._tickers)
        if n <= 1:
            self._cholesky = None
            return
        corr = np.eye(n)
        for i in range(n):
            for j in range(i + 1, n):
                rho = self._pairwise_correlation(self._tickers[i], self._tickers[j])
                corr[i, j] = corr[j, i] = rho
        try:
            self._cholesky = np.linalg.cholesky(corr)
        except np.linalg.LinAlgError:
            logger.warning("Correlation matrix not positive definite; using independent moves")
            self._cholesky = None

    @staticmethod
    def _pairwise_correlation(t1: str, t2: str) -> float:
        """Tech/tech 0.6, finance/finance 0.5, TSLA with anything 0.3, else 0.3."""
        if t1 == "TSLA" or t2 == "TSLA":
            return TSLA_CORR
        tech, finance = CORRELATION_GROUPS["tech"], CORRELATION_GROUPS["finance"]
        if t1 in tech and t2 in tech:
            return INTRA_TECH_CORR
        if t1 in finance and t2 in finance:
            return INTRA_FINANCE_CORR
        return CROSS_GROUP_CORR


class SimulatorDataSource(MarketDataSource):
    """MarketDataSource that runs GBMSimulator on an asyncio loop."""

    def __init__(
        self,
        price_cache: PriceCache,
        update_interval: float = 0.5,
        event_probability: float = 0.001,
        seed: int | None = None,
    ) -> None:
        self._cache = price_cache
        self._interval = update_interval
        self._event_prob = event_probability
        self._rng = np.random.default_rng(seed)
        self._sim: GBMSimulator | None = None
        self._task: asyncio.Task | None = None

    async def start(
        self, tickers: list[str], initial_prices: dict[str, float] | None = None
    ) -> None:
        tickers = list(dict.fromkeys(normalize_ticker(t) for t in tickers))
        initial = {normalize_ticker(t): p for t, p in (initial_prices or {}).items()}
        self._sim = GBMSimulator(
            tickers=tickers,
            dt=self._interval / TRADING_SECONDS_PER_YEAR,  # keep volatility per real second fixed
            event_probability=self._event_prob,
            rng=self._rng,
            initial_prices=initial,
        )
        for ticker in tickers:
            self._seed_cache(ticker)
        self._task = asyncio.create_task(self._run_loop(), name="simulator-loop")
        logger.info("Simulator started with %d tickers", len(tickers))

    async def stop(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._task = None
        logger.info("Simulator stopped")

    async def add_ticker(self, ticker: str) -> None:
        ticker = normalize_ticker(ticker)
        if self._sim is None or ticker in self._sim.get_tickers():
            return
        self._sim.add_ticker(ticker)
        self._seed_cache(ticker)  # priced before this call returns
        logger.info("Simulator: added %s", ticker)

    async def remove_ticker(self, ticker: str) -> None:
        ticker = normalize_ticker(ticker)
        if self._sim:
            self._sim.remove_ticker(ticker)
        self._cache.remove(ticker)
        logger.info("Simulator: removed %s", ticker)

    def get_tickers(self) -> list[str]:
        return self._sim.get_tickers() if self._sim else []

    def _seed_cache(self, ticker: str) -> None:
        assert self._sim is not None
        start = self._sim.get_start_price(ticker)
        if start is not None:
            self._cache.update(ticker, start, reference_price=start)

    async def _run_loop(self) -> None:
        while True:
            try:
                if self._sim:
                    for ticker, price in self._sim.step().items():
                        self._cache.update(ticker, price)
            except Exception:
                logger.exception("Simulator step failed")  # never kill the loop
            await asyncio.sleep(self._interval)
