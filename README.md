# FinAlly — AI Trading Workstation

A visually stunning AI-powered trading workstation that streams live market data, simulates portfolio trading, and integrates an LLM chat assistant that can analyze positions and execute trades via natural language.

Built entirely by coding agents as a capstone project for an agentic AI coding course.

## Features

- **Live price streaming** via SSE with green/red flash animations
- **Simulated portfolio** — $10k virtual cash, market orders, instant fills
- **Portfolio visualizations** — heatmap (treemap), P&L chart, positions table
- **AI chat assistant** — analyzes holdings, suggests and auto-executes trades
- **Watchlist management** — track tickers manually or via AI
- **Dark terminal aesthetic** — Bloomberg-inspired, data-dense layout

## Architecture

Single Docker container serving everything on port 8000:

- **Frontend**: Next.js (static export) with TypeScript and Tailwind CSS
- **Backend**: FastAPI (Python/uv) with SSE streaming
- **Database**: SQLite with lazy initialization (`db/finally.db`, persisted in the `finally-data` volume)
- **AI**: LiteLLM → OpenRouter (Cerebras inference) with structured outputs
- **Market data**: Built-in GBM simulator (default) or Massive API (optional)

## Quick Start (Docker)

Requires Docker (Docker Desktop on macOS/Windows).

```bash
cp .env.example .env          # then set OPENROUTER_API_KEY (or LLM_MOCK=true)

./scripts/start_mac.sh        # macOS / Linux
.\scripts\start_windows.ps1   # Windows PowerShell
```

Open http://localhost:8000. The start script builds the `finally` image if it's missing, replaces any old `finally` container, mounts the `finally-data` volume and opens the browser. If `.env` is missing it is created from `.env.example`.

| Script | Options |
|---|---|
| `scripts/start_mac.sh` | `--build` force rebuild · `--no-open` don't open the browser · `PORT=9000` env to change the host port |
| `scripts/start_windows.ps1` | `-Build` · `-NoOpen` · `-Port 9000` |
| `scripts/stop_mac.sh` / `scripts/stop_windows.ps1` | Stops and removes the container; the data volume is kept |

All scripts are idempotent. To wipe your portfolio completely: `docker volume rm finally-data`.

Manual equivalent:

```bash
docker build -t finally .
docker run -d --name finally -v finally-data:/app/db -p 8000:8000 --env-file .env finally
```

## Local Development

Backend (Python 3.12, [uv](https://docs.astral.sh/uv/)) — reads the root `.env` automatically:

```bash
cd backend
uv sync --extra dev
uv run uvicorn app.main:app --reload --port 8000
```

Frontend (Node 20) — run the Next.js dev server alongside the backend:

```bash
cd frontend
npm install
npm run dev
```

To serve the built UI from the backend instead (single origin, like Docker):

```bash
cd frontend && npm run build && cp -r out ../backend/static
```

## Environment Variables

| Variable | Default | Description |
|---|---|---|
| `OPENROUTER_API_KEY` | — | OpenRouter key for AI chat. The app starts without it; chat replies with a friendly error |
| `LLM_MOCK` | `false` | `true` → deterministic mock LLM responses (tests, no key needed) |
| `MASSIVE_API_KEY` | empty | Massive (Polygon.io) key for real market data; empty → built-in simulator |
| `MASSIVE_POLL_INTERVAL` | `15` | Massive poll interval in seconds |
| `SIM_EVENT_PROBABILITY` | `0.001` | Simulator per-tick chance of a 2–5% jump |
| `SIM_SEED` | empty | Integer seed for a reproducible simulator path |
| `ENABLE_RESET` | `false` | `true` exposes `POST /api/reset` (E2E tests only) |
| `DB_PATH` | `<repo>/db/finally.db` | SQLite path; Docker sets `/app/db/finally.db` |
| `STATIC_DIR` | `backend/static` | Static frontend export; Docker sets `/app/static` |

## Tests

```bash
# Backend unit tests
cd backend && uv run --extra dev pytest

# Frontend unit tests
cd frontend && npm test

# End-to-end (Playwright, runs against the Docker image with LLM_MOCK=true)
docker compose -f test/docker-compose.test.yml up --build --abort-on-container-exit --exit-code-from playwright
```

## Project Structure

```
finally/
├── frontend/    # Next.js static export
├── backend/     # FastAPI uv project
├── planning/    # Project documentation and agent contracts
├── test/        # Playwright E2E tests
├── db/          # SQLite volume mount (runtime)
├── scripts/     # Start/stop helpers
└── Dockerfile   # Multi-stage build (Node → Python)
```

## License

See [LICENSE](LICENSE).
