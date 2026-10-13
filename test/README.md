# FinAlly E2E tests

Playwright (TypeScript) end-to-end tests. They drive the real app through the `data-testid`
contract in `planning/TEAM_CONTRACT.md` §7 and the mock LLM triggers in §6.

The app must run with:

| Var | Value | Why |
|---|---|---|
| `LLM_MOCK` | `true` | deterministic chat replies |
| `ENABLE_RESET` | `true` | every test calls `POST /api/reset` in `beforeEach` |
| `SIM_SEED` | `42` | reproducible simulator |
| `SIM_EVENT_PROBABILITY` | `0` | no random price jumps |

Tests run serially (`workers: 1`) because they share one server and one database.

## Option 1: Docker Compose (recommended)

From the repo root:

```bash
docker compose -f test/docker-compose.test.yml up --build --abort-on-container-exit --exit-code-from playwright
docker compose -f test/docker-compose.test.yml down
```

This builds the app from the root `Dockerfile` (fresh DB, no volume), waits for `/api/health`,
then runs the suite in `mcr.microsoft.com/playwright:v1.64.0-noble` against `http://finally:8000`.
The HTML report is written to `test/playwright-report/`.

The image tag must match the `@playwright/test` version in `package.json`.

## Option 2: Locally against a running server

Start the backend with the env above (and the built frontend in `backend/static`):

```bash
cd backend
LLM_MOCK=true ENABLE_RESET=true SIM_SEED=42 SIM_EVENT_PROBABILITY=0 MASSIVE_API_KEY= \
  uv run uvicorn app.main:app --port 8000
```

Then:

```bash
cd test
npm ci
npx playwright install chromium
npx playwright test                       # BASE_URL defaults to http://localhost:8000
BASE_URL=http://localhost:8001 npx playwright test   # another server
npx playwright show-report
```

## Scenarios

| Spec | Covers |
|---|---|
| `fresh-start.spec.ts` | 10 default tickers, `$10,000.00` cash/total, prices stream and change, connection status `connected`, ticker click selects main chart |
| `watchlist.spec.ts` | add a ticker (streams a price, persists after reload), remove a ticker, invalid ticker error |
| `trading.spec.ts` | buy (cash drops, position row, heatmap tile), sell partial then full, insufficient shares/cash show `trade-error`, P&L chart points |
| `chat.spec.ts` | mock greeting, "buy" executes a trade inline and the position appears, failed "sell" action, "add" watchlist action, history after reload |

SSE reconnection is not tested end-to-end (PLAN §13.3 #7). Only the status indicator is asserted.
