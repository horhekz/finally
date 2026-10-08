# Unified Market Data Interface

The single Python API that FinAlly's backend uses to get stock prices. It uses **Massive** when `MASSIVE_API_KEY` is set and non-empty, and the **GBM simulator** otherwise. Nothing downstream (SSE, portfolio, trades, chat) knows or cares which source is running.

Related docs: `MASSIVE_API.md` (API research), `MARKET_SIMULATOR.md` (simulator design).

**Status:** Most of this is already built in `backend/app/market/`. Sections marked **🔧 Proposed** are changes this doc recommends. They resolve PLAN §13.1 items 1–4 and 14, and the Massive bugs in `MASSIVE_API.md` §9. §11 lists them as a checklist.

---

## 1. Design Principles

1. **Push, don't pull.** A data source writes into a shared in-memory `PriceCache` on its own schedule. Consumers **only read the cache** and never call the source for a price. That way a slow Massive call can never block a trade or an SSE tick.
2. **One interface, two implementations.** `MarketDataSource` (an ABC) has the methods `start / stop / add_ticker / remove_ticker / get_tickers`.
3. **Pick the source once, at startup**, in a factory that reads the environment.
4. **Never crash the loop.** Source errors are logged and the last good price stays in the cache.
5. **Plain data out.** `PriceUpdate` is a frozen dataclass with a `to_dict()` that *is* the wire format.

---

## 2. Architecture

```
                 ┌──────────────── create_market_data_source(cache) ────────────────┐
                 │  MASSIVE_API_KEY set?                                             │
                 │     yes → MassiveDataSource (REST poll, 15 s)                     │
                 │     no  → SimulatorDataSource (GBM, 500 ms)                       │
                 └──────────────────────────────┬───────────────────────────────────┘
                                                │ writes  cache.update(ticker, price, ...)
                                                ▼
                                       ┌─────────────────┐
                                       │   PriceCache    │  thread-safe, versioned
                                       └───────┬─────────┘
                       reads                   │                    reads
        ┌──────────────────────┬───────────────┼───────────────────┬──────────────────┐
        ▼                      ▼               ▼                   ▼                  ▼
  SSE /api/stream/prices  POST /trade    GET /portfolio     GET /watchlist     LLM chat context
```

Ticker-set changes flow the other way, through one helper (§7):

```
watchlist add/remove, trade fill  ──►  MarketDataService.sync()  ──►  source.add_ticker / remove_ticker
```

---

## 3. Module Layout

```
backend/app/market/
├── __init__.py        # public exports (below)
├── models.py          # PriceUpdate
├── cache.py           # PriceCache
├── interface.py       # MarketDataSource ABC
├── factory.py         # create_market_data_source()
├── simulator.py       # GBMSimulator + SimulatorDataSource   (see MARKET_SIMULATOR.md)
├── seed_prices.py     # seed prices, per-ticker params, correlation groups
├── massive_client.py  # MassiveDataSource
├── stream.py          # create_stream_router() → GET /api/stream/prices
├── tickers.py         # 🔧 normalize_ticker() / validation
└── service.py         # 🔧 MarketDataService: tracked set = watchlist ∪ positions
```

Public API, i.e. what other backend modules import:

```python
from app.market import (
    PriceUpdate,                 # data model
    PriceCache,                  # shared store
    MarketDataSource,            # ABC (for typing)
    create_market_data_source,   # factory
    create_stream_router,        # SSE router
    normalize_ticker,            # 🔧
    MarketDataService,           # 🔧
)
```

---

## 4. Core Types

### 4.1 `PriceUpdate` (`models.py`)

```python
@dataclass(frozen=True, slots=True)
class PriceUpdate:
    ticker: str
    price: float                          # latest price, rounded to 2 dp
    previous_price: float                 # price at the previous update (tick-to-tick)
    timestamp: float                      # Unix seconds (float)
    reference_price: float | None = None  # 🔧 reference for daily change: prev close / session open

    @property
    def change(self) -> float: ...            # price - previous_price
    @property
    def change_percent(self) -> float: ...    # tick-to-tick %
    @property
    def direction(self) -> str: ...           # "up" | "down" | "flat"  (drives the flash animation)
    @property
    def day_change_percent(self) -> float | None: ...   # 🔧 (price - reference_price) / reference_price * 100

    def to_dict(self) -> dict: ...
```

**Wire format** (`to_dict()`). The SSE stream and REST endpoints use exactly this:

```json
{
  "ticker": "AAPL",
  "price": 190.42,
  "previous_price": 190.38,
  "timestamp": 1791322345.512,
  "change": 0.04,
  "change_percent": 0.021,
  "direction": "up",
  "reference_price": 190.00,
  "day_change_percent": 0.2211
}
```

`reference_price` and `day_change_percent` are 🔧 proposed (PLAN §13.1 item 4). Where they come from:

| Source | `reference_price` |
|---|---|
| Simulator | The seed price the ticker started from at process start (a stand-in for "today's open") |
| Massive, live | `snapshot.prev_day.close` (yesterday's close, the standard way to compute daily change) |
| Massive, EOD | The close of the trading day before the latest bar |

### 4.2 `PriceCache` (`cache.py`)

```python
class PriceCache:
    def update(self, ticker: str, price: float, timestamp: float | None = None,
               reference_price: float | None = None) -> PriceUpdate: ...
    def get(self, ticker: str) -> PriceUpdate | None: ...
    def get_price(self, ticker: str) -> float | None: ...
    def get_all(self) -> dict[str, PriceUpdate]: ...      # shallow copy
    def remove(self, ticker: str) -> None: ...
    @property
    def version(self) -> int: ...                         # bumps on every update; SSE uses it to detect changes
    def __len__(self) -> int: ...
    def __contains__(self, ticker: str) -> bool: ...
```

- It's guarded by a `threading.Lock`, so it's safe to call from `asyncio.to_thread` workers as well as the event loop.
- On the first update for a ticker, `previous_price == price` (direction `"flat"`).
- 🔧 `reference_price` is **sticky**. If the caller passes `None`, keep the stored value. If nothing is stored yet, default to the first price seen. The simulator then only has to pass it once, when it seeds the cache.

```python
# 🔧 proposed body of update()
with self._lock:
    prev = self._prices.get(ticker)
    ref = reference_price if reference_price is not None else (
        prev.reference_price if prev and prev.reference_price is not None else price)
    upd = PriceUpdate(ticker=ticker, price=round(price, 2),
                      previous_price=round(prev.price if prev else price, 2),
                      timestamp=timestamp or time.time(),
                      reference_price=round(ref, 2))
    self._prices[ticker] = upd
    self._version += 1
    return upd
```

### 4.3 `MarketDataSource` (`interface.py`)

```python
class MarketDataSource(ABC):
    @abstractmethod
    async def start(self, tickers: list[str]) -> None:
        """Start the background task. Seed the cache before returning when possible. Call once."""
    @abstractmethod
    async def stop(self) -> None:
        """Cancel the background task. Idempotent."""
    @abstractmethod
    async def add_ticker(self, ticker: str) -> None:
        """Start tracking. No-op if already tracked."""
    @abstractmethod
    async def remove_ticker(self, ticker: str) -> None:
        """Stop tracking AND evict from the cache. No-op if unknown."""
    @abstractmethod
    def get_tickers(self) -> list[str]:
        """Currently tracked tickers."""
```

The contract every implementation must keep (enforce it with a shared parametrised test):

| Behaviour | Simulator | Massive |
|---|---|---|
| `start()` fills the cache before it returns | ✓ seed prices | ✓ first poll is awaited |
| `add_ticker()` gives a price | **immediately** | on the **next poll** (≤ interval) |
| `remove_ticker()` evicts from the cache | ✓ | ✓ |
| Tickers are normalised to uppercase | 🔧 via `normalize_ticker` | ✓ |
| Errors stay inside the loop | ✓ | ✓ |

### 4.4 Factory (`factory.py`)

```python
def create_market_data_source(price_cache: PriceCache) -> MarketDataSource:
    api_key = os.environ.get("MASSIVE_API_KEY", "").strip()
    if api_key:
        return MassiveDataSource(
            api_key=api_key,
            price_cache=price_cache,
            poll_interval=float(os.environ.get("MASSIVE_POLL_INTERVAL", "15")),   # 🔧
        )
    return SimulatorDataSource(price_cache=price_cache)
```

| Env var | Default | Effect |
|---|---|---|
| `MASSIVE_API_KEY` | empty | Non-empty → Massive. Empty or unset → simulator. |
| `MASSIVE_POLL_INTERVAL` 🔧 | `15` | Seconds between snapshot polls. Use 2–5 on paid unlimited plans. |

---

## 5. Massive Implementation (`MassiveDataSource`)

It polls Massive with **one REST call per cycle for all tracked tickers** and writes the results to the cache. The SDK is synchronous, so each call runs in `asyncio.to_thread`.

### 5.1 🔧 Two modes, auto-detected

A free (Basic) key gets **403 NOT_AUTHORIZED** on snapshot endpoints (`MASSIVE_API.md` §2). Rather than spamming errors, the source switches modes on its own:

| Mode | Endpoint | Interval | Prices move? |
|---|---|---|---|
| `live` (default) | `get_snapshot_all(STOCKS, tickers=...)` | `poll_interval` (15 s) | Yes. Real-time on Advanced, 15-min delayed on Starter/Developer. |
| `eod` (fallback after 403) | `get_grouped_daily_aggs(date)` | 30 min | No. Last close only. |

### 5.2 Code

```python
class MassiveDataSource(MarketDataSource):
    EOD_INTERVAL = 1800.0

    def __init__(self, api_key: str, price_cache: PriceCache, poll_interval: float = 15.0):
        self._api_key, self._cache, self._interval = api_key, price_cache, poll_interval
        self._tickers: list[str] = []
        self._task: asyncio.Task | None = None
        self._client: RESTClient | None = None
        self._mode: Literal["live", "eod"] = "live"

    async def start(self, tickers: list[str]) -> None:
        self._client = RESTClient(api_key=self._api_key)
        self._tickers = [normalize_ticker(t) for t in tickers]
        await self._poll_once()                         # cache is warm before start() returns
        self._task = asyncio.create_task(self._poll_loop(), name="massive-poller")

    async def add_ticker(self, ticker: str) -> None:
        t = normalize_ticker(ticker)
        if t not in self._tickers:
            self._tickers.append(t)                     # priced on next poll

    async def remove_ticker(self, ticker: str) -> None:
        t = normalize_ticker(ticker)
        self._tickers = [x for x in self._tickers if x != t]
        self._cache.remove(t)

    async def _poll_loop(self) -> None:
        while True:
            await asyncio.sleep(self._interval if self._mode == "live" else self.EOD_INTERVAL)
            await self._poll_once()

    async def _poll_once(self) -> None:
        if not self._tickers or not self._client:
            return
        try:
            if self._mode == "live":
                await self._poll_snapshot()
            else:
                await self._poll_eod()
        except BadResponse as e:
            if self._mode == "live" and "NOT_AUTHORIZED" in str(e):
                logger.warning("Massive plan lacks snapshots; falling back to end-of-day prices")
                self._mode = "eod"
                await self._poll_eod()
            else:
                logger.error("Massive poll failed: %s", e)
        except Exception as e:                          # network, parsing, ...
            logger.error("Massive poll failed: %s", e)

    async def _poll_snapshot(self) -> None:
        snaps = await asyncio.to_thread(
            self._client.get_snapshot_all, SnapshotMarketType.STOCKS, self._tickers)
        for s in snaps:
            price = _first_positive(
                s.last_trade and s.last_trade.price,
                s.min and s.min.close,
                s.day and s.day.close,
                s.prev_day and s.prev_day.close,
            )
            if price is None:
                continue
            ts_ns = (s.last_trade and s.last_trade.sip_timestamp) or s.updated
            self._cache.update(
                ticker=s.ticker,
                price=price,
                timestamp=ts_ns / 1e9 if ts_ns else None,          # NANOSECONDS → seconds
                reference_price=s.prev_day.close if s.prev_day else None,
            )

    async def _poll_eod(self) -> None:
        latest, previous = await asyncio.to_thread(self._last_two_trading_days)
        for t in self._tickers:
            bar = latest.get(t)
            if bar:
                prev = previous.get(t)
                self._cache.update(t, bar.close, bar.timestamp / 1e3,
                                   reference_price=prev.close if prev else bar.open)

    def _last_two_trading_days(self) -> tuple[dict, dict]:
        """Walk back from today until two non-empty grouped-daily results are found (max 10 days)."""
        found: list[dict] = []
        day = datetime.now(ZoneInfo("America/New_York")).date()
        for _ in range(10):
            bars = self._client.get_grouped_daily_aggs(date=day.isoformat(), adjusted=True)
            if bars:
                found.append({b.ticker: b for b in bars})
                if len(found) == 2:
                    break
            day -= timedelta(days=1)
        return (found + [{}, {}])[0], (found + [{}, {}])[1]


def _first_positive(*vals: float | None) -> float | None:
    return next((float(v) for v in vals if v), None)
```

Notes:
- With the 5 req/min free tier, EOD mode can use up to ~4 calls (today is empty → walk back) every 30 min. That's well inside the quota.
- A ticker Massive doesn't know never shows up in the response, so it never gets a cache entry. The UI shows "—" (PLAN §13.1 item 14).
- **Don't** call `remove_ticker` → `cache.remove` from the poll path. Only explicit removals evict.

---

## 6. Simulator Implementation (`SimulatorDataSource`)

This is a summary. The full design is in `MARKET_SIMULATOR.md`.

- `GBMSimulator` holds the per-ticker price state and a Cholesky factor of the sector correlation matrix. `step()` moves every ticker forward by one correlated GBM step, with occasional 2–5% shock "events".
- `SimulatorDataSource` wraps it in an asyncio loop: `step()` → `cache.update()` for each ticker → `sleep(0.5)`.
- `start()` and `add_ticker()` write the seed price to the cache straight away, so a new ticker has a price within the same request.

---

## 7. 🔧 Ticker Rules and the Tracked Set

### 7.1 `normalize_ticker` (`tickers.py`), for PLAN §13.1 item 3

```python
TICKER_RE = re.compile(r"^[A-Z][A-Z0-9.\-]{0,9}$")     # AAPL, BRK.B, BF-B

class InvalidTicker(ValueError): ...

def normalize_ticker(raw: str) -> str:
    t = (raw or "").strip().upper()
    if not TICKER_RE.fullmatch(t):
        raise InvalidTicker(f"Invalid ticker symbol: {raw!r}")
    return t
```

Every entry point calls this: the API routes, the LLM action executor, and both data sources. In simulator mode, any symbol that is well-formed is accepted.

### 7.2 `MarketDataService` (`service.py`), for PLAN §13.1 items 1–2

**Decision (option a):** tracked tickers = **watchlist ∪ open positions**. Removing AAPL from the watchlist while you still hold AAPL keeps it priced.

```python
class MarketDataService:
    """Owns the source + cache; the only place that calls add_ticker/remove_ticker."""

    def __init__(self, source: MarketDataSource, cache: PriceCache):
        self.source, self.cache = source, cache

    async def start(self, watchlist: set[str], held: set[str]) -> None:
        await self.source.start(sorted(watchlist | held))

    async def sync(self, watchlist: set[str], held: set[str]) -> None:
        """Make the tracked set equal watchlist ∪ held. Call after any watchlist change or trade."""
        wanted = watchlist | held
        current = set(self.source.get_tickers())
        for t in wanted - current:
            await self.source.add_ticker(t)
        for t in current - wanted:
            await self.source.remove_ticker(t)

    async def ensure_price(self, ticker: str, timeout: float = 2.0) -> float | None:
        """Track a ticker if needed and wait briefly for its first price (used before a trade)."""
        if ticker not in self.source.get_tickers():
            await self.source.add_ticker(ticker)
        deadline = time.monotonic() + timeout
        while (p := self.cache.get_price(ticker)) is None and time.monotonic() < deadline:
            await asyncio.sleep(0.1)
        return p

    async def stop(self) -> None:
        await self.source.stop()
```

How it's used for trading an untracked ticker (§13.1 item 2): the trade route calls `await service.ensure_price(t)`. With the simulator the price is there at once. With Massive it's usually `None` after 2 s, so the route returns **409** `{"error": "Price for PYPL not yet available, retry shortly"}`. The ticker stays tracked, so a retry works after the next poll.

---

## 8. FastAPI Integration

```python
# backend/app/main.py
from contextlib import asynccontextmanager
from fastapi import FastAPI
from app.market import PriceCache, create_market_data_source, create_stream_router, MarketDataService
from app.db import get_watchlist_tickers, get_held_tickers, init_db

price_cache = PriceCache()
service = MarketDataService(create_market_data_source(price_cache), price_cache)

@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    await service.start(set(get_watchlist_tickers()), set(get_held_tickers()))
    app.state.market = service
    yield
    await service.stop()

app = FastAPI(lifespan=lifespan)
app.include_router(create_stream_router(price_cache))   # GET /api/stream/prices
# ... other /api routers ...
# app.mount("/", StaticFiles(directory="static", html=True))   ← mount LAST
```

### 8.1 SSE contract: `GET /api/stream/prices`

- `Content-Type: text/event-stream`. The first frame is `retry: 1000` (the browser reconnects after 1 s).
- About every 500 ms, **if `cache.version` has changed**, it sends **one event holding every tracked ticker**:

```
data: {"AAPL": {"ticker":"AAPL","price":190.42,"previous_price":190.38,"timestamp":1791322345.5,"change":0.04,"change_percent":0.021,"direction":"up","reference_price":190.0,"day_change_percent":0.2211}, "MSFT": {...}}

```

- There's no `event:` name, so the client uses `es.onmessage`. A ticker that drops out of the payload has been removed, and the frontend should drop it.
- In Massive EOD mode the version barely changes, so events are rare. The frontend must not treat silence as a disconnect. Use `EventSource.readyState` and `onerror` for that.

```ts
const es = new EventSource("/api/stream/prices");
es.onmessage = (e) => {
  const prices: Record<string, PriceUpdate> = JSON.parse(e.data);
  store.applyPrices(prices);
};
es.onopen = () => setStatus("connected");
es.onerror = () => setStatus(es.readyState === EventSource.CLOSED ? "disconnected" : "reconnecting");
```

### 8.2 Consumers read the cache

```python
# Trade execution
price = await request.app.state.market.ensure_price(ticker)
if price is None:
    raise HTTPException(409, detail="Price not yet available, retry")

# Portfolio valuation (PLAN §13.1 item 14: fall back to avg_cost)
def mark(pos) -> float:
    return price_cache.get_price(pos.ticker) or pos.avg_cost

# GET /api/watchlist first paint
[{"ticker": t, **(u.to_dict() if (u := price_cache.get(t)) else {"price": None})} for t in tickers]

# After POST/DELETE /api/watchlist or a trade
await request.app.state.market.sync(set(db.watchlist()), set(db.held()))
```

---

## 9. Testing

| Test | What it checks |
|---|---|
| **Contract test** (parametrised over both sources) | `start()` warms the cache; add/remove/get_tickers semantics; `stop()` is idempotent |
| `PriceCache` | first update is flat, direction, version bump, sticky `reference_price`, thread safety |
| Massive parsing | Build **real** models with `TickerSnapshot.from_dict(<JSON sample from MASSIVE_API.md §4.1>)`, **not `MagicMock`**. A mock accepts any attribute and hides the `.timestamp` bug. Check that ns → s is right. |
| Massive fallback | A mocked `get_snapshot_all` raises `BadResponse('{"status":"NOT_AUTHORIZED"}')` → the mode becomes `eod` and grouped-daily prices land in the cache |
| Massive price fallback chain | `last_trade=None` → `min.close` is used, and so on |
| `normalize_ticker` | `" aapl "`→`AAPL`, `BRK.B` ok, `""`, `"TOOLONGTICKER"`, `"$$$"` are rejected |
| `MarketDataService.sync` | Removing a held ticker from the watchlist doesn't call `remove_ticker` |

---

## 10. Non-Goals

- Massive WebSocket streaming. REST polling is enough, and it works on every plan that has snapshots.
- Historical backfill for charts. Charts build up from SSE (PLAN §2). `MASSIVE_API.md` §4.6 documents the endpoint for later.
- Multi-user fan-out. The cache is already shared, so nothing changes there.

---

## 11. 🔧 Change Checklist vs. the Current Code

✅ = done (2026-10-06). #2 is done except the `reference_price` part, which waits on #4. EOD mode currently uses the latest bar's close only.

| # | File | Change | Why |
|---|---|---|---|
| 1 | ✅ `massive_client.py` | `last_trade.sip_timestamp / 1e9` (or `updated / 1e9`) instead of `last_trade.timestamp / 1000` | Fixes `AttributeError` on every real snapshot |
| 2 | ✅ `massive_client.py` | Price fallback chain, with `prev_day.close` as the reference | Pre-market gaps; daily change % |
| 3 | ✅ `massive_client.py` | 403 → EOD mode using `get_grouped_daily_aggs` | Free-tier keys work at all |
| 4 | `models.py`, `cache.py` | Add `reference_price` and `day_change_percent` | PLAN §13.1 item 4 |
| 5 | `simulator.py` | Pass the seed price as `reference_price`; normalise tickers | Consistent daily change; uppercase |
| 6 | `tickers.py` (new) | `normalize_ticker` + `InvalidTicker` | PLAN §13.1 item 3 |
| 7 | `service.py` (new) | `MarketDataService` (tracked = watchlist ∪ held, `ensure_price`) | PLAN §13.1 items 1–2 |
| 8 | `factory.py` | Read `MASSIVE_POLL_INTERVAL` | Paid tiers can poll faster |
| 9 | `stream.py` | Create the `APIRouter` **inside** `create_stream_router` (it's module-level today) | Calling the factory twice, e.g. in tests, registers the route twice |
| 10 | ✅ `tests/market/test_massive.py` | Replace `MagicMock` snapshots with `TickerSnapshot.from_dict` | It's the reason bug #1 went unnoticed |
