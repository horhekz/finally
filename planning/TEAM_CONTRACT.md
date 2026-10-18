# FinAlly — Team Contract (Lead)

This is the binding interface contract for the agent team. It settles every open item in PLAN.md §13.
If you need to change anything here, message the affected teammates first and update this file.
The market data layer is done — see `backend/CLAUDE.md` and `planning/MARKET_DATA_SUMMARY.md`.

## 1. Team & file ownership

Only edit files you own. Need a change elsewhere? Message the owner.

| Member (agent name) | Owns |
|---|---|
| `frontend` | `frontend/**` |
| `backend` | `backend/app/main.py`, `backend/app/api/**` (except `chat.py`), `backend/app/services/**`, `backend/app/config.py`, `backend/pyproject.toml`, `backend/uv.lock`, `backend/tests/api/**`, `backend/tests/services/**`, `backend/tests/conftest.py` |
| `database` | `backend/app/db/**` (schema SQL, seed, lazy init, repository), `backend/tests/db/**` |
| `llm` | `backend/app/llm/**`, `backend/app/api/chat.py`, `backend/tests/llm/**` |
| `devops` | `Dockerfile`, `.dockerignore`, `scripts/**`, `db/.gitkeep`, `.gitignore`, `.env.example`, root `README.md` |
| `tester` | `test/**` (Playwright + `docker-compose.test.yml`) |

- `backend/app/market/**` is finished. Don't change it without a bug report to `backend`.
- Python dependencies: only `backend` runs `uv add` (avoids `uv.lock` conflicts). It adds `litellm`, `python-dotenv`, `pydantic` up front.
- **No git commits.** The lead handles commits.

## 2. Runtime config (env vars)

| Var | Default | Notes |
|---|---|---|
| `DB_PATH` | `<repo>/db/finally.db` locally; Docker sets `/app/db/finally.db` | parent dir auto-created |
| `STATIC_DIR` | `backend/static` locally; Docker sets `/app/static` | Next.js export output; skip mounting if missing |
| `OPENROUTER_API_KEY` | — | app still starts without it; chat returns a friendly message |
| `LLM_MOCK` | `false` | `true` → deterministic responses (§6) |
| `ENABLE_RESET` | `false` | `true` → `POST /api/reset` exists (E2E only) |
| `MASSIVE_API_KEY` (alias `POLYGON_API_KEY`), `MASSIVE_POLL_INTERVAL`, `SIM_EVENT_PROBABILITY`, `SIM_SEED` | see `.env.example` | market data |

Local dev: the backend loads the root `.env` with `python-dotenv` (`override=False`). Docker uses `--env-file .env`.

## 3. Rules settled from PLAN §13

- Tracked tickers = watchlist ∪ held positions (`MarketDataService.sync`). Call `sync` after every watchlist change and trade.
- Tickers: `normalize_ticker()`. `InvalidTickerError` → 400.
- Trading an untracked ticker: `ensure_price()`. `None` → 409 `"price not yet available, retry"`. A ticker you buy is **not** added to the watchlist automatically.
- Quantity: must be > 0. Round to 4 decimals, then reject anything ≤ 0 with 400.
- Position math: buys use a weighted average cost; sells leave `avg_cost` unchanged; |qty| < 1e-9 counts as 0 and deletes the row. Position, cash and trade log are written in **one SQLite transaction**.
- Missing price: valuation falls back to `avg_cost`; `current_price` is `null` in the API; the UI shows "—".
- Snapshots: one `record_snapshot()` helper, called at startup, every 30 s, and right after each successful trade (including LLM trades). Recorded even with no positions.
- History endpoint: last 24 h, downsampled to ≤ 500 points.
- Timestamps: UTC ISO-8601 with `Z` or `+00:00`.
- `users_profile` uses `id` as the user key (no `user_id` column). Every other table has `user_id` defaulting to `'default'`.
- Chat: exactly one LLM call per user message, no tool-calling loop. The last 20 messages are sent as history, and each assistant message in that history includes its executed `actions` JSON. Failed actions are shown next to the message in the UI. The LLM never trades without an explicit user request, but it may *add* watchlist tickers on its own.
- LLM failures (timeout, bad JSON, missing key): reply with a friendly assistant message and empty actions, store it, return 200. Never return 500.
- Error format for every 4xx: `{"error": "human readable message"}`.
- API routers are registered **before** the static mount. Static serves `index.html` at `/` plus `/_next/*`.
- Chat panel starts open and can be collapsed. Layout is desktop-first and stacks below roughly 1024px.

## 4. Python interfaces

### `app.db` (database)
Synchronous `sqlite3`. Every function takes an optional `user_id="default"`.
```python
init_db(path: str | None = None) -> None      # idempotent: create tables + seed if empty
reset_db() -> None                             # drop all user data, re-seed (cash 10000, 10 default tickers)
get_cash() -> float
list_positions() -> list[dict]                 # {ticker, quantity, avg_cost, updated_at}
get_position(ticker) -> dict | None
list_watchlist() -> list[dict]                 # {ticker, added_at}, ordered by added_at
add_watchlist(ticker) -> bool                  # False if already present
remove_watchlist(ticker) -> bool               # False if absent
apply_trade(ticker, side, quantity, price) -> dict   # atomic; returns trade row {id,ticker,side,quantity,price,executed_at}
    # raises InsufficientCashError / InsufficientSharesError (both subclass TradeError(ValueError))
insert_snapshot(total_value: float) -> None
list_snapshots(since_iso: str | None = None) -> list[dict]   # {total_value, recorded_at} ascending
insert_chat_message(role, content, actions: dict | None) -> dict   # returns stored row (actions parsed)
list_chat_messages(limit: int = 50) -> list[dict]  # ascending by created_at
```

### `app.services.portfolio` (backend)
Module-level singletons (market service and price cache) are wired up in `lifespan`.
```python
async execute_trade(ticker: str, side: str, quantity: float) -> dict   # returns trade dict; records snapshot; syncs market
    # raises TradeRejected(status_code: int, message: str)
get_portfolio() -> dict          # shape = GET /api/portfolio
async add_to_watchlist(ticker) -> dict      # raises TradeRejected-style ApiError(400/409)
async remove_from_watchlist(ticker) -> None # ApiError(404) if absent
get_watchlist() -> list[dict]    # shape = GET /api/watchlist items
record_snapshot() -> None
```
All errors subclass `ApiError(status_code, message)` in `app.services.errors`. One exception handler turns them into `{"error": ...}`.

### `app.llm` (llm)
`app/api/chat.py` exports `router` (`POST /api/chat`, `GET /api/chat/history`). `backend` includes it in `main.py`. The chat service calls `app.services.portfolio` for context and execution, and `app.db` for chat storage.

## 5. REST API

```
GET  /api/health                → {"status": "ok"}

GET  /api/portfolio             → {
  "cash_balance": 10000.0, "positions_value": 0.0, "total_value": 10000.0,
  "unrealized_pnl": 0.0, "unrealized_pnl_percent": 0.0,
  "positions": [{"ticker","quantity","avg_cost","current_price"|null,"market_value",
                 "unrealized_pnl","unrealized_pnl_percent","weight"}]   // weight = market_value/total_value
}
POST /api/portfolio/trade  {ticker, quantity, side:"buy"|"sell"}
     200 → {"trade": {id,ticker,side,quantity,price,executed_at}, "portfolio": <GET /api/portfolio>}
     400 invalid ticker/quantity/side, insufficient cash/shares · 409 no price
GET  /api/portfolio/history     → {"snapshots": [{"total_value", "recorded_at"}]}

GET  /api/watchlist             → {"tickers": [{"ticker","added_at","price"|null,"previous_price"|null,
                                     "reference_price"|null,"day_change_percent"|null,"direction"|null}]}
POST /api/watchlist {ticker}    → 201 <item> (new) · 200 <item> (already present) · 400 invalid
DELETE /api/watchlist/{ticker}  → 204 · 404 not on watchlist

POST /api/chat {message}        → {"id","role":"assistant","content","created_at",
   "actions": {"trades": [{"ticker","side","quantity","status":"executed"|"failed","price"?,"error"?}],
               "watchlist_changes": [{"ticker","action":"add"|"remove","status":"executed"|"failed","error"?}]}}
GET  /api/chat/history          → {"messages": [{"id","role","content","actions"|null,"created_at"}]}  // last 50, ascending

POST /api/reset  (only if ENABLE_RESET=true, else 404) → {"status":"reset"}  // reset DB, re-sync market, record snapshot
GET  /api/stream/prices         → SSE, see MARKET_DATA_SUMMARY.md (one event = all tickers keyed by symbol)
```

## 6. LLM mock (`LLM_MOCK=true`)

Match against the lowercased message, first match wins. Actions run through the normal execution path.

| Trigger | content | actions |
|---|---|---|
| contains `buy` | `Mock: buying 1 share of AAPL.` | trade buy AAPL 1 |
| contains `sell` | `Mock: selling 1 share of AAPL.` | trade sell AAPL 1 |
| regex `\badd ([a-z][a-z0-9.\-]*)` | `Mock: adding {T} to your watchlist.` | watchlist add T (uppercased) |
| regex `\bremove ([a-z][a-z0-9.\-]*)` | `Mock: removing {T} from your watchlist.` | watchlist remove T |
| anything else | `Mock: Hello! I'm FinAlly, your AI trading assistant.` | none |

## 7. Frontend `data-testid` contract (used by E2E)

- Header: `header-total-value`, `header-cash`, `connection-status` (attr `data-status` = `connected|reconnecting|disconnected`)
- Watchlist: `watchlist`, `watchlist-row-{TICKER}`, `price-{TICKER}` (text `—` when no price; gets class `flash-up`/`flash-down` briefly), `day-change-{TICKER}`, `sparkline-{TICKER}`, `watchlist-remove-{TICKER}`, `watchlist-add-input`, `watchlist-add-button`, `watchlist-error`
- Main chart: `main-chart` (attr `data-ticker`)
- Trade bar: `trade-ticker`, `trade-quantity`, `trade-buy`, `trade-sell`, `trade-error`
- Positions: `positions-table`, `position-row-{TICKER}`, `positions-empty`
- Heatmap: `portfolio-heatmap`, `heatmap-tile-{TICKER}` (attr `data-pnl` = `positive|negative|flat`)
- P&L chart: `pnl-chart` (attr `data-points` = number of snapshots)
- Chat: `chat-panel`, `chat-toggle`, `chat-input`, `chat-send`, `chat-loading`, `chat-message` (attr `data-role`), `chat-action` (attrs `data-status` = `executed|failed`, `data-kind` = `trade|watchlist`)

Money is shown as `$10,000.00`, i.e. `toLocaleString('en-US', {style:'currency', currency:'USD'})`.

## 8. E2E environment

`test/docker-compose.test.yml` runs the app image with `LLM_MOCK=true ENABLE_RESET=true SIM_SEED=42 SIM_EVENT_PROBABILITY=0` and a Playwright container pointed at `http://finally:8000`. Each test calls `POST /api/reset` first.
