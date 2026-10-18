# Market Data Backend — Summary

**Status:** Complete. Implements every item in `MARKET_DATA_DESIGN.md` §15 (the post-review migration).
156 tests passing, 99% coverage, `ruff` clean, live SSE smoke test under uvicorn passing.

The authoritative design (full module code, wire contract, failure modes) is
`planning/MARKET_DATA_DESIGN.md`. This page is the short version for other agents.

## What Was Built

A market data subsystem in `backend/app/market/` (10 modules) providing live price simulation and
real market data behind one interface.

### Architecture

```
create_market_data_source(cache)        MASSIVE_API_KEY set?  yes → MassiveDataSource
                                                               no  → SimulatorDataSource
        │  cache.update(ticker, price, ts, reference_price)
        ▼
   PriceCache (thread-safe, version counter)
        │  reads only
        ├──→ SSE  GET /api/stream/prices
        ├──→ portfolio valuation / trades / LLM context

MarketDataService.sync(watchlist, held) / ensure_price(ticker)
        └──→ the ONLY caller of source.add_ticker / remove_ticker
```

### Modules

| File | Purpose |
|------|---------|
| `models.py` | `PriceUpdate`: immutable snapshot; `to_dict()` is the wire format. Tick-to-tick `change`/`direction` plus `reference_price`/`day_change`/`day_change_percent` |
| `cache.py` | `PriceCache`: thread-safe store; sticky reference price; `version` bumped on update and remove |
| `tickers.py` | `normalize_ticker`, `InvalidTickerError` (→ HTTP 400), `DEFAULT_TICKERS` |
| `interface.py` | `MarketDataSource` ABC: `start(tickers, initial_prices=None)`, `stop`, `add_ticker`, `remove_ticker`, `get_tickers` |
| `seed_prices.py` | Seed prices, per-ticker GBM params, correlation groups |
| `simulator.py` | `GBMSimulator` (pure, seedable, Cholesky-correlated GBM + jump events) and `SimulatorDataSource` |
| `massive_client.py` | `MassiveDataSource`: snapshot polling (live) with automatic end-of-day fallback on a free key (403) |
| `factory.py` | `create_market_data_source()`: reads `MASSIVE_API_KEY`, `MASSIVE_POLL_INTERVAL`, `SIM_EVENT_PROBABILITY`, `SIM_SEED` |
| `service.py` | `MarketDataService`: tracked tickers = watchlist ∪ held positions; `ensure_price` for trades |
| `stream.py` | `create_stream_router()`: SSE endpoint; public `generate_price_events` |

### Decisions that settle PLAN.md §13 items

| PLAN item | Decision |
|---|---|
| §13.1 #1 removing a held ticker | Tracked = watchlist ∪ held (`MarketDataService.sync`) |
| §13.1 #2 trading an untracked ticker | `ensure_price` auto-tracks and waits up to 3 s; `None` → 409 "price not yet available, retry" |
| §13.1 #3 valid ticker | Strip + uppercase + `^[A-Z][A-Z0-9.\-]{0,9}$`; anything well-formed works in simulator mode |
| §13.1 #4 daily change % | `reference_price`: simulator start price / Massive previous close |
| §13.1 #5 SSE payload | One event holding all tracked tickers, keyed by ticker (below) |
| §13.1 #14 no price yet | Ticker absent from the cache → `None` → UI shows "—", valuation uses `avg_cost` |

### SSE wire contract: `GET /api/stream/prices`

```
retry: 1000

data: {"AAPL": {"ticker": "AAPL", "price": 190.02, "previous_price": 190.02, "timestamp": 1791706748.17, "change": 0.0, "change_percent": 0.0, "direction": "flat", "reference_price": 190.0, "day_change": 0.02, "day_change_percent": 0.0105}, "AMZN": {...}}

: ping
```

- An event is sent only when the cache changed (checked every 500 ms); a new connection always gets the full snapshot first.
- `{}` is sent after the last ticker is removed. A ticker missing from the payload was untracked.
- `: ping` every 15 s while idle (Massive EOD mode can be silent for 30 min).
- `timestamp` is Unix seconds. `reference_price`, `day_change`, `day_change_percent` may be `null`.

### Environment variables

| Var | Default | Effect |
|---|---|---|
| `MASSIVE_API_KEY` | empty | Non-empty → Massive. Otherwise the simulator. |
| `POLYGON_API_KEY` | empty | Alias for `MASSIVE_API_KEY`, used only if that is empty. |
| `MASSIVE_POLL_INTERVAL` | `15` | Live-mode poll seconds (min 1) |
| `SIM_EVENT_PROBABILITY` | `0.001` | Jump chance per ticker per tick; `0.0002` is calmer |
| `SIM_SEED` | unset | Integer → reproducible simulator path |

All are in `.env.example` at the repo root.

## Test Suite

**156 tests, all passing** (`uv run --extra dev pytest`). 9 modules in `backend/tests/market/`.

| Module | Tests | Covers |
|--------|-------|--------|
| test_models.py | 16 | change / day-change maths, exact `to_dict()` keys |
| test_cache.py | 20 | sticky reference, version on remove, `timestamp=0`, rounding |
| test_tickers.py | 17 | normalisation table |
| test_simulator.py | 31 | GBM, seeded determinism, correlation ≈ 0.6 / 0.3, event probability, `initial_prices`, `LinAlgError` fallback |
| test_simulator_source.py | 16 | async contract: start/add/remove/stop, normalisation, no writes after stop |
| test_massive.py | 27 | real SDK models: fallback chain, ns timestamps, 403 → EOD, chunking, removed-mid-poll, wake-on-add |
| test_factory.py | 15 | blank key, poll interval, SIM env vars, bad values |
| test_service.py | 8 | held tickers survive watchlist removal, `ensure_price` paths |
| test_stream.py | 6 | event format, `{}` after last removal, heartbeat, disconnect, router idempotence, real-uvicorn SSE |

Coverage: 99% of `app/market`.

## History

1. Original build (archive: `planning/archive/MARKET_DATA_DESIGN.md`) and review
   (`planning/archive/MARKET_DATA_REVIEW.md`). Review §5 items: hatch `packages = ["app"]`,
   `_generate_events` annotation (now public `generate_price_events`), public `GBMSimulator.get_tickers()`,
   `DEFAULT_CORR` removed in favour of `CROSS_GROUP_CORR`, unused test imports — all resolved.
2. Migration to `MARKET_DATA_DESIGN.md` (§15 checklist). Most important fix: `massive_client.py` used to
   read `snap.last_trade.timestamp / 1000`, which doesn't exist on the real SDK model (it's
   `sip_timestamp`, in **nanoseconds**), so every real snapshot was skipped. The old tests used
   `MagicMock` snapshots and couldn't catch it; the rewritten tests use real models.
   Runtime dependency `tzdata` was added because `zoneinfo` has no system tz database on Windows.

## Demo

```bash
cd backend
uv run market_data_demo.py
```

A live Rich dashboard with all 10 tickers, sparklines, direction arrows and an event log. Runs 60 s or until Ctrl+C.

## Usage for Downstream Code

```python
from app.market import (
    InvalidTickerError, MarketDataService, PriceCache, create_market_data_source,
    create_stream_router, normalize_ticker,
)

price_cache = PriceCache()
market = MarketDataService(create_market_data_source(price_cache), price_cache)

# lifespan
await market.start(watchlist={...}, held={...}, initial_prices={"PYPL": 71.2})
app.include_router(create_stream_router(price_cache))      # before the static mount

# routes
ticker = normalize_ticker(body.ticker)                      # InvalidTickerError → 400
await market.sync(watchlist_tickers, held_tickers)          # after watchlist change / trade
price = await market.ensure_price(ticker)                   # None → 409
price_cache.get_price("AAPL")                               # float | None
price_cache.get_all()                                       # dict[str, PriceUpdate]

# shutdown
await market.stop()
```
