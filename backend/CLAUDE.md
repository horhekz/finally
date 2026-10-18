# Backend — Developer Guide

## Project Setup

```bash
cd backend
uv sync --extra dev   # Install all dependencies including test/lint tools
```

## Market Data API

The market data subsystem lives in `app/market/`. The full design (module code, wire format,
failure modes) is `planning/MARKET_DATA_DESIGN.md`. Import **only** from the package:

```python
from app.market import (
    DEFAULT_TICKERS, InvalidTickerError, MarketDataService, MarketDataSource,
    PriceCache, PriceUpdate, create_market_data_source, create_stream_router, normalize_ticker,
)
```

### Core Types

- **`PriceUpdate`** — Immutable dataclass: `ticker`, `price`, `previous_price`, `timestamp` (Unix seconds),
  `reference_price` (simulator: start price; Massive: previous close; may be `None`). Properties:
  `change`, `change_percent`, `direction` ("up"/"down"/"flat", tick-to-tick, drives the flash) and
  `day_change`, `day_change_percent` (vs. `reference_price`, drives "daily change %"; `None` without a
  reference). `to_dict()` **is** the wire format for SSE and REST.

- **`PriceCache`** — Thread-safe in-memory latest-price store:
  - `update(ticker, price, timestamp=None, reference_price=None) -> PriceUpdate` (reference is sticky)
  - `get(ticker) -> PriceUpdate | None`, `get_price(ticker) -> float | None`
  - `get_all() -> dict[str, PriceUpdate]`
  - `remove(ticker)` — bumps `version` only if the ticker was present
  - `version` — monotonic counter bumped on every update **and** removal (SSE change detection)

- **`normalize_ticker(raw) -> str`** — strip + uppercase + validate `^[A-Z][A-Z0-9.\-]{0,9}$`.
  Raises `InvalidTickerError` (a `ValueError`); map it to HTTP 400 with one exception handler.

- **`MarketDataSource`** — ABC implemented by `SimulatorDataSource` and `MassiveDataSource`.
  `start(tickers, initial_prices=None)` -> `add_ticker()` / `remove_ticker()` -> `stop()`.
  Tickers are normalised; `remove_ticker` evicts from the cache; `stop()` is idempotent.

- **`MarketDataService(source, cache)`** — **the only code that changes the tracked set.**
  Tracked = watchlist ∪ held positions, so removing a held ticker from the watchlist never
  un-prices it.
  - `await start(watchlist, held, initial_prices)` — call in FastAPI `lifespan`; pass each held
    ticker's last fill price as `initial_prices` so simulator P&L is continuous across restarts
  - `await sync(watchlist, held)` — after any watchlist change or trade
  - `await ensure_price(ticker, timeout=3.0) -> float | None` — before a trade; `None` → answer 409
  - `tracked() -> set[str]`, `await stop()`

- **`create_market_data_source(cache)`** — Factory, returns an unstarted source:
  `MassiveDataSource` if `MASSIVE_API_KEY` (or its alias `POLYGON_API_KEY`) is non-empty, otherwise `SimulatorDataSource`.

### Environment variables

| Var | Default | Effect |
|---|---|---|
| `MASSIVE_API_KEY` | empty | Non-empty → Massive/Polygon (live snapshots; free key auto-falls back to end-of-day) |
| `POLYGON_API_KEY` | empty | Alias for `MASSIVE_API_KEY`, used only if that is empty |
| `MASSIVE_POLL_INTERVAL` | `15` | Live-mode poll seconds (min 1) |
| `SIM_EVENT_PROBABILITY` | `0.001` | Simulator jump chance per ticker per tick |
| `SIM_SEED` | unset | Integer → deterministic simulator |

Bad values are logged and replaced by the default; they never stop startup.

### SSE Streaming

```python
app.include_router(create_stream_router(price_cache))  # GET /api/stream/prices
```

First frame `retry: 1000`, then one unnamed `data:` event holding **all** tracked tickers
(`{"AAPL": {...PriceUpdate.to_dict()}, ...}`) whenever the cache changes (checked every 500 ms),
`{}` after the last ticker is removed, and a `: ping` comment every 15 s when idle. A ticker missing
from the payload has been untracked. The watchlist UI should come from `GET /api/watchlist`, not from
the payload keys (held tickers not on the watchlist are included).

### Seed Data

Default tickers: `DEFAULT_TICKERS` (AAPL, GOOGL, MSFT, AMZN, TSLA, NVDA, META, JPM, V, NFLX). Seed prices and
per-ticker volatility/drift params are in `app/market/seed_prices.py`.

## Running Tests

```bash
uv run --extra dev pytest -v              # All tests
uv run --extra dev pytest --cov=app       # With coverage
uv run --extra dev ruff check .           # Lint
```

Massive tests mock the SDK *client* but build real `TickerSnapshot` / `GroupedDailyAgg` models
with `from_dict()`. Never use `MagicMock` snapshots: they accept any attribute and hide field bugs.

## Demo

```bash
uv run market_data_demo.py   # Live terminal dashboard with simulated prices
```
