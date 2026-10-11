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
        """One SSE event with every tracked ticker, sent whenever the cache changes."""
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
    """Serialise the whole cache as one SSE `data:` frame."""
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
