# Market Data Backend — Detailed Design

The implementation reference for FinAlly's market data layer: one unified Python API, backed by either the **GBM simulator** (default) or the **Massive REST API** (when `MASSIVE_API_KEY` is set). Each section has the complete code for one module, so an agent can implement it, or check the existing code against it, by working through this document from top to bottom.

| | |
|---|---|
| **Scope** | `backend/app/market/` (10 modules) and `backend/tests/market/` |
| **Supersedes** | `planning/archive/MARKET_DATA_DESIGN.md` (the original design, written before the review) |
| **Builds on** | `MARKET_INTERFACE.md` (interface decisions), `MARKET_SIMULATOR.md` (simulator math), `MASSIVE_API.md` (API research), `PLAN.md` §6 and §13 |
| **Status** | Design. The module code in §4–§12 was run as a working package against `massive` 2.2.0 on Python 3.13: 84 tests passed (the existing model, cache, simulator and factory tests, plus every test in §14), `ruff` was clean, and a live SSE smoke test under uvicorn passed. §15 lists what has to change in the current code. |

---

## Table of Contents

1. [Goals and Decisions](#1-goals-and-decisions)
2. [Architecture](#2-architecture)
3. [Module Layout and Public API](#3-module-layout-and-public-api)
4. [Data Model: `models.py`](#4-data-model-modelspy)
5. [Price Cache: `cache.py`](#5-price-cache-cachepy)
6. [Ticker Rules: `tickers.py`](#6-ticker-rules-tickerspy)
7. [Unified Interface: `interface.py`](#7-unified-interface-interfacepy)
8. [Simulator: `seed_prices.py` + `simulator.py`](#8-simulator-seed_pricespy--simulatorpy)
9. [Massive API: `massive_client.py`](#9-massive-api-massive_clientpy)
10. [Factory and Configuration: `factory.py`](#10-factory-and-configuration-factorypy)
11. [Tracked-Ticker Service: `service.py`](#11-tracked-ticker-service-servicepy)
12. [SSE Streaming: `stream.py`](#12-sse-streaming-streampy)
13. [FastAPI Integration and Consumer Examples](#13-fastapi-integration-and-consumer-examples)
14. [Testing](#14-testing)
15. [Migration Checklist vs. Current Code](#15-migration-checklist-vs-current-code)
16. [Failure Modes and Edge Cases](#16-failure-modes-and-edge-cases)
17. [Non-Goals](#17-non-goals)

---

## 1. Goals and Decisions

### 1.1 Design principles

1. **Push, don't pull.** The active source writes to a shared in-memory `PriceCache` on its own schedule. Consumers (SSE, trades, portfolio, chat) **only read the cache**, so a slow Massive call can never block a request.
2. **One interface, two implementations.** `MarketDataSource` is an ABC. `SimulatorDataSource` and `MassiveDataSource` implement it. Nothing downstream knows which one is running.
3. **Choose once, at startup.** `create_market_data_source()` reads the environment one time.
4. **Never crash the loop.** Errors in the background task are logged, and the last good price stays in the cache.
5. **The data class is the wire format.** `PriceUpdate.to_dict()` is exactly what goes out over SSE and REST.
6. **One owner for the tracked set.** Only `MarketDataService` calls `add_ticker` / `remove_ticker`, which keeps held positions priced.

### 1.2 Decisions that settle PLAN.md §13 items

| PLAN item | Decision | Where |
|---|---|---|
| §13.1 #1: removing a held ticker breaks valuation | **Option (a)**: tracked = watchlist ∪ held positions. Removing a ticker from the watchlist never evicts one you hold. | §11 `MarketDataService.sync` |
| §13.1 #2: trading an untracked ticker | Auto-track it, wait up to 3 s for a price, then return **409** "price not yet available, retry". | §11 `ensure_price`, §13.3 |
| §13.1 #3: what counts as a valid ticker | Strip and uppercase, then match `^[A-Z][A-Z0-9.\-]{0,9}$`. Any well-formed symbol is accepted in simulator mode. | §6 |
| §13.1 #4: daily change % has no reference | Add a `reference_price` to `PriceUpdate`: the seed/start price for the simulator, `prev_day.close` for Massive. | §4, §5 |
| §13.1 #5: SSE payload shape | One event holding **all** tickers, keyed by ticker. The exact JSON is in §12.2. | §12 |
| §13.1 #14: no price yet | A ticker with no price is simply missing from the cache. Readers get `None`, the UI shows "—", and valuation falls back to `avg_cost`. | §13.3 |
| §13.4: lifespan start | The source starts in FastAPI `lifespan` with watchlist ∪ held. | §13.1 |
| Restart P&L jump (new) | On startup the simulator starts held tickers at their last fill price (`initial_prices`). | §8.4, §13.1 |
| Free Massive key gets 403 (new) | Fall back automatically to end-of-day prices from grouped daily bars. | §9.2 |

---

## 2. Architecture

```
                 ┌──────────── create_market_data_source(cache) ────────────┐
                 │  MASSIVE_API_KEY non-empty?                               │
                 │    yes → MassiveDataSource  (REST poll; live 15 s / EOD)  │
                 │    no  → SimulatorDataSource (GBM, 500 ms)                │
                 └──────────────────────────┬────────────────────────────────┘
                                            │  cache.update(ticker, price, ts, reference_price)
                                            ▼
                                  ┌───────────────────┐
                                  │    PriceCache     │  threading.Lock, version counter
                                  └─────────┬─────────┘
               reads (never writes)         │
     ┌───────────────────┬──────────────────┼──────────────────┬───────────────────┐
     ▼                   ▼                  ▼                  ▼                   ▼
 SSE /api/stream/   POST /api/        GET /api/          GET /api/          LLM chat
 prices (500 ms)    portfolio/trade   portfolio          watchlist          context
```

Ticker-set changes go the other way, and **only** through the service:

```
POST/DELETE /api/watchlist, trade fill, LLM actions
        │
        ▼
MarketDataService.sync(watchlist, held)  ──►  source.add_ticker / source.remove_ticker
MarketDataService.ensure_price(ticker)   ──►  source.add_ticker + wait for first price
```

**Concurrency model.** Everything runs on the single uvicorn event loop. The simulator steps on the loop itself, which takes microseconds. The Massive SDK is synchronous, so its HTTP calls run in `asyncio.to_thread`, and only `PriceCache` is touched from outside the loop. That's why `PriceCache` is the only class with a lock.

---

## 3. Module Layout and Public API

```
backend/app/market/
├── __init__.py        # public exports
├── models.py          # PriceUpdate                                  (§4)
├── cache.py           # PriceCache                                   (§5)
├── tickers.py         # normalize_ticker, InvalidTickerError, DEFAULT_TICKERS   NEW (§6)
├── interface.py       # MarketDataSource ABC                         (§7)
├── seed_prices.py     # seed prices, GBM params, correlation groups  (§8.2)
├── simulator.py       # GBMSimulator + SimulatorDataSource           (§8)
├── massive_client.py  # MassiveDataSource                            (§9)
├── factory.py         # create_market_data_source                    (§10)
├── service.py         # MarketDataService                            NEW (§11)
└── stream.py          # create_stream_router → GET /api/stream/prices (§12)
```

Other backend modules import **only** from the package:

```python
# backend/app/market/__init__.py
"""Market data subsystem for FinAlly. Import everything from here."""

from .cache import PriceCache
from .factory import create_market_data_source
from .interface import MarketDataSource
from .models import PriceUpdate
from .service import MarketDataService
from .stream import create_stream_router
from .tickers import DEFAULT_TICKERS, InvalidTickerError, normalize_ticker

__all__ = [
    "DEFAULT_TICKERS",
    "InvalidTickerError",
    "MarketDataService",
    "MarketDataSource",
    "PriceCache",
    "PriceUpdate",
    "create_market_data_source",
    "create_stream_router",
    "normalize_ticker",
]
```

---

## 4. Data Model: `models.py`

`PriceUpdate` is an immutable snapshot of one ticker. Derived values are properties, so they can never get out of sync with the price.

```python
# backend/app/market/models.py
"""Data models for market data."""

from __future__ import annotations

import time
from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class PriceUpdate:
    """Immutable snapshot of a single ticker's price at a point in time."""

    ticker: str
    price: float  # latest price, rounded to cents
    previous_price: float  # price at the previous update (tick-to-tick)
    timestamp: float = field(default_factory=time.time)  # Unix seconds
    reference_price: float | None = None  # "day open": prev close (Massive) / seed (simulator)

    @property
    def change(self) -> float:
        """Absolute price change from the previous update."""
        return round(self.price - self.previous_price, 4)

    @property
    def change_percent(self) -> float:
        """Percentage change from the previous update."""
        if self.previous_price == 0:
            return 0.0
        return round((self.price - self.previous_price) / self.previous_price * 100, 4)

    @property
    def direction(self) -> str:
        """'up', 'down', or 'flat' — drives the frontend flash animation."""
        if self.price > self.previous_price:
            return "up"
        if self.price < self.previous_price:
            return "down"
        return "flat"

    @property
    def day_change(self) -> float | None:
        """Absolute change versus the reference price, or None if unknown."""
        if not self.reference_price:
            return None
        return round(self.price - self.reference_price, 4)

    @property
    def day_change_percent(self) -> float | None:
        """Percentage change versus the reference price, or None if unknown."""
        if not self.reference_price:
            return None
        return round((self.price - self.reference_price) / self.reference_price * 100, 4)

    def to_dict(self) -> dict:
        """Serialize for JSON / SSE transmission. This IS the wire format."""
        return {
            "ticker": self.ticker,
            "price": self.price,
            "previous_price": self.previous_price,
            "timestamp": self.timestamp,
            "change": self.change,
            "change_percent": self.change_percent,
            "direction": self.direction,
            "reference_price": self.reference_price,
            "day_change": self.day_change,
            "day_change_percent": self.day_change_percent,
        }
```

Notes:

- **Two kinds of change.** `change` / `change_percent` / `direction` compare against the *previous tick* and drive the flash animation. `day_change` / `day_change_percent` compare against `reference_price` and drive the watchlist "daily change %" column.
- `reference_price` comes last and has a default, so existing positional constructors `PriceUpdate(t, p, pp, ts)` keep working.
- `slots=True` keeps instances small. The cache replaces them; it never mutates them.

### 4.1 Wire format (one ticker)

```json
{
  "ticker": "AAPL",
  "price": 190.42,
  "previous_price": 190.38,
  "timestamp": 1791322345.512,
  "change": 0.04,
  "change_percent": 0.021,
  "direction": "up",
  "reference_price": 190.0,
  "day_change": 0.42,
  "day_change_percent": 0.2211
}
```

| Field | Type | Meaning |
|---|---|---|
| `price` | number | Latest price, rounded to cents |
| `previous_price` | number | Price at the previous update. Equal to `price` on the first update. |
| `timestamp` | number | Unix **seconds** (float). For Massive, the time of the last trade. |
| `change`, `change_percent` | number | Tick-to-tick |
| `direction` | `"up" \| "down" \| "flat"` | Tick-to-tick, for the flash |
| `reference_price` | number \| null | Simulator: the start price. Massive: the previous close. |
| `day_change`, `day_change_percent` | number \| null | vs. `reference_price`. `null` only when there's no reference. |

---

## 5. Price Cache: `cache.py`

```python
# backend/app/market/cache.py
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
        with self._lock:
            return self._prices.get(ticker)

    def get_price(self, ticker: str) -> float | None:
        update = self.get(ticker)
        return update.price if update else None

    def get_all(self) -> dict[str, PriceUpdate]:
        """Shallow copy — safe to iterate without holding the lock."""
        with self._lock:
            return dict(self._prices)

    def remove(self, ticker: str) -> None:
        with self._lock:
            if self._prices.pop(ticker, None) is not None:
                self._version += 1  # so SSE pushes a payload without this ticker

    @property
    def version(self) -> int:
        with self._lock:
            return self._version

    def __len__(self) -> int:
        with self._lock:
            return len(self._prices)

    def __contains__(self, ticker: str) -> bool:
        with self._lock:
            return ticker in self._prices
```

Notes:

- **Why a version counter?** The SSE loop compares one integer every 500 ms instead of diffing dicts. It only serialises and sends when something changed, so in Massive mode (one update every 15 s or less) idle streams cost almost nothing.
- **`remove()` bumps the version.** Without that, a removed ticker would stay on screen until the next price update. In Massive EOD mode, that could be 30 minutes.
- **Sticky reference.** The simulator passes `reference_price` once, when it seeds the cache, and later `update(t, p)` calls keep it. Massive passes it on every poll, which also handles a new trading day correctly: `prev_day.close` changes and the reference follows.
- `previous_price` is taken from the stored (already rounded) price, so `direction` reflects visible cent changes. A sub-cent move shows as `"flat"`, with no flash.
- `timestamp if timestamp is not None` (not `timestamp or ...`) so a timestamp of `0` in a test isn't silently replaced.
- Memory is O(number of tickers). The cache stores no history. Sparklines and the main chart build up on the frontend from SSE.

### 5.1 Usage

```python
cache = PriceCache()
cache.update("AAPL", 190.00, reference_price=190.00)   # first: flat, ref 190
u = cache.update("AAPL", 191.90)                      # up; ref stays 190
u.direction, u.change, u.day_change_percent           # ('up', 1.9, 1.0)
cache.get_price("AAPL")                               # 191.9
cache.get_price("ZZZZ")                               # None → UI shows "—"
cache.get_all()                                       # {'AAPL': PriceUpdate(...)}
cache.remove("AAPL"); "AAPL" in cache                 # False
```

---

## 6. Ticker Rules: `tickers.py`

There's one normalisation function, and every entry point uses it: API routes, LLM actions, both sources, and the service. That way `aapl`, ` AAPL ` and `AAPL` are always the same key.

```python
# backend/app/market/tickers.py
"""Ticker symbol validation and normalisation."""

from __future__ import annotations

import re

# AAPL, BRK.B, BF-B. Starts with a letter, at most 10 chars.
TICKER_RE = re.compile(r"[A-Z][A-Z0-9.\-]{0,9}")

DEFAULT_TICKERS: tuple[str, ...] = (
    "AAPL", "GOOGL", "MSFT", "AMZN", "TSLA", "NVDA", "META", "JPM", "V", "NFLX",
)


class InvalidTickerError(ValueError):
    """Raised for a malformed ticker symbol. API routes map this to HTTP 400."""


def normalize_ticker(raw: str) -> str:
    """Strip + uppercase, then validate. ' aapl ' -> 'AAPL'."""
    ticker = (raw or "").strip().upper()
    if not TICKER_RE.fullmatch(ticker):
        raise InvalidTickerError(f"Invalid ticker symbol: {raw!r}")
    return ticker
```

| Input | Result |
|---|---|
| `" aapl "` | `AAPL` |
| `"BRK.B"`, `"bf-b"` | `BRK.B`, `BF-B` |
| `""`, `"   "`, `None` | `InvalidTickerError` |
| `"TOOLONGTICKER"` (13 chars) | `InvalidTickerError` |
| `"$$$"`, `"1ABC"` | `InvalidTickerError` |

One FastAPI exception handler turns `InvalidTickerError` into `400 {"error": "Invalid ticker symbol: '$$$'"}` for every route and LLM action (§13.3). In simulator mode, any well-formed symbol gets a price, while in Massive mode an unknown one simply never gets one (§9.5). Checking whether a symbol actually exists is a non-goal.

---

## 7. Unified Interface: `interface.py`

```python
# backend/app/market/interface.py
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
```

### 7.1 Behavioural contract per implementation

| Behaviour | `SimulatorDataSource` | `MassiveDataSource` |
|---|---|---|
| `start()` fills the cache before it returns | ✓ seed/initial prices | ✓ first poll is awaited (may be partial on errors) |
| A newly added ticker gets a price | **immediately**, inside `add_ticker` | live: on an immediate extra poll (~1 s). EOD: immediately, from data already in memory. |
| `remove_ticker()` evicts from the cache | ✓ | ✓ |
| Tickers are normalised | ✓ | ✓ |
| `initial_prices` | used for tickers it can't price for real | ignored |
| Update cadence | 500 ms | live: `MASSIVE_POLL_INTERVAL` (15 s). EOD: 30 min. |
| Errors | logged, loop continues | logged, last prices kept |

### 7.2 Lifecycle

```python
cache = PriceCache()
source = create_market_data_source(cache)             # unstarted
await source.start(["AAPL", "MSFT"], initial_prices={"PYPL": 71.2})
await source.add_ticker("tsla")                       # → "TSLA"
await source.remove_ticker("MSFT")                    # also evicted from the cache
source.get_tickers()                                  # ['AAPL', 'TSLA']
await source.stop()                                   # idempotent
```

---

## 8. Simulator: `seed_prices.py` + `simulator.py`

The full rationale for the maths is in `MARKET_SIMULATOR.md`. This section has the implementable summary and the code.

### 8.1 Model

Every 500 ms, each ticker's price takes one correlated Geometric Brownian Motion step:

```
S(t+Δt) = S(t) · exp( (μ − σ²/2)·Δt + σ·√Δt·Z ),     Z = L·z,  z ~ N(0, I),  C = L·Lᵀ
```

- `Δt = update_interval / (252 · 6.5 · 3600)` ≈ 8.48e-8 for 500 ms. Δt is in **trading-year** units, so the volatility you see on screen matches the annual σ, and `dt` is **derived from `update_interval`** so the two can't drift apart.
- `C` is the sector correlation matrix (tech 0.6, finance 0.5, everything else and TSLA 0.3). It's always positive definite. `L` is recomputed only when the ticker set changes.
- **Events:** each ticker independently has an `event_probability` chance per tick of a ±2–5% jump. At 0.001 with 10 tickers, that's about one jump every 50 s. Set `SIM_EVENT_PROBABILITY=0.0002` for calmer prices (see `MARKET_SIMULATOR.md` §2.4).
- **Randomness comes only from an injected `np.random.Generator`.** `SIM_SEED=42` makes a run reproducible for tests and E2E.

### 8.2 Parameters: `seed_prices.py` (unchanged)

```python
SEED_PRICES = {"AAPL": 190.00, "GOOGL": 175.00, "MSFT": 420.00, "AMZN": 185.00, "TSLA": 250.00,
               "NVDA": 800.00, "META": 500.00, "JPM": 195.00, "V": 280.00, "NFLX": 600.00}

TICKER_PARAMS = {  # annualised volatility (sigma) and drift (mu)
    "AAPL": {"sigma": 0.22, "mu": 0.05}, "GOOGL": {"sigma": 0.25, "mu": 0.05},
    "MSFT": {"sigma": 0.20, "mu": 0.05}, "AMZN":  {"sigma": 0.28, "mu": 0.05},
    "TSLA": {"sigma": 0.50, "mu": 0.03}, "NVDA":  {"sigma": 0.40, "mu": 0.08},
    "META": {"sigma": 0.30, "mu": 0.05}, "JPM":   {"sigma": 0.18, "mu": 0.04},
    "V":    {"sigma": 0.17, "mu": 0.04}, "NFLX":  {"sigma": 0.35, "mu": 0.05},
}
DEFAULT_PARAMS = {"sigma": 0.25, "mu": 0.05}           # any other ticker

CORRELATION_GROUPS = {
    "tech": {"AAPL", "GOOGL", "MSFT", "AMZN", "META", "NVDA", "NFLX"},
    "finance": {"JPM", "V"},
}
INTRA_TECH_CORR, INTRA_FINANCE_CORR, CROSS_GROUP_CORR, TSLA_CORR = 0.6, 0.5, 0.3, 0.3
```

A ticker that isn't in `SEED_PRICES` starts at `uniform(50, 300)` (or at its `initial_prices` hint) and uses `DEFAULT_PARAMS`.

### 8.3 `simulator.py`

```python
# backend/app/market/simulator.py
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
        if ticker in self._prices:
            return
        self._add_ticker_internal(ticker, initial_price)
        self._rebuild_cholesky()

    def remove_ticker(self, ticker: str) -> None:
        if ticker not in self._prices:
            return
        self._tickers.remove(ticker)
        del self._prices[ticker], self._start_prices[ticker], self._params[ticker]
        self._rebuild_cholesky()

    def get_price(self, ticker: str) -> float | None:
        price = self._prices.get(ticker)
        return round(price, 2) if price is not None else None

    def get_start_price(self, ticker: str) -> float | None:
        return self._start_prices.get(ticker)

    def get_tickers(self) -> list[str]:
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
```

### 8.4 Design notes

- **Pure core, async shell.** `GBMSimulator` has no asyncio, no cache and no I/O, so tests can step it thousands of times in milliseconds. `SimulatorDataSource` only handles scheduling and cache writes.
- **No lock in the simulator.** `add_ticker`, `remove_ticker` and `_run_loop` all run on the event loop, and `step()` has no `await`, so they can't interleave.
- **Full precision inside, cents outside.** `_prices` holds floats. Only the returned/cached value is rounded, so rounding error doesn't pile up.
- **Start price = reference price.** `_seed_cache` writes the start price with `reference_price=start`, which makes "daily change %" mean "since the server started". That's a stand-in for the session open.
- **`initial_prices` keeps P&L continuous across restarts.** Without it, a restart resets NVDA to $800 (or PYPL to a *new* random price), and unrealised P&L jumps. The lifespan passes each held ticker's last fill price (§13.1). `initial_prices` is only a hint: it overrides the seed price, and nothing else.
- **Vectorise later, if needed.** The per-ticker Python loop costs a few microseconds per ticker. For hundreds of tickers, hold `prices`, `mu` and `sigma` as arrays and do `prices *= np.exp(drift + diff * z)`.

### 8.5 Behaviour guarantees (each one is a test in §14)

1. Prices are always `> 0` (`exp` of anything is positive, and the worst shock is −5%).
2. `start()` returns with every starting ticker cached at its start price, with `reference_price` equal to the start price.
3. `add_ticker()` caches a price before it returns. A second `add_ticker()` for the same ticker is a no-op and doesn't reset its price.
4. After `remove_ticker(t)`, `t` is gone from both `step()` output and the cache.
5. The same `seed` gives an identical price path.
6. With `event_probability=0`, the sample correlation of log returns is ≈ 0.6 for AAPL/MSFT and ≈ 0.3 for AAPL/TSLA.
7. With `event_probability=1`, every tick moves the price by at least about 2%.
8. After `stop()`, the cache version stops changing. `stop()` is idempotent.

---

## 9. Massive API: `massive_client.py`

The API research is in `MASSIVE_API.md`. This section turns it into a data source.

### 9.1 Endpoints used

| Purpose | SDK call | REST | Plans |
|---|---|---|---|
| Live prices, many tickers, **one call** | `client.get_snapshot_all(SnapshotMarketType.STOCKS, tickers=[...])` | `GET /v2/snapshot/locale/us/markets/stocks/tickers?tickers=AAPL,MSFT` | Starter+ (15-min delayed); Advanced+ (real-time) |
| End-of-day close for **all** tickers, one call | `client.get_grouped_daily_aggs(date="2026-10-07", adjusted=True)` | `GET /v2/aggs/grouped/locale/us/market/stocks/2026-10-07?adjusted=true` | All plans, including free |

### 9.2 Two modes, auto-detected

```
start() ──► live mode: get_snapshot_all
              │  BadResponse containing "NOT_AUTHORIZED" (free key → 403)
              ▼
            eod mode: get_grouped_daily_aggs for the last two trading days, every 30 min
```

| Mode | Interval | Price | `reference_price` | Timestamp |
|---|---|---|---|---|
| `live` | `MASSIVE_POLL_INTERVAL` (15 s) | `last_trade.price` → `min.close` → `day.close` → `prev_day.close` (first positive value) | `prev_day.close` | `last_trade.sip_timestamp` (**ns**) → `updated` (**ns**) → `min.timestamp` (ms) |
| `eod` | 1800 s | latest bar `close` | previous trading day `close`, else the bar's `open` | bar `timestamp` (ms) |

The switch happens once and is one-way. If the user upgrades their plan, a restart picks live mode back up.

### 9.3 `massive_client.py`

```python
# backend/app/market/massive_client.py
"""Massive (formerly Polygon.io) REST poller for real market data."""

from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime, timedelta
from typing import Literal
from zoneinfo import ZoneInfo

from massive import RESTClient
from massive.exceptions import BadResponse
from massive.rest.models import GroupedDailyAgg, SnapshotMarketType, TickerSnapshot

from .cache import PriceCache
from .interface import MarketDataSource
from .tickers import normalize_ticker

logger = logging.getLogger(__name__)

NEW_YORK = ZoneInfo("America/New_York")
SNAPSHOT_CHUNK = 100  # tickers per snapshot call; keeps the URL a sane length
EOD_LOOKBACK_DAYS = 7  # how far back to search for two trading days


def to_epoch_seconds(ts: int | float | None) -> float | None:
    """Normalise a Massive timestamp (s, ms, µs or ns) to float seconds by magnitude."""
    if not ts:
        return None
    ts = float(ts)
    if ts > 1e17:  # nanoseconds (~1.7e18 today)
        return ts / 1e9
    if ts > 1e14:  # microseconds
        return ts / 1e6
    if ts > 1e11:  # milliseconds (~1.7e12)
        return ts / 1e3
    return ts


def _first_positive(*values: float | None) -> float | None:
    """First value that is a positive number. Pre-market bars are often 0."""
    for v in values:
        if v is not None and v > 0:
            return float(v)
    return None


def parse_snapshot(snap: TickerSnapshot) -> tuple[float, float | None, float | None] | None:
    """Extract (price, timestamp_s, reference_price) from one snapshot, or None."""
    last_trade, minute, day, prev_day = snap.last_trade, snap.min, snap.day, snap.prev_day
    price = _first_positive(
        last_trade.price if last_trade else None,
        minute.close if minute else None,
        day.close if day else None,
        prev_day.close if prev_day else None,
    )
    if price is None:
        return None
    timestamp = to_epoch_seconds(
        (last_trade.sip_timestamp if last_trade else None)
        or snap.updated
        or (minute.timestamp if minute else None)
    )
    reference = _first_positive(prev_day.close if prev_day else None)
    return price, timestamp, reference


class MassiveDataSource(MarketDataSource):
    """Polls Massive for every tracked ticker and writes into the PriceCache.

    Modes (auto-detected):
      live — get_snapshot_all every `poll_interval` s (paid plans; real-time or 15-min delayed)
      eod  — get_grouped_daily_aggs every EOD_INTERVAL s (free plan; snapshots return 403)
    """

    EOD_INTERVAL = 1800.0

    def __init__(self, api_key: str, price_cache: PriceCache, poll_interval: float = 15.0) -> None:
        self._api_key = api_key
        self._cache = price_cache
        self._interval = poll_interval
        self._tickers: list[str] = []
        self._client: RESTClient | None = None
        self._task: asyncio.Task | None = None
        self._wake = asyncio.Event()  # set by add_ticker → poll now instead of waiting
        self._mode: Literal["live", "eod"] = "live"
        # Last grouped-daily result, so add_ticker can price instantly in EOD mode
        self._eod_latest: dict[str, GroupedDailyAgg] = {}
        self._eod_previous: dict[str, GroupedDailyAgg] = {}

    @property
    def mode(self) -> str:
        return self._mode

    # --- MarketDataSource ---

    async def start(
        self, tickers: list[str], initial_prices: dict[str, float] | None = None
    ) -> None:
        self._client = RESTClient(api_key=self._api_key)
        self._tickers = list(dict.fromkeys(normalize_ticker(t) for t in tickers))
        await self._poll_once()  # warm the cache before start() returns
        self._task = asyncio.create_task(self._poll_loop(), name="massive-poller")
        logger.info(
            "Massive poller started: %d tickers, mode=%s, interval=%.0fs",
            len(self._tickers), self._mode, self._current_interval(),
        )

    async def stop(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._task = None
        self._client = None
        logger.info("Massive poller stopped")

    async def add_ticker(self, ticker: str) -> None:
        ticker = normalize_ticker(ticker)
        if ticker in self._tickers:
            return
        self._tickers.append(ticker)
        if self._mode == "eod":
            self._apply_eod(ticker)  # data already in memory; the next fetch is 30 min away
        else:
            self._wake.set()  # live: poll right away (paid plans have no per-minute cap)
        logger.info("Massive: added %s", ticker)

    async def remove_ticker(self, ticker: str) -> None:
        ticker = normalize_ticker(ticker)
        self._tickers = [t for t in self._tickers if t != ticker]
        self._cache.remove(ticker)
        logger.info("Massive: removed %s", ticker)

    def get_tickers(self) -> list[str]:
        return list(self._tickers)

    # --- Polling ---

    def _current_interval(self) -> float:
        return self._interval if self._mode == "live" else self.EOD_INTERVAL

    async def _poll_loop(self) -> None:
        while True:
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=self._current_interval())
            except TimeoutError:
                pass
            self._wake.clear()
            await self._poll_once()

    async def _poll_once(self) -> None:
        """One cycle. Never raises: errors are logged and the last prices stay cached."""
        if not self._tickers or self._client is None:
            return
        tickers = list(self._tickers)  # copy: the worker thread must not see mutations
        try:
            if self._mode == "live":
                await self._poll_snapshots(tickers)
            else:
                await self._poll_eod(tickers)
        except BadResponse as e:
            if self._mode == "live" and "NOT_AUTHORIZED" in str(e):
                logger.warning("Massive plan has no snapshot access; switching to end-of-day prices")
                self._mode = "eod"
                await self._safe_poll_eod(tickers)
            else:
                logger.error("Massive poll failed: %s", e)
        except Exception as e:  # network errors, timeouts, unexpected payloads
            logger.error("Massive poll failed: %s", e)

    async def _poll_snapshots(self, tickers: list[str]) -> None:
        snapshots = await asyncio.to_thread(self._fetch_snapshots, tickers)
        updated = 0
        for snap in snapshots:
            parsed = parse_snapshot(snap)
            if snap.ticker not in self._tickers or parsed is None:
                continue  # removed mid-poll, or no usable price
            price, timestamp, reference = parsed
            self._cache.update(snap.ticker, price, timestamp=timestamp, reference_price=reference)
            updated += 1
        logger.debug("Massive snapshot: %d/%d tickers priced", updated, len(tickers))

    def _fetch_snapshots(self, tickers: list[str]) -> list[TickerSnapshot]:
        """Blocking SDK call(s). Runs in a worker thread."""
        assert self._client is not None
        out: list[TickerSnapshot] = []
        for i in range(0, len(tickers), SNAPSHOT_CHUNK):
            out.extend(
                self._client.get_snapshot_all(
                    market_type=SnapshotMarketType.STOCKS,
                    tickers=tickers[i : i + SNAPSHOT_CHUNK],
                )
            )
        return out

    async def _safe_poll_eod(self, tickers: list[str]) -> None:
        try:
            await self._poll_eod(tickers)
        except Exception as e:
            logger.error("Massive end-of-day poll failed: %s", e)

    async def _poll_eod(self, tickers: list[str]) -> None:
        self._eod_latest, self._eod_previous = await asyncio.to_thread(self._fetch_last_two_days)
        for ticker in tickers:
            if ticker in self._tickers:
                self._apply_eod(ticker)

    def _apply_eod(self, ticker: str) -> None:
        bar = self._eod_latest.get(ticker)
        if bar is None or not bar.close:
            return  # unknown symbol: never priced, UI shows "—"
        prev = self._eod_previous.get(ticker)
        reference = _first_positive(prev.close if prev else None, bar.open)
        self._cache.update(
            ticker, bar.close, timestamp=to_epoch_seconds(bar.timestamp), reference_price=reference
        )

    def _fetch_last_two_days(self) -> tuple[dict[str, GroupedDailyAgg], dict[str, GroupedDailyAgg]]:
        """Walk back from today (New York) to the two most recent days with data."""
        assert self._client is not None
        found: list[dict[str, GroupedDailyAgg]] = []
        day: date = datetime.now(NEW_YORK).date()
        for _ in range(EOD_LOOKBACK_DAYS):
            if day.weekday() < 5:  # skip weekends without spending a request
                bars = self._client.get_grouped_daily_aggs(date=day.isoformat(), adjusted=True)
                if bars:
                    found.append({b.ticker: b for b in bars if b.ticker})
                    if len(found) == 2:
                        break
            day -= timedelta(days=1)
        found += [{}, {}]
        return found[0], found[1]
```

### 9.4 Design notes

- **The real SDK models are dataclasses, not dicts.** `TickerSnapshot.last_trade` is a `LastTrade` with `.price` and **`.sip_timestamp` (nanoseconds)**. There's **no `.timestamp`** attribute. The current code reads `last_trade.timestamp / 1000`, which raises `AttributeError` on every real snapshot. The old tests didn't catch it because `MagicMock` accepts any attribute. `to_epoch_seconds()` converts by magnitude, so units can't be mixed up again.
- **Fallback chain.** Snapshot data is cleared overnight and fills up again from about 4 AM ET. Before then, `last_trade` may be missing and `day` is all zeros, so `_first_positive` skips `None` and `0`.
- **One call per cycle.** Every tracked ticker goes into a single snapshot request (split into chunks of 100 to keep the URL short). Grouped daily covers the whole market in a single call.
- **The worker thread gets a copy** of the ticker list. Results for a ticker removed while a poll was in flight are dropped (`snap.ticker not in self._tickers`), so a removal can't be undone by a late poll.
- **`add_ticker` in live mode sets `_wake`**, so the loop polls immediately instead of up to 15 s later. Paid plans have no per-minute cap, so the extra call is free. This lets `ensure_price()` (§11) succeed within its 3 s timeout on a paid plan.
- **`add_ticker` in EOD mode** prices straight from the grouped-daily data already in memory. That data covers the whole market, so there's no need to wait 30 min.
- **Free-tier budget (5 req/min).** At startup: 1 snapshot call (403), then 2–4 grouped-daily calls (weekends are skipped without a request, and a holiday costs one extra). After that, 2–4 calls every 30 min. If the limit is ever hit, the SDK retries 429 responses with backoff.

### 9.5 Error handling

| Situation | What happens |
|---|---|
| 403 `NOT_AUTHORIZED` in live mode | Switch to `eod` and poll grouped daily straight away |
| 401 invalid key, other `BadResponse` | Logged at ERROR. The last prices stay cached. Retried next interval. |
| 429 / 5xx | The SDK retries internally with backoff. If it still fails, it's handled as above. |
| Network error / timeout | Logged. Retried next interval. |
| Unknown symbol | Missing from the response, so it never gets a cache entry and the UI shows "—" |
| Weekend / holiday / before the data is published (EOD) | Empty results; walk back one day (up to 7) |
| `MASSIVE_API_KEY` set but blank | The factory treats it as unset and uses the simulator. The SDK's `AuthError` can't happen. |

---

## 10. Factory and Configuration: `factory.py`

```python
# backend/app/market/factory.py
"""Pick the market data source from the environment."""

from __future__ import annotations

import logging
import os

from .cache import PriceCache
from .interface import MarketDataSource
from .massive_client import MassiveDataSource
from .simulator import SimulatorDataSource

logger = logging.getLogger(__name__)


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name, "").strip()
    try:
        return float(raw) if raw else default
    except ValueError:
        logger.warning("Ignoring invalid %s=%r; using %s", name, raw, default)
        return default


def create_market_data_source(price_cache: PriceCache) -> MarketDataSource:
    """MASSIVE_API_KEY non-empty → Massive; otherwise the GBM simulator. Returns it unstarted."""
    api_key = os.environ.get("MASSIVE_API_KEY", "").strip()
    if api_key:
        interval = max(_env_float("MASSIVE_POLL_INTERVAL", 15.0), 1.0)
        logger.info("Market data source: Massive API (poll every %.0fs)", interval)
        return MassiveDataSource(api_key=api_key, price_cache=price_cache, poll_interval=interval)

    seed_raw = os.environ.get("SIM_SEED", "").strip()
    seed = int(seed_raw) if seed_raw.isdigit() else None
    logger.info("Market data source: GBM simulator (seed=%s)", seed)
    return SimulatorDataSource(
        price_cache=price_cache,
        event_probability=_env_float("SIM_EVENT_PROBABILITY", 0.001),
        seed=seed,
    )
```

| Env var | Default | Effect |
|---|---|---|
| `MASSIVE_API_KEY` | empty | Non-empty (after strip) → Massive. Otherwise → simulator. |
| `MASSIVE_POLL_INTERVAL` | `15` | Live-mode poll seconds (minimum 1). Use 15 on Starter, 2–5 on unlimited plans. |
| `SIM_EVENT_PROBABILITY` | `0.001` | Per-ticker, per-tick chance of a jump. Use `0.0002` for calmer prices. |
| `SIM_SEED` | unset | An integer makes the simulator deterministic (E2E tests, demos). |

Add these to `.env.example` with comments. A bad value is logged and replaced with the default, and never stops startup.

---

## 11. Tracked-Ticker Service: `service.py`

This settles PLAN §13.1 #1 and #2. It's the only code that changes the tracked set.

```python
# backend/app/market/service.py
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
```

### 11.1 Scenarios

| Action | Call | Result |
|---|---|---|
| Startup | `start(watchlist={10 defaults}, held={AAPL, PYPL}, initial_prices={...})` | Tracks 11 tickers. PYPL starts at its last fill price. |
| User removes AAPL from the watchlist but still holds it | `sync(watchlist - {AAPL}, held={AAPL, PYPL})` | AAPL stays tracked and priced, and stays in the SSE payload. |
| User sells all their AAPL | `sync(watchlist, held={PYPL})` | AAPL is no longer wanted → `remove_ticker` → evicted, and it drops out of SSE. |
| User adds `pypl` to the watchlist | `sync(...)` | No-op: PYPL was already held and tracked. |
| User buys untracked `SHOP` | `ensure_price("SHOP")` → price; trade; `sync(...)` | SHOP is tracked (it's held now). |
| LLM buys `ZZZZ` in Massive mode | `ensure_price("ZZZZ")` → `None` after 3 s | The trade fails with "price not yet available"; the next `sync` untracks ZZZZ. |

The frontend should show the watchlist from `GET /api/watchlist`, and **not** from whatever keys happen to be in the SSE payload. That payload can include held tickers that aren't on the watchlist. The positions table and heatmap use the SSE prices for those tickers.

---

## 12. SSE Streaming: `stream.py`

### 12.1 Code

```python
# backend/app/market/stream.py
"""SSE endpoint: GET /api/stream/prices."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncGenerator

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from .cache import PriceCache

logger = logging.getLogger(__name__)

STREAM_INTERVAL = 0.5  # seconds between cache checks
HEARTBEAT_EVERY = 15.0  # seconds; SSE comment so proxies don't drop an idle stream


def create_stream_router(price_cache: PriceCache, interval: float = STREAM_INTERVAL) -> APIRouter:
    """Build a fresh router per call (safe to call more than once, e.g. in tests)."""
    router = APIRouter(prefix="/api/stream", tags=["streaming"])

    @router.get("/prices")
    async def stream_prices(request: Request) -> StreamingResponse:
        return StreamingResponse(
            generate_price_events(price_cache, request, interval),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    return router


def format_prices_event(price_cache: PriceCache) -> str:
    data = {ticker: update.to_dict() for ticker, update in price_cache.get_all().items()}
    return f"data: {json.dumps(data)}\n\n"


async def generate_price_events(
    price_cache: PriceCache, request: Request, interval: float = STREAM_INTERVAL
) -> AsyncGenerator[str, None]:
    """Yield one event with ALL tickers whenever the cache version changes."""
    yield "retry: 1000\n\n"  # browser reconnects 1 s after a drop
    last_version = -1
    idle = 0.0
    client = request.client.host if request.client else "unknown"
    logger.info("SSE client connected: %s", client)
    try:
        while not await request.is_disconnected():
            version = price_cache.version
            if version != last_version:
                last_version = version
                idle = 0.0
                yield format_prices_event(price_cache)  # may be "{}" after the last removal
            elif idle >= HEARTBEAT_EVERY:
                idle = 0.0
                yield ": ping\n\n"
            await asyncio.sleep(interval)
            idle += interval
    except asyncio.CancelledError:
        pass
    logger.info("SSE client disconnected: %s", client)
```

Changes from the current code: the router is built **inside** the factory (the module-level router used to register the route twice if the factory was called twice), an empty `{}` payload is sent after the last ticker is removed, a heartbeat comment is sent every 15 s, and the event generator is public so tests can drive it without a server.

### 12.2 Wire contract: `GET /api/stream/prices`

- `Content-Type: text/event-stream`. The first frame is `retry: 1000` (the browser reconnects 1 s after a drop).
- Every 500 ms, **if anything changed**, the server sends one unnamed event (handled by `onmessage`) holding **every tracked ticker**:

```
retry: 1000

data: {"AAPL": {"ticker": "AAPL", "price": 189.99, "previous_price": 190.0, "timestamp": 1791480744.76, "change": -0.01, "change_percent": -0.0053, "direction": "down", "reference_price": 190.0, "day_change": -0.01, "day_change_percent": -0.0053}, "AMZN": {...}, "GOOGL": {...}}

: ping

```

(That's real output from the smoke test.)

- A new connection always gets the full current snapshot first, so a reconnect needs no special handling.
- A ticker that drops out of the payload has been untracked, and the client should drop it.
- `: ping` comments keep idle connections alive (Massive EOD mode can be silent for 30 min). `EventSource` ignores them.
- Silence is **not** a disconnect. Connection status comes from `EventSource` events (below).

### 12.3 Frontend client (reference for the Frontend agent)

```ts
// lib/prices.ts
export type Direction = "up" | "down" | "flat";

export interface PriceUpdate {
  ticker: string;
  price: number;
  previous_price: number;
  timestamp: number;            // Unix seconds
  change: number;
  change_percent: number;
  direction: Direction;
  reference_price: number | null;
  day_change: number | null;
  day_change_percent: number | null;
}

export type ConnectionStatus = "connected" | "reconnecting" | "disconnected";

export function subscribePrices(
  onPrices: (prices: Record<string, PriceUpdate>) => void,
  onStatus: (s: ConnectionStatus) => void,
): () => void {
  const es = new EventSource("/api/stream/prices");
  es.onopen = () => onStatus("connected");
  es.onmessage = (e) => onPrices(JSON.parse(e.data));
  es.onerror = () =>
    onStatus(es.readyState === EventSource.CLOSED ? "disconnected" : "reconnecting");
  return () => es.close();
}
```

```ts
// In the store: append to sparkline history only when the price changes
function applyPrices(prices: Record<string, PriceUpdate>) {
  for (const [t, u] of Object.entries(prices)) {
    const hist = history[t] ?? (history[t] = []);
    if (hist.length === 0 || hist[hist.length - 1].price !== u.price) {
      hist.push({ time: u.timestamp, price: u.price });
      if (hist.length > 600) hist.shift();     // ~5 min at 500 ms
    }
    if (u.direction !== "flat") flash(t, u.direction);  // CSS class for ~500 ms
  }
  latest = prices;                              // tickers missing here were untracked
}
```

---

## 13. FastAPI Integration and Consumer Examples

These are sketches for the Backend agent. `app.db` names stand in for the real database layer, which doesn't exist yet.

### 13.1 `app/main.py`: lifespan

```python
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app import db
from app.market import (
    MarketDataService, PriceCache, create_market_data_source, create_stream_router,
)

price_cache = PriceCache()
market = MarketDataService(create_market_data_source(price_cache), price_cache)


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()                                           # lazy schema + seed
    await market.start(
        watchlist=set(db.watchlist_tickers()),
        held=set(db.held_tickers()),
        initial_prices=db.last_fill_prices(),              # {ticker: last trade price}
    )
    app.state.market = market
    app.state.prices = price_cache
    yield
    await market.stop()


app = FastAPI(lifespan=lifespan)
app.include_router(create_stream_router(price_cache))      # GET /api/stream/prices
# app.include_router(portfolio_router); app.include_router(watchlist_router); ...
app.mount("/", StaticFiles(directory="static", html=True), name="static")   # LAST
```

`db.last_fill_prices()` is `SELECT ticker, price FROM trades WHERE id IN (latest per ticker)`, limited to tickers that are currently held.

### 13.2 Dependency helpers

```python
# app/deps.py
from fastapi import Request
from app.market import MarketDataService, PriceCache

def get_market(request: Request) -> MarketDataService:
    return request.app.state.market

def get_prices(request: Request) -> PriceCache:
    return request.app.state.prices
```

### 13.3 Route usage

```python
# app/main.py: one handler turns a bad symbol into 400 everywhere (routes and LLM actions)
from fastapi import Request
from fastapi.responses import JSONResponse
from app.market import InvalidTickerError

@app.exception_handler(InvalidTickerError)
async def invalid_ticker(_: Request, exc: InvalidTickerError) -> JSONResponse:
    return JSONResponse(status_code=400, content={"error": str(exc)})
```

```python
from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from app.market import MarketDataService, PriceCache, normalize_ticker

router = APIRouter(prefix="/api")

class TickerIn(BaseModel):
    ticker: str

class TradeIn(BaseModel):
    ticker: str
    side: str          # "buy" | "sell"
    quantity: float


# --- Watchlist ---------------------------------------------------------------
@router.get("/watchlist")
async def list_watchlist(prices: PriceCache = Depends(get_prices)):
    out = []
    for t in db.watchlist_tickers():
        u = prices.get(t)
        out.append(u.to_dict() if u else {"ticker": t, "price": None})   # "—" in the UI
    return out

@router.post("/watchlist")
async def add_watchlist(body: TickerIn, market: MarketDataService = Depends(get_market)):
    ticker = normalize_ticker(body.ticker)                   # InvalidTickerError → 400
    db.add_watchlist(ticker)                                 # idempotent (UNIQUE)
    await market.sync(set(db.watchlist_tickers()), set(db.held_tickers()))
    return {"ticker": ticker}

@router.delete("/watchlist/{ticker}")
async def remove_watchlist(ticker: str, market: MarketDataService = Depends(get_market)):
    ticker = normalize_ticker(ticker)
    db.remove_watchlist(ticker)
    await market.sync(set(db.watchlist_tickers()), set(db.held_tickers()))  # held → stays priced
    return {"ticker": ticker}


# --- Trade -------------------------------------------------------------------
@router.post("/portfolio/trade")
async def trade(body: TradeIn, market: MarketDataService = Depends(get_market)):
    ticker = normalize_ticker(body.ticker)
    price = await market.ensure_price(ticker)
    if price is None:
        return JSONResponse(
            status_code=409, content={"error": f"Price for {ticker} not yet available, retry shortly"}
        )
    result = db.execute_trade(ticker, body.side, body.quantity, price)   # one transaction
    await market.sync(set(db.watchlist_tickers()), set(db.held_tickers()))
    db.record_snapshot(total_value(market.cache))
    return result


# --- Valuation (portfolio, snapshots, LLM context) ----------------------------
def mark_price(pos, prices: PriceCache) -> float:
    return prices.get_price(pos.ticker) or pos.avg_cost      # PLAN §13.1 #14

def total_value(prices: PriceCache) -> float:
    return db.cash() + sum(p.quantity * mark_price(p, prices) for p in db.positions())
```

The LLM chat handler runs its `trades` and `watchlist_changes` through **the same** functions, so it gets the same validation and `sync`. Its portfolio context is built from `prices.get_all()`.

---

## 14. Testing

Run with `uv run --extra dev pytest -v`. `asyncio_mode = "auto"` is already set in `pyproject.toml`.

| File | Covers |
|---|---|
| `test_models.py` (existing, extend) | `day_change*` with and without a reference, `to_dict()` keys |
| `test_cache.py` (existing, extend) | sticky reference, `remove()` bumps the version only when something was removed |
| `test_tickers.py` (new) | the §6 table |
| `test_simulator.py` / `test_simulator_source.py` (existing, extend) | §8.5 guarantees, determinism, `initial_prices`, normalisation |
| `test_massive.py` (**rewrite**) | real SDK models, fallback chain, ns timestamps, 403 → EOD, EOD `add_ticker`, error resilience |
| `test_factory.py` (extend) | blank key, poll interval, SIM env vars |
| `test_service.py` (new) | held tickers survive watchlist removal; `ensure_price` |
| `test_stream.py` (new) | event format, `{}` after the last removal, disconnect, router factory idempotence |

### 14.1 Massive tests: build real models, never `MagicMock` snapshots

```python
from unittest.mock import MagicMock

from massive.exceptions import BadResponse
from massive.rest.models import GroupedDailyAgg, TickerSnapshot

from app.market.cache import PriceCache
from app.market.massive_client import MassiveDataSource, parse_snapshot, to_epoch_seconds

AAPL_JSON = {   # the sample from MASSIVE_API.md §4.1
    "ticker": "AAPL",
    "updated": 1605195918306274000,
    "day": {"o": 119.62, "h": 120.53, "l": 118.81, "c": 120.4229, "v": 28727868},
    "prevDay": {"o": 117.19, "h": 119.63, "l": 116.44, "c": 119.49, "v": 110597265},
    "min": {"c": 120.4201, "t": 1684428720000},
    "lastTrade": {"p": 120.47, "s": 236, "x": 10, "t": 1605195918306274000},
}


def make_source(tickers, client):
    cache = PriceCache()
    src = MassiveDataSource(api_key="test", price_cache=cache, poll_interval=60)
    src._tickers = list(tickers)
    src._client = client          # the SDK client is mocked; its RESULTS are real models
    return src, cache


def test_parse_snapshot_uses_last_trade_and_ns_timestamp():
    price, ts, ref = parse_snapshot(TickerSnapshot.from_dict(AAPL_JSON))
    assert price == 120.47
    assert abs(ts - 1605195918.306274) < 1e-3       # ns → s, not ms
    assert ref == 119.49


def test_parse_snapshot_falls_back_when_no_trade():
    data = {k: v for k, v in AAPL_JSON.items() if k != "lastTrade"}
    data["day"] = {"o": 0, "c": 0}                  # pre-market: zeroed day bar
    price, _, _ = parse_snapshot(TickerSnapshot.from_dict(data))
    assert price == 120.4201                        # min.close


def test_to_epoch_seconds_units():
    assert to_epoch_seconds(1_700_000_000) == 1_700_000_000
    assert to_epoch_seconds(1_700_000_000_000) == 1_700_000_000
    assert to_epoch_seconds(1_700_000_000_000_000_000) == 1_700_000_000
    assert to_epoch_seconds(None) is None


async def test_live_poll_updates_cache():
    client = MagicMock()
    client.get_snapshot_all.return_value = [TickerSnapshot.from_dict(AAPL_JSON)]
    src, cache = make_source(["AAPL", "ZZZZ"], client)
    await src._poll_once()
    upd = cache.get("AAPL")
    assert upd.price == 120.47 and upd.reference_price == 119.49
    assert round(upd.day_change_percent, 2) == 0.82
    assert "ZZZZ" not in cache                      # unknown symbol: never priced


async def test_403_switches_to_eod_mode():
    client = MagicMock()
    client.get_snapshot_all.side_effect = BadResponse(
        '{"status":"NOT_AUTHORIZED","message":"You are not entitled to this data."}'
    )
    client.get_grouped_daily_aggs.side_effect = [
        [GroupedDailyAgg.from_dict({"T": "AAPL", "o": 230, "c": 231.4, "t": 1759708800000})],
        [GroupedDailyAgg.from_dict({"T": "AAPL", "o": 228, "c": 229.0, "t": 1759622400000})],
    ] + [[]] * 10
    src, cache = make_source(["AAPL"], client)
    await src._poll_once()
    assert src.mode == "eod"
    assert cache.get("AAPL").price == 231.4
    assert cache.get("AAPL").reference_price == 229.0


async def test_eod_add_ticker_prices_from_memory():
    src, cache = make_source(["AAPL"], MagicMock())
    src._mode = "eod"
    src._eod_latest = {"MSFT": GroupedDailyAgg.from_dict({"T": "MSFT", "o": 430, "c": 437.1})}
    await src.add_ticker("msft")
    assert cache.get_price("MSFT") == 437.1


async def test_other_errors_keep_last_price():
    client = MagicMock()
    client.get_snapshot_all.return_value = [TickerSnapshot.from_dict(AAPL_JSON)]
    src, cache = make_source(["AAPL"], client)
    await src._poll_once()
    client.get_snapshot_all.side_effect = BadResponse('{"status":"ERROR"}')
    await src._poll_once()                          # must not raise
    assert src.mode == "live"
    assert cache.get_price("AAPL") == 120.47
```

### 14.2 Simulator, cache, tickers and service

```python
import asyncio

import numpy as np
import pytest

from app.market import InvalidTickerError, MarketDataService, PriceCache, normalize_ticker
from app.market.simulator import GBMSimulator, SimulatorDataSource


def test_cache_reference_is_sticky_and_remove_bumps_version():
    c = PriceCache()
    c.update("AAPL", 190.0, reference_price=190.0)
    u = c.update("AAPL", 191.9)
    assert u.reference_price == 190.0 and u.day_change_percent == 1.0
    v = c.version
    c.remove("AAPL")
    assert c.version == v + 1
    c.remove("AAPL")                                # unknown → no bump
    assert c.version == v + 1


@pytest.mark.parametrize("raw,ok", [(" aapl ", "AAPL"), ("BRK.B", "BRK.B"), ("bf-b", "BF-B")])
def test_normalize_ok(raw, ok):
    assert normalize_ticker(raw) == ok


@pytest.mark.parametrize("raw", ["", "   ", "TOOLONGTICKER", "$$$", "1ABC", None])
def test_normalize_bad(raw):
    with pytest.raises(InvalidTickerError):
        normalize_ticker(raw)


def test_seeded_simulator_is_deterministic():
    a = GBMSimulator(["AAPL", "MSFT"], rng=np.random.default_rng(42))
    b = GBMSimulator(["AAPL", "MSFT"], rng=np.random.default_rng(42))
    assert [a.step() for _ in range(50)] == [b.step() for _ in range(50)]


def test_correlation_structure():
    sim = GBMSimulator(["AAPL", "MSFT", "TSLA"], event_probability=0, rng=np.random.default_rng(1))
    prev = {t: sim._prices[t] for t in sim.get_tickers()}
    rets = {t: [] for t in prev}
    for _ in range(20_000):
        sim.step()
        for t in prev:
            rets[t].append(np.log(sim._prices[t] / prev[t]))
            prev[t] = sim._prices[t]
    c = np.corrcoef([rets["AAPL"], rets["MSFT"], rets["TSLA"]])
    assert abs(c[0, 1] - 0.6) < 0.05 and abs(c[0, 2] - 0.3) < 0.05


def test_events_always_move_at_probability_one():
    sim = GBMSimulator(["AAPL"], event_probability=1.0, rng=np.random.default_rng(3))
    p = sim._prices["AAPL"]
    for _ in range(100):
        sim.step()
        q = sim._prices["AAPL"]
        assert abs(q / p - 1) > 0.019
        p = q


async def test_simulator_source_contract():
    cache = PriceCache()
    src = SimulatorDataSource(cache, update_interval=0.05, seed=7)
    await src.start(["aapl", "PYPL"], initial_prices={"pypl": 70.0})
    assert cache.get("AAPL").price == 190.0 and cache.get("AAPL").reference_price == 190.0
    assert cache.get_price("PYPL") == 70.0          # initial_prices hint honoured
    await src.add_ticker("nflx")
    assert cache.get_price("NFLX") == 600.0         # priced before add_ticker returns
    await src.remove_ticker("AAPL")
    assert "AAPL" not in cache and "AAPL" not in src.get_tickers()
    await src.stop()
    v = cache.version
    await asyncio.sleep(0.15)
    assert cache.version == v                       # no writes after stop
    await src.stop()                                # idempotent


async def test_service_keeps_held_tickers():
    cache = PriceCache()
    svc = MarketDataService(SimulatorDataSource(cache, seed=1), cache)
    await svc.start({"AAPL", "MSFT"}, set())
    await svc.sync(watchlist={"MSFT"}, held={"AAPL"})
    assert svc.tracked() == {"AAPL", "MSFT"} and "AAPL" in cache
    await svc.sync(watchlist={"MSFT"}, held=set())
    assert svc.tracked() == {"MSFT"} and "AAPL" not in cache
    assert await svc.ensure_price("pypl") is not None
    await svc.stop()
```

### 14.3 SSE stream without a server

```python
import asyncio
import json

from fastapi import FastAPI

from app.market import PriceCache, create_stream_router
from app.market.stream import generate_price_events


class FakeRequest:
    client = None
    def __init__(self):
        self.disconnected = False
    async def is_disconnected(self):
        return self.disconnected


async def test_stream_events():
    cache = PriceCache()
    cache.update("AAPL", 190.0)
    req = FakeRequest()
    gen = generate_price_events(cache, req, interval=0.01)
    assert await anext(gen) == "retry: 1000\n\n"
    payload = json.loads((await anext(gen))[len("data: "):])
    assert payload["AAPL"]["price"] == 190.0 and payload["AAPL"]["direction"] == "flat"
    cache.remove("AAPL")
    assert json.loads((await anext(gen))[len("data: "):]) == {}
    req.disconnected = True
    await asyncio.sleep(0)
    try:
        await anext(gen)
        raise AssertionError("generator should stop after disconnect")
    except StopAsyncIteration:
        pass


def test_router_factory_can_be_called_twice():
    cache = PriceCache()
    app = FastAPI()
    app.include_router(create_stream_router(cache))
    assert len(create_stream_router(cache).routes) == 1
    assert [r.path for r in app.routes].count("/api/stream/prices") == 1
```

### 14.4 Manual checks

```bash
cd backend
uv run market_data_demo.py                                    # Rich dashboard, simulator
MASSIVE_API_KEY=xxx uv run python -c "
import asyncio, logging; logging.basicConfig(level=logging.INFO)
from app.market import PriceCache, create_market_data_source
async def main():
    c = PriceCache(); s = create_market_data_source(c)
    await s.start(['AAPL','MSFT','NVDA']); print(s.mode, {t: u.to_dict() for t, u in c.get_all().items()})
    await s.stop()
asyncio.run(main())"                                          # real key: live or eod?
curl -N localhost:8000/api/stream/prices                      # against the running app
```

---

## 15. Migration Checklist vs. Current Code

The code on `main` today matches `planning/archive/MARKET_DATA_DESIGN.md`. ⚠️ `MARKET_INTERFACE.md` §11 and `MASSIVE_API.md` §9 mark the Massive fixes as done (✅), but `backend/app/market/massive_client.py` **still reads `snap.last_trade.timestamp / 1000.0`**, and `test_massive.py` still uses `MagicMock` snapshots. Treat those items as **not done**.

| # | File | Change | Section |
|---|---|---|---|
| 1 | `models.py` | Add `reference_price`, `day_change`, `day_change_percent`, and put them in `to_dict()` | §4 |
| 2 | `cache.py` | `reference_price` param (sticky); `remove()` bumps the version; `version` read under the lock; `timestamp is not None` | §5 |
| 3 | `tickers.py` | **New**: `normalize_ticker`, `InvalidTickerError`, `DEFAULT_TICKERS` | §6 |
| 4 | `interface.py` | `start(tickers, initial_prices=None)`; contract docstring | §7 |
| 5 | `simulator.py` | Injected `np.random.Generator` + `seed`; `dt` derived from `update_interval`; `initial_prices`; start price written as `reference_price`; ticker normalisation; `LinAlgError` guard | §8 |
| 6 | `massive_client.py` | `parse_snapshot` (fallback chain, `sip_timestamp` ns, `prev_day.close` reference); live → EOD on 403; `_wake` for immediate polls; chunking; ticker normalisation; drop results for removed tickers | §9 |
| 7 | `factory.py` | Read `MASSIVE_POLL_INTERVAL`, `SIM_EVENT_PROBABILITY`, `SIM_SEED` | §10 |
| 8 | `service.py` | **New**: `MarketDataService` | §11 |
| 9 | `stream.py` | Router built inside the factory; `{}` after the last removal; heartbeat; public `generate_price_events` | §12 |
| 10 | `__init__.py` | Export the new names | §3 |
| 11 | `tests/market/` | Rewrite `test_massive.py`; add tickers/service/stream tests; extend the others | §14 |
| 12 | `backend/CLAUDE.md`, `MARKET_DATA_SUMMARY.md`, `.env.example` | Document the new API and env vars | §3, §10 |

Order: 1 → 2 → 3 → 4 → 5 → 6 → 7 → 8 → 9 → 10, then run the tests after each step. The existing simulator, cache and model tests pass unchanged against the new code. Only `test_massive.py` has to be replaced, because it encodes the timestamp bug.

---

## 16. Failure Modes and Edge Cases

| Case | Behaviour |
|---|---|
| Empty watchlist and no positions at startup | The source starts with `[]`. The simulator steps nothing, and Massive skips polls. SSE sends `{}` once, then heartbeats until a ticker is added. |
| Trade on a ticker with no price | `ensure_price` returns `None`, so the route answers 409 and stores nothing |
| Ticker removed while a Massive poll is in flight | The late result is dropped, so the ticker isn't re-added to the cache |
| Duplicate add / unknown remove | No-op |
| Lowercase or padded input anywhere | Normalised once at the boundary |
| Simulator running for hours | Prices drift (that's how GBM works) and stay positive. Jumps dominate the drift at 0.001, so tune `SIM_EVENT_PROBABILITY`. |
| Container restart (simulator) | Held tickers resume at their last fill price, and the rest go back to their seeds. "Daily change" resets to 0. |
| Container restart (Massive) | Prices come from the first poll, so there's no discontinuity |
| Many SSE clients | Each one reads the shared cache. There's no per-client upstream cost. |
| Slow client | `StreamingResponse` backpressure only affects that client's generator |
| Massive outage | The last prices stay cached and keep being served, and timestamps show how old they are. The frontend could grey out prices older than 2× the interval (optional). |

---

## 17. Non-Goals

- Massive WebSocket streaming (REST polling works on every plan).
- Historical backfill for charts (charts build up from SSE; `MASSIVE_API.md` §4.6 has the endpoint for later).
- Checking that a symbol really exists in simulator mode.
- Market hours, bid/ask spreads, volume, and order book simulation.
- Multi-user fan-out (the cache is already shared, so nothing would need to change).
