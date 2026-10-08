# Massive API Reference (formerly Polygon.io)

Research notes on the Massive REST API for getting **real-time** and **end-of-day (EOD)** stock prices for **many tickers at once**. The unified interface built on top of this API is in `MARKET_INTERFACE.md`.

*Researched 2026-10-06 against massive.com docs and `massive` Python client v2.2.0 (the version locked in `backend/uv.lock`).*

---

## 1. Overview

| Item | Value |
|---|---|
| Company | Massive.com. Polygon.io rebranded on **2025-10-30**. |
| Base URL | `https://api.massive.com` (the legacy `https://api.polygon.io` still works for now) |
| Python package | `massive` (`uv add massive`), Python ≥ 3.9. It replaces `polygon-api-client`. |
| Auth | API key. Send it as `Authorization: Bearer <KEY>` or as the `?apiKey=<KEY>` query param. The Python client does this for you. |
| Env var | The client reads `MASSIVE_API_KEY` if no `api_key` is passed. |
| Ticker case | **Case-sensitive.** Always send uppercase (`AAPL`, not `aapl`). |
| Timestamps | Unix epoch. **The units differ by field**: see §6. |
| LLM-friendly docs | `https://massive.com/llms.txt` lists every endpoint. Add `.md` to a docs URL to get markdown. |

---

## 2. Plans, Data Freshness, and Rate Limits

This is the most important constraint for FinAlly. **Which endpoints you can call depends on the plan.**

| Plan (individual) | Price | Rate limit | Freshness | Snapshot endpoints? |
|---|---|---|---|---|
| **Stocks Basic** | $0 | **5 req/min** | **End-of-day only** | **No (403)** |
| Stocks Starter | $29/mo | Unlimited* | 15-min delayed | Yes |
| Stocks Developer | $79/mo | Unlimited* | 15-min delayed | Yes |
| Stocks Advanced | $199/mo | Unlimited* | **Real-time** | Yes |
| Stocks Business | custom | Unlimited* | Real-time (+ FMV field) | Yes |

\* "Unlimited" means no hard per-minute cap. Massive asks you to stay under ~100 req/s.

Endpoint availability per plan:

| Endpoint | Basic | Starter / Developer | Advanced / Business |
|---|---|---|---|
| Full Market Snapshot `/v2/snapshot/.../tickers` | ✗ | ✓ 15-min delayed | ✓ real-time |
| Single Ticker Snapshot | ✗ | ✓ delayed | ✓ real-time |
| Unified Snapshot `/v3/snapshot` | ✗ | ✓ delayed | ✓ real-time |
| Daily Market Summary (grouped daily) | ✓ EOD | ✓ delayed | ✓ real-time |
| Previous Day Bar | ✓ EOD | ✓ | ✓ |
| Daily Ticker Summary (open/close) | ✓ EOD | ✓ | ✓ |
| Custom Bars (aggregates) | ✓ 2y history | ✓ 5y/10y | ✓ 20y+ |
| Last Trade / Last Quote | ✗ | Developer+ (trades) | ✓ |

> **Implication:** A free (Basic) key **cannot** stream intraday prices. With a free key, the best we can do is the last close for every ticker, which updates once per trading day. Live-moving prices from Massive need Starter or higher (delayed) or Advanced (real-time). `MARKET_INTERFACE.md` §5 describes how the backend handles both cases.

---

## 3. Python Client Basics

```python
from massive import RESTClient

client = RESTClient()                      # reads MASSIVE_API_KEY from the environment
client = RESTClient(api_key="...")         # or pass it explicitly
client = RESTClient(api_key="...", trace=True, verbose=True)   # log URLs and headers for debugging
client = RESTClient(api_key="...", pagination=False)           # don't auto-follow next_url
```

Facts about the client:

- **It is synchronous** (built on `urllib3`). In FastAPI, call it through `await asyncio.to_thread(...)` so it doesn't block the event loop.
- **It retries automatically** on HTTP `413, 429, 499, 500, 502, 503, 504`, with exponential backoff (factor 0.1).
- **Errors**:
  - `massive.exceptions.AuthError` is raised **in the constructor** if there's no key.
  - `massive.exceptions.BadResponse` is raised on any non-200 response once retries run out. The message has the JSON body, e.g. `{"status":"NOT_AUTHORIZED","message":"You are not entitled to this data..."}`.
- `list_*` methods return **iterators** that page for you. `get_*` methods return a model or a list.
- Every method accepts `raw=True`, which returns the `urllib3` `HTTPResponse` instead of parsed models. This is handy for debugging the JSON.
- Models are dataclasses with **snake_case** attributes, mapped from the terse JSON keys (`p` → `price`, `prevDay` → `prev_day`, and so on).

---

## 4. Endpoints for Many Tickers

### 4.1 Full Market Snapshot: real-time/delayed, many tickers, ONE call ⭐

This is the main polling endpoint for paid plans. It returns the latest trade, the latest quote, today's bar, the current minute bar, and the previous day's bar for every ticker you ask for, all in one request.

**REST**

```
GET /v2/snapshot/locale/us/markets/stocks/tickers?tickers=AAPL,MSFT,NVDA
```

| Param | Type | Notes |
|---|---|---|
| `tickers` | CSV string | Case-sensitive. **If you leave it out, you get the whole market (~10k tickers).** Always pass it. |
| `include_otc` | bool | Defaults to `false`. |

**Python**

```python
from massive import RESTClient
from massive.rest.models import SnapshotMarketType

client = RESTClient()
snapshots = client.get_snapshot_all(
    market_type=SnapshotMarketType.STOCKS,      # or "stocks"
    tickers=["AAPL", "MSFT", "NVDA"],           # list or CSV string
)

for s in snapshots:                              # list[TickerSnapshot]
    trade_price = s.last_trade.price if s.last_trade else None
    print(
        s.ticker,
        trade_price,                             # last trade price
        s.prev_day.close if s.prev_day else None,# yesterday's close
        s.todays_change, s.todays_change_percent,
        s.updated,                               # NANOSECONDS
    )
```

**Response (per ticker, raw JSON)**

```json
{
  "ticker": "AAPL",
  "todaysChange": 0.98,
  "todaysChangePerc": 0.82,
  "updated": 1605195918306274000,
  "day":     {"o": 119.62, "h": 120.53, "l": 118.81, "c": 120.4229, "v": 28727868, "vw": 119.725},
  "prevDay": {"o": 117.19, "h": 119.63, "l": 116.44, "c": 119.49,  "v": 110597265, "vw": 118.4998},
  "min":     {"o": 120.435, "h": 120.468, "l": 120.37, "c": 120.4201, "v": 270796, "av": 28724441, "n": 762, "t": 1684428720000},
  "lastTrade": {"p": 120.47, "s": 236, "x": 10, "c": [14, 41], "i": "4046", "t": 1605195918306274000},
  "lastQuote": {"p": 120.46, "s": 8, "P": 120.47, "S": 4, "t": 1605195918507251700}
}
```

Envelope: `{"status": "OK", "count": N, "tickers": [ ...as above... ]}`.

**Python model attribute map (`TickerSnapshot`)**

| JSON | Attribute | Type / notes |
|---|---|---|
| `ticker` | `ticker` | str |
| `lastTrade` | `last_trade` | `LastTrade`: `.price`, `.size`, `.exchange`, `.conditions`, `.id`, **`.sip_timestamp` (ns)** |
| `lastQuote` | `last_quote` | `LastQuote`: `.bid_price` (`p`), `.bid_size`, `.ask_price` (`P`), `.ask_size`, `.sip_timestamp` (ns) |
| `day` | `day` | `Agg`: `.open .high .low .close .volume .vwap` |
| `prevDay` | `prev_day` | `Agg`: `.close` is yesterday's close |
| `min` | `min` | `MinuteSnapshot`: `.close`, `.accumulated_volume`, `.timestamp` (ms) |
| `todaysChange` | `todays_change` | float, vs `prevDay.c` |
| `todaysChangePerc` | `todays_change_percent` | float, percent |
| `updated` | `updated` | int, **nanoseconds** |

> ⚠️ **There is no `last_trade.timestamp` attribute.** The trade time is `last_trade.sip_timestamp`, and it is in **nanoseconds**. To get seconds, divide by `1e9`, not `1000`.

**Behaviour notes**

- Snapshot data is **cleared at 3:30 AM ET** and fills up again from about 4:00 AM ET as pre-market trades arrive. Between those times, `day` can be all zeros and `last_trade` can be missing. Fall back to `min.close`, then `prev_day.close`.
- A ticker with no data (unknown symbol, or no trades yet) is **left out of the list**. It does not appear as a null entry.
- The `tickers` value goes into the URL. For more than about 200 symbols, split the list into chunks (e.g., 100 per call) so the URL stays a sane length.

### 4.2 Unified Snapshot (v3): up to 250 tickers, multi-asset

A newer endpoint that also covers options, FX, crypto, and indices, and has a cleaner `session` object. It's also paid-only.

```
GET /v3/snapshot?ticker.any_of=AAPL,MSFT,NVDA&limit=250
```

```python
for s in client.list_universal_snapshots(
    ticker_any_of=["AAPL", "MSFT", "NVDA"],
    limit=250,
):
    # s.session.price, s.session.change_percent, s.session.previous_close
    # s.last_trade.price, s.market_status ("open", "closed", "early_trading", "late_trading")
    print(s.ticker, s.session.price, s.session.previous_close, s.market_status)
```

- At most **250 tickers per request** (`ticker.any_of`). `limit` defaults to 10, so **set `limit`** or you'll only get 10 rows back.
- If a ticker fails, its entry has `error` and `message` set instead of failing the whole response.
- `session.previous_close` and `session.change_percent` are handy for "daily change %".
- FinAlly sticks with v2 (§4.1) because it's already integrated. v3 is a fine alternative.

### 4.3 Daily Market Summary (Grouped Daily): EOD for ALL tickers, ONE call ⭐ (free tier)

This returns the OHLCV bar for **every US stock** on a given date in a single call. It works on **every plan**, including Basic. It's the right EOD endpoint for many tickers: 1 call instead of N.

```
GET /v2/aggs/grouped/locale/us/market/stocks/{date}?adjusted=true
```

```python
bars = client.get_grouped_daily_aggs(date="2026-10-05", adjusted=True)   # list[GroupedDailyAgg]

wanted = {"AAPL", "MSFT", "NVDA"}
closes = {b.ticker: b.close for b in bars if b.ticker in wanted}
# {'AAPL': 231.4, 'MSFT': 437.1, 'NVDA': 126.8}
```

Each result (`GroupedDailyAgg`) has `ticker` (`T`), `open` (`o`), `high` (`h`), `low` (`l`), `close` (`c`), `volume` (`v`), `vwap` (`vw`), `transactions` (`n`), and `timestamp` (`t`, **ms**, the start of the day).

- On a weekend, a holiday, or before the day's data is out, **`results` is empty**. Walk back one day at a time until you get rows (cap at ~7 tries).
- The response is ~10k rows (≈1–2 MB). That's fine once per refresh, but don't call it every few seconds.
- On Basic the data is end-of-day. On paid plans the same endpoint returns today's partial bar (delayed or real-time).

### 4.4 Previous Day Bar: per ticker

```
GET /v2/aggs/ticker/{ticker}/prev?adjusted=true
```

```python
prev = client.get_previous_close_agg(ticker="AAPL")
# The type hint says PreviousCloseAgg, but some client versions return a list of them.
# Handle both. Fields: .ticker .open .high .low .close .volume .vwap .timestamp (ms)
bars = prev if isinstance(prev, list) else [prev]
for bar in bars:
    print(bar.ticker, bar.close)
```

This is one call **per ticker**. With 10 tickers on the free tier (5/min), that's 2 minutes of quota. Use Grouped Daily (§4.3) for many tickers.

### 4.5 Daily Ticker Summary (Open/Close): per ticker, per date

```
GET /v1/open-close/{ticker}/{date}?adjusted=true
```

```python
oc = client.get_daily_open_close_agg(ticker="AAPL", date="2026-10-05")
print(oc.open, oc.high, oc.low, oc.close, oc.pre_market, oc.after_hours, oc.volume)
```

This one adds pre-market and after-hours prices. It's also one call per ticker.

### 4.6 Custom Bars (Aggregates): history for charts

```
GET /v2/aggs/ticker/{ticker}/range/{multiplier}/{timespan}/{from}/{to}
```

```python
# 30 daily bars (works on Basic, 2 years of history)
bars = client.get_aggs("AAPL", 1, "day", "2026-08-25", "2026-10-05", adjusted=True, limit=5000)
# Intraday 1-minute bars (paid plans for recent data); list_aggs auto-paginates
for b in client.list_aggs("AAPL", 1, "minute", "2026-10-05", "2026-10-05", limit=50000):
    print(b.timestamp, b.close)   # timestamp in ms
```

`timespan` is one of `second | minute | hour | day | week | month | quarter | year`. FinAlly doesn't need history today (charts build up from SSE), but this is the endpoint for a future "load 1D chart" feature.

### 4.7 Last Trade / Last Quote: per ticker

```python
t = client.get_last_trade("AAPL")   # GET /v2/last/trade/AAPL  -> .price .size .sip_timestamp (ns)
q = client.get_last_quote("AAPL")   # GET /v2/last/nbbo/AAPL   -> .bid_price .ask_price .sip_timestamp (ns)
```

These are one call per ticker, and you need a plan with trades/quotes. **Don't use them for polling many tickers.** The snapshot already includes both.

---

## 5. Raw HTTP (no SDK)

This is useful in tests or if we ever drop the SDK. The example uses `httpx`, which already comes in through FastAPI/uvicorn tooling.

```python
import os, httpx

BASE = "https://api.massive.com"
HEADERS = {"Authorization": f"Bearer {os.environ['MASSIVE_API_KEY']}"}

async def fetch_snapshots(tickers: list[str]) -> dict[str, float]:
    async with httpx.AsyncClient(base_url=BASE, headers=HEADERS, timeout=10) as http:
        r = await http.get(
            "/v2/snapshot/locale/us/markets/stocks/tickers",
            params={"tickers": ",".join(t.upper() for t in tickers)},
        )
        r.raise_for_status()                    # 403 on Basic plan
        out = {}
        for t in r.json().get("tickers", []):
            price = (t.get("lastTrade") or {}).get("p") \
                 or (t.get("min") or {}).get("c") \
                 or (t.get("prevDay") or {}).get("c")
            if price:
                out[t["ticker"]] = float(price)
        return out

async def fetch_grouped_daily(date: str) -> dict[str, float]:
    async with httpx.AsyncClient(base_url=BASE, headers=HEADERS, timeout=30) as http:
        r = await http.get(f"/v2/aggs/grouped/locale/us/market/stocks/{date}",
                           params={"adjusted": "true"})
        r.raise_for_status()
        return {row["T"]: row["c"] for row in r.json().get("results") or []}
```

```bash
curl -H "Authorization: Bearer $MASSIVE_API_KEY" \
  "https://api.massive.com/v2/snapshot/locale/us/markets/stocks/tickers?tickers=AAPL,MSFT"

curl "https://api.massive.com/v2/aggs/grouped/locale/us/market/stocks/2026-10-05?adjusted=true&apiKey=$MASSIVE_API_KEY"
```

---

## 6. Timestamp Units (a common bug)

| Field | Unit | To seconds |
|---|---|---|
| Snapshot `updated` | **ns** | `/ 1e9` |
| Snapshot `lastTrade.t` → `last_trade.sip_timestamp` | **ns** | `/ 1e9` |
| Snapshot `lastQuote.t` → `last_quote.sip_timestamp` | **ns** | `/ 1e9` |
| Snapshot `min.t` → `min.timestamp` | ms | `/ 1e3` |
| Aggregates / grouped / prev `t` → `.timestamp` | ms | `/ 1e3` |

A unit-safe helper:

```python
def to_epoch_seconds(ts: int | float | None) -> float | None:
    """Normalise a Massive timestamp (s, ms, µs or ns) to float seconds by magnitude."""
    if not ts:
        return None
    ts = float(ts)
    if ts > 1e17:      # nanoseconds  (~1.7e18 today)
        return ts / 1e9
    if ts > 1e14:      # microseconds (~1.7e15)
        return ts / 1e6
    if ts > 1e11:      # milliseconds (~1.7e12)
        return ts / 1e3
    return ts          # already seconds (~1.7e9)
```

---

## 7. Errors

| HTTP | `status` in body | Meaning | What to do |
|---|---|---|---|
| 401 | `ERROR` | Missing or invalid key | Log once and keep serving the last cached prices. Check the key. |
| 403 | `NOT_AUTHORIZED` | The plan doesn't include this endpoint (e.g., a snapshot on Basic) | **Switch to EOD mode** (grouped daily). |
| 429 | `ERROR` | Rate limit hit (Basic: 5/min) | The client retries. Make the poll interval longer. |
| 5xx | — | Server problem | The client retries. The next poll tries again. |
| 200, empty `tickers`/`results` | `OK` / `DELAYED` | Unknown tickers, weekend/holiday, or pre-4 AM | Keep the old cache values. For grouped daily, walk back a day. |

```python
from massive.exceptions import AuthError, BadResponse

try:
    snaps = client.get_snapshot_all("stocks", tickers=["AAPL"])
except BadResponse as e:
    if "NOT_AUTHORIZED" in str(e):
        ...  # free plan → fall back to get_grouped_daily_aggs
    else:
        raise
```

---

## 8. Recommended Usage in FinAlly

| Plan detected | Endpoint | Calls per cycle | Poll interval | What the UI shows |
|---|---|---|---|---|
| Paid (snapshot works) | `get_snapshot_all(tickers=watched)` | 1 | 15 s by default; 2–5 s on unlimited plans | Live (or 15-min delayed) moving prices |
| Basic (snapshot → 403) | `get_grouped_daily_aggs(last trading day)` | 1 | 15–60 min (the data changes once a day) | Last close. Prices don't tick. |

For both: **one call per cycle for all tickers**, run with `asyncio.to_thread`, swallow and log errors, never crash the loop, and keep the last good price in the cache.

Reference prices for "daily change %":
- Snapshot: `prev_day.close`, or the pre-computed `todays_change_percent`.
- Grouped daily: the previous trading day's `close` (fetch two days), or use `open` from the same bar.

---

## 9. Findings Against the Current Code (`backend/app/market/massive_client.py`)

> **Status (2026-10-06):** Items 1–3 are **fixed** in `massive_client.py`, and the tests now build real `TickerSnapshot` / `GroupedDailyAgg` models. Item 4 (reference price) is still open; see `MARKET_INTERFACE.md` §11.

1. **Wrong timestamp attribute and unit.** The code reads `snap.last_trade.timestamp / 1000.0`. The `LastTrade` model has **no `timestamp` attribute**. It's `sip_timestamp`, in **nanoseconds**. With the real client this raises `AttributeError` for every ticker, so every snapshot gets skipped and the cache never fills. The unit tests miss this because they build `MagicMock` snapshots, which accept any attribute. **Fix:** `ts = snap.last_trade.sip_timestamp / 1e9`, or use `snap.updated / 1e9`, and fall back to `time.time()`. Tests should build real `TickerSnapshot.from_dict({...})` objects from the JSON sample in §4.1.
2. **A free (Basic) key gets 403 on the snapshot endpoint.** The code logs the error every 15 s and never produces a price. **Fix:** detect `NOT_AUTHORIZED` and fall back to the grouped daily EOD mode (see `MARKET_INTERFACE.md` §5).
3. **No fallback when `last_trade` is missing** (3:30–4:00 AM ET, or an illiquid name). **Fix:** use `last_trade.price` → `min.close` → `day.close` → `prev_day.close`.
4. **`prev_day.close` is thrown away.** It's the natural reference for the "daily change %" column (PLAN §13.1 item 4).

---

## Sources

- Endpoint index: <https://massive.com/llms.txt>
- Full Market Snapshot: <https://massive.com/docs/rest/stocks/snapshots/full-market-snapshot>
- Single Ticker Snapshot: <https://massive.com/docs/rest/stocks/snapshots/single-ticker-snapshot>
- Unified Snapshot: <https://massive.com/docs/rest/stocks/snapshots/unified-snapshot>
- Daily Market Summary: <https://massive.com/docs/rest/stocks/aggregates/daily-market-summary>
- Previous Day Bar: <https://massive.com/docs/rest/stocks/aggregates/previous-day-bar>
- Daily Ticker Summary: <https://massive.com/docs/rest/stocks/aggregates/daily-ticker-summary>
- Python client: <https://github.com/massive-com/client-python> (`massive/rest/snapshot.py`, `aggs.py`, `models/`, `base.py`)
- Plan pricing summary: <https://apicostcalc.com/polygon.html>, <https://dataglobehub.com/api-finder/massive-api/>
