"""FastAPI application: API routers, SSE stream, background tasks and the static frontend.

Run with: uv run uvicorn app.main:app --port 8000
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from app import config, db
from app.api import chat, health, reset
from app.api import portfolio as portfolio_api
from app.api import watchlist as watchlist_api
from app.market import (
    InvalidTickerError,
    MarketDataService,
    MarketDataSource,
    PriceCache,
    create_market_data_source,
    create_stream_router,
)
from app.services import portfolio
from app.services.errors import ApiError

logger = logging.getLogger(__name__)

SourceFactory = Callable[[PriceCache], MarketDataSource]


async def _snapshot_loop(interval: float) -> None:
    while True:
        await asyncio.sleep(interval)
        try:
            await asyncio.to_thread(portfolio.record_snapshot)
        except Exception:
            logger.exception("Periodic portfolio snapshot failed")


def _error(status_code: int, message: str) -> JSONResponse:
    return JSONResponse({"error": message}, status_code=status_code)


def _validation_message(exc: RequestValidationError) -> str:
    parts = []
    for err in exc.errors():
        loc = ".".join(str(p) for p in err.get("loc", ()) if p != "body")
        parts.append(f"{loc}: {err.get('msg')}" if loc else str(err.get("msg")))
    return "; ".join(parts) or "Invalid request"


def create_app(
    source_factory: SourceFactory = create_market_data_source,
    snapshot_interval: float = config.SNAPSHOT_INTERVAL,
) -> FastAPI:
    """Build the app. Env-dependent parts (reset route, static dir) are read here."""
    price_cache = PriceCache()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        await asyncio.to_thread(db.init_db, config.db_path())
        market = MarketDataService(source_factory(price_cache), price_cache)
        portfolio.configure(market, price_cache)
        positions = await asyncio.to_thread(db.list_positions)
        watchlist = {w["ticker"] for w in await asyncio.to_thread(db.list_watchlist)}
        await market.start(
            watchlist=watchlist,
            held={p["ticker"] for p in positions},
            initial_prices={p["ticker"]: p["avg_cost"] for p in positions},
        )
        await asyncio.to_thread(portfolio.record_snapshot)
        task = asyncio.create_task(_snapshot_loop(snapshot_interval), name="snapshot-loop")
        try:
            yield
        finally:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
            await market.stop()
            portfolio.release(market)

    app = FastAPI(title="FinAlly", lifespan=lifespan)
    app.state.price_cache = price_cache

    @app.exception_handler(ApiError)
    async def _api_error(_: Request, exc: ApiError) -> JSONResponse:
        return _error(exc.status_code, exc.message)

    @app.exception_handler(InvalidTickerError)
    async def _invalid_ticker(_: Request, exc: InvalidTickerError) -> JSONResponse:
        return _error(400, str(exc))

    @app.exception_handler(RequestValidationError)
    async def _invalid_request(_: Request, exc: RequestValidationError) -> JSONResponse:
        return _error(400, _validation_message(exc))

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        return JSONResponse(
            {"error": str(exc.detail)}, status_code=exc.status_code, headers=exc.headers
        )

    # API routes first: the static mount at "/" would otherwise swallow /api/*.
    app.include_router(health.router)
    app.include_router(portfolio_api.router)
    app.include_router(watchlist_api.router)
    app.include_router(create_stream_router(price_cache))
    app.include_router(chat.router)
    if config.enable_reset():
        app.include_router(reset.router)

    static = config.static_dir()
    if static.is_dir():
        app.mount("/", StaticFiles(directory=static, html=True), name="static")
    else:
        logger.info("Static dir %s not found; serving the API only", static)

    return app


app = create_app()
