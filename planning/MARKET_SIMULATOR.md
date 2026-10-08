# Market Simulator Design

How FinAlly makes realistic-looking live prices when no `MASSIVE_API_KEY` is set. The simulator is the **default** data source. It runs in-process, needs no network, and implements the same `MarketDataSource` interface as the Massive client (see `MARKET_INTERFACE.md`).

**Status:** Built in `backend/app/market/simulator.py` and `seed_prices.py`. Items marked **🔧 Proposed** are recommended refinements (§9).

---

## 1. Goals

| Goal | How it's met |
|---|---|
| Looks like a real market | Geometric Brownian Motion: prices stay positive, moves are proportional to price, returns are lognormal |
| Each stock has its own character | Per-ticker volatility (σ) and drift (μ). TSLA and NVDA are jumpy; JPM and V are calm. |
| Stocks move together | Sector correlation applied through a Cholesky factor (tech ≈ 0.6) |
| Drama for demos | Rare random 2–5% jumps ("events") |
| Feels live | A new tick every 500 ms. That matches the SSE cadence. |
| Starts somewhere believable | Seed prices near real levels (AAPL $190, NVDA $800, …) |
| Any ticker works | Unknown symbols get a random seed price and default parameters |
| Cheap | One NumPy matrix-vector product per tick. Microseconds for dozens of tickers. |

Non-goals: order books, bid/ask spreads, volume, market hours, mean reversion, or fitting real history.

---

## 2. The Math

### 2.1 GBM step

Each tick, every ticker's price is multiplied by a lognormal factor:

```
S(t+Δt) = S(t) · exp( (μ − σ²/2)·Δt  +  σ·√Δt·Z )
```

| Symbol | Meaning | Value |
|---|---|---|
| `S` | price | starts at the seed price |
| `μ` | annualised drift | 0.03 – 0.08 |
| `σ` | annualised volatility | 0.17 – 0.50 |
| `Δt` | tick length as a fraction of a **trading** year | `0.5 / (252 · 6.5 · 3600) ≈ 8.48e-8` |
| `Z` | correlated standard normal | from §2.2 |

The `−σ²/2` term (the Itô correction) keeps the *expected* price growing at `μ`. Using `exp` means a price can never go negative.

Measuring Δt in **trading** seconds (6.5 h/day × 252 days) means one simulated trading day takes 6.5 real hours. The volatility you see while watching the screen therefore matches the stated annual σ.

### 2.2 What the numbers mean (diffusion only)

| Ticker | σ | Per tick (500 ms) | Per minute | Per hour | Per trading day |
|---|---|---|---|---|---|
| V | 0.17 | 0.0050% (~$0.014) | 0.054% | 0.42% | 1.07% |
| AAPL | 0.22 | 0.0064% (~$0.012) | 0.070% | 0.54% | 1.39% |
| TSLA | 0.50 | 0.0146% (~$0.036) | 0.160% | 1.24% | 3.15% |

These are one-standard-deviation moves. Because prices are rounded to cents, a quiet stock like AAPL often ticks by just 0–2 ¢. That's realistic, and it means some ticks are "flat" (no flash).

### 2.3 Correlated draws (Cholesky)

Correlation comes from a **sector correlation matrix** `C` (n × n). The steps are:

1. Build `C`, with `C[i][i] = 1` and `C[i][j] = ρ(ticker_i, ticker_j)`.
2. Factor it: `C = L·Lᵀ` (`np.linalg.cholesky`). This is done **only when the ticker set changes**.
3. On each tick: `z = standard_normal(n)` and then `Z = L @ z`. Now `Cov(Z) = C`.

Pairwise ρ (`seed_prices.py`):

| Pair | ρ |
|---|---|
| Both in tech (AAPL, GOOGL, MSFT, AMZN, META, NVDA, NFLX) | 0.6 |
| Both in finance (JPM, V) | 0.5 |
| TSLA with anything | 0.3 (it "does its own thing") |
| Cross-sector, or any unknown ticker | 0.3 |

This block structure is always positive definite: every off-diagonal value is between 0.3 and 0.6, it behaves like a market factor plus sector factors, and ρ < 1 throughout. So the Cholesky never fails, even with dynamically added tickers that all get 0.3.

### 2.4 Random events (jumps)

After the GBM step, each ticker independently has an `event_probability` chance (default **0.001**) of a shock:

```
S ← S · (1 ± U(0.02, 0.05))        # random sign, 2–5 % jump
```

At 2 ticks/s, each ticker gets an event about every **8 minutes**. With 10 tickers, something jumps about every **50 seconds**. These jumps are what make the price flashes and the AI commentary interesting.

> **Tuning note:** At the default rate, jumps dominate realised volatility. They add roughly **±9.7% per hour** per ticker, against **±0.5%/hour** from GBM diffusion for AAPL. Over a long demo session prices can drift ±20–30% from their seeds, and the heatmap gets very red/green. That's fine for drama. For more realistic behaviour, use `event_probability=0.0002` (≈ ±4.3%/hr from events, one jump across 10 tickers every ~4 min). See §9.

---

## 3. Parameters (`seed_prices.py`)

```python
SEED_PRICES = {"AAPL": 190.00, "GOOGL": 175.00, "MSFT": 420.00, "AMZN": 185.00, "TSLA": 250.00,
               "NVDA": 800.00, "META": 500.00, "JPM": 195.00, "V": 280.00, "NFLX": 600.00}

TICKER_PARAMS = {                      # annualised
    "AAPL": {"sigma": 0.22, "mu": 0.05},  "GOOGL": {"sigma": 0.25, "mu": 0.05},
    "MSFT": {"sigma": 0.20, "mu": 0.05},  "AMZN":  {"sigma": 0.28, "mu": 0.05},
    "TSLA": {"sigma": 0.50, "mu": 0.03},  "NVDA":  {"sigma": 0.40, "mu": 0.08},
    "META": {"sigma": 0.30, "mu": 0.05},  "JPM":   {"sigma": 0.18, "mu": 0.04},
    "V":    {"sigma": 0.17, "mu": 0.04},  "NFLX":  {"sigma": 0.35, "mu": 0.05},
}
DEFAULT_PARAMS = {"sigma": 0.25, "mu": 0.05}          # any other ticker

CORRELATION_GROUPS = {"tech": {...7 names...}, "finance": {"JPM", "V"}}
INTRA_TECH_CORR, INTRA_FINANCE_CORR, CROSS_GROUP_CORR, TSLA_CORR = 0.6, 0.5, 0.3, 0.3
```

When a ticker isn't in `SEED_PRICES` (e.g., the user adds `PYPL`), its seed price is `uniform(50, 300)` and it uses `DEFAULT_PARAMS`. It joins the correlation matrix at ρ = 0.3 with everything else.

To add a "known" ticker, add a row to `SEED_PRICES` and `TICKER_PARAMS`, plus a group entry if it belongs to a sector. No code changes are needed.

---

## 4. Code Structure

There are two classes with a clear split: **pure math** and **async plumbing**.

```
simulator.py
├── GBMSimulator            # synchronous, no I/O, no asyncio. Easy to unit-test.
│   ├── __init__(tickers, dt=DEFAULT_DT, event_probability=0.001)
│   ├── step() -> dict[str, float]          # hot path: one tick for all tickers
│   ├── add_ticker(t) / remove_ticker(t)    # then _rebuild_cholesky()
│   ├── get_price(t) / get_tickers()
│   ├── _add_ticker_internal(t)             # seed price + params, no rebuild (batch init)
│   ├── _rebuild_cholesky()                 # O(n²) build + O(n³) factor; n is small
│   └── _pairwise_correlation(t1, t2)       # sector rules from §2.3
│
└── SimulatorDataSource(MarketDataSource)   # the asyncio side
    ├── __init__(price_cache, update_interval=0.5, event_probability=0.001)
    ├── start(tickers)    # build GBMSimulator, seed the cache, launch _run_loop task
    ├── stop()            # cancel the task (idempotent)
    ├── add_ticker(t)     # sim.add_ticker + write the seed price to the cache now
    ├── remove_ticker(t)  # sim.remove_ticker + cache.remove
    ├── get_tickers()
    └── _run_loop()       # while True: step → cache.update for each ticker → sleep(interval)
```

### 4.1 The hot path

```python
def step(self) -> dict[str, float]:
    n = len(self._tickers)
    if n == 0:
        return {}
    z = np.random.standard_normal(n)
    if self._cholesky is not None:          # None when n == 1
        z = self._cholesky @ z
    out = {}
    for i, t in enumerate(self._tickers):
        mu, sigma = self._params[t]["mu"], self._params[t]["sigma"]
        self._prices[t] *= math.exp((mu - 0.5 * sigma**2) * self._dt
                                    + sigma * math.sqrt(self._dt) * z[i])
        if random.random() < self._event_prob:
            self._prices[t] *= 1 + random.uniform(0.02, 0.05) * random.choice([-1, 1])
        out[t] = round(self._prices[t], 2)
    return out
```

Internal state keeps **full precision**. Only the value returned (and cached) is rounded to cents, so rounding error doesn't pile up.

### 4.2 The loop

```python
async def _run_loop(self) -> None:
    while True:
        try:
            for ticker, price in self._sim.step().items():
                self._cache.update(ticker=ticker, price=price)
        except Exception:
            logger.exception("Simulator step failed")     # never kill the loop
        await asyncio.sleep(self._interval)
```

- It runs on the event loop itself. One step costs microseconds, so there's no need for a thread.
- `add_ticker` and `remove_ticker` are called from request handlers on the same event loop, so they can't run in the middle of a `step()`. No lock is needed inside `GBMSimulator`. (The `PriceCache` has its own lock for thread readers.)
- The loop sleeps a fixed amount after each step rather than keeping a precise schedule. A little drift is fine.

### 4.3 Lifecycle

```python
cache = PriceCache()
src = SimulatorDataSource(cache)              # what the factory returns with no API key
await src.start(["AAPL", "MSFT"])             # cache has AAPL=190.00, MSFT=420.00 at once
await src.add_ticker("PYPL")                  # priced at once (random seed in [50, 300])
await src.remove_ticker("MSFT")               # gone from sim + cache; Cholesky rebuilt
await src.stop()
```

---

## 5. Behaviour Guarantees (what tests should assert)

1. Prices are always `> 0`, whatever the shocks.
2. `start()` returns with every starting ticker already in the cache, priced at its seed.
3. `add_ticker()` puts a price in the cache **before it returns**. That's the reason trades on a newly added ticker work immediately in simulator mode.
4. `add_ticker` on an existing ticker and `remove_ticker` on an unknown one are no-ops.
5. After `remove_ticker(t)`, `step()` never returns `t` and the cache has no `t`.
6. With `event_probability=0`, the sample mean of the log return per tick ≈ `(μ−σ²/2)Δt` and its std ≈ `σ√Δt`. Check within tolerance over ~10k steps.
7. With `event_probability=0`, the sample correlation of log returns for AAPL/MSFT ≈ 0.6 and for AAPL/TSLA ≈ 0.3 (±0.05 over ~20k steps).
8. With `event_probability=1`, every tick moves every ticker by at least about 2%.
9. `stop()` is idempotent, and the cache stops changing after it.

---

## 6. Performance

Rough order-of-magnitude estimates (not benchmarked):

| n tickers | Cholesky rebuild | `step()` |
|---|---|---|
| 10 | tens of µs | tens of µs |
| 50 | ~100s of µs | <100 µs |

At 2 Hz this is effectively zero CPU. The Python per-ticker loop costs more than the NumPy work. If n ever reached the hundreds, vectorise: hold `prices`, `mu`, and `sigma` as arrays and do `prices *= np.exp(drift + diff * z)`.

---

## 7. How the Simulator Feeds the UI

```
GBMSimulator.step()  ──500 ms──►  PriceCache.update()  ──version++──►  SSE generator (500 ms)
                                                                  └──► one event, all tickers
```

- `direction` (`up`/`down`/`flat`) drives the green/red flash.
- The frontend builds sparklines by appending each SSE price.
- The daily change % shown in the watchlist is `(price − reference_price) / reference_price`, where the simulator's reference is the seed price (🔧 §9).

---

## 8. Configuration

| Knob | Where | Default |
|---|---|---|
| `update_interval` | `SimulatorDataSource(...)` | 0.5 s |
| `event_probability` | `SimulatorDataSource(...)` | 0.001 |
| `dt` | `GBMSimulator(...)` | `0.5 / 5_896_800` |
| Seed prices, σ, μ, correlations | `seed_prices.py` | §3 |
| 🔧 `SIM_EVENT_PROBABILITY` | env var, read in the factory | 0.001 |
| 🔧 `SIM_SEED` | env var, an int for a reproducible run | unset (random) |

If you change `update_interval`, change `dt` to match (`dt = interval / TRADING_SECONDS_PER_YEAR`). Otherwise the volatility per real second shifts.

---

## 9. 🔧 Proposed Refinements

| # | Change | Why |
|---|---|---|
| 1 | Swap the global `np.random` / `random` for an injected `np.random.Generator` (`GBMSimulator(..., rng=np.random.default_rng(seed))`) and use `rng.random()`, `rng.uniform()`, `rng.choice()` for events | Runs become deterministic for unit tests and E2E (`SIM_SEED`), and nothing else touches the global RNG |
| 2 | Pass `reference_price=<seed price>` when seeding the cache in `start()` / `add_ticker()` | Gives the watchlist a "daily change %" (PLAN §13.1 item 4, `MARKET_INTERFACE.md` §4.1) |
| 3 | Normalise tickers (`normalize_ticker`) in `add_ticker` / `remove_ticker` / `start` | Matches the Massive source, so `aapl` and `AAPL` don't become two tickers |
| 4 | Read `SIM_EVENT_PROBABILITY` in the factory; consider a default of `0.0002` | Jumps currently dominate volatility (§2.4) |
| 5 | Optional `start(tickers, initial_prices: dict[str, float] | None)`. The service passes each held ticker's last trade price | After a container restart, prices reset to seed values (or a *new* random price for unknown tickers), which makes unrealised P&L jump. Seeding held tickers from their last fill keeps P&L continuous. |
| 6 | Guard `np.linalg.cholesky` with `try/except LinAlgError` and fall back to independent draws (`self._cholesky = None`) plus a warning | Defensive. It can't happen with today's ρ values, but it might if someone edits the correlations badly. |

None of these change the public interface. #5 adds an optional parameter.
