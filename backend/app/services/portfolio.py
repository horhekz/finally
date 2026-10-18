"""Portfolio, trading and watchlist services (contract §4).

Routes and the LLM chat service call these functions; they never touch the market source
directly. The market singletons are wired up by `configure()` in the FastAPI lifespan.
"""

from __future__ import annotations

import asyncio
import logging
import math
from datetime import UTC, datetime, timedelta
from typing import Any

from app import db
from app.config import HISTORY_MAX_POINTS, HISTORY_WINDOW_HOURS
from app.market import InvalidTickerError, MarketDataService, PriceCache, normalize_ticker

from .errors import ApiError, TradeRejected

logger = logging.getLogger(__name__)

QUANTITY_DECIMALS = 4

_market: MarketDataService | None = None
_cache: PriceCache | None = None


# --- Wiring ----------------------------------------------------------------


def configure(market: MarketDataService | None, cache: PriceCache | None) -> None:
    """Install (or clear, with None) the market service and price cache singletons."""
    global _market, _cache
    _market, _cache = market, cache


def release(market: MarketDataService) -> None:
    """Clear the singletons on shutdown, unless another app instance has replaced them."""
    if _market is market:
        configure(None, None)


def get_market() -> MarketDataService:
    if _market is None:
        raise RuntimeError("Market data service is not configured")
    return _market


def get_cache() -> PriceCache:
    if _cache is None:
        raise RuntimeError("Price cache is not configured")
    return _cache


def _price(ticker: str) -> float | None:
    return _cache.get_price(ticker) if _cache is not None else None


def _normalize(raw: Any) -> str:
    try:
        return normalize_ticker(raw if isinstance(raw, str) else "")
    except InvalidTickerError as exc:
        raise ApiError(400, str(exc)) from exc


def tracked_sets() -> tuple[set[str], set[str]]:
    """(watchlist tickers, held tickers) from the DB."""
    watchlist = {w["ticker"] for w in db.list_watchlist()}
    held = {p["ticker"] for p in db.list_positions()}
    return watchlist, held


async def sync_market() -> None:
    """Make the tracked set equal watchlist ∪ held positions."""
    if _market is None:
        return
    watchlist, held = await asyncio.to_thread(tracked_sets)
    await _market.sync(watchlist, held)


# --- Portfolio -------------------------------------------------------------


def get_portfolio() -> dict[str, Any]:
    """Cash, positions valued at the latest price (avg_cost if none), totals and weights."""
    cash = db.get_cash()
    positions = []
    positions_value = 0.0
    cost_basis = 0.0
    for p in db.list_positions():
        qty, avg_cost = p["quantity"], p["avg_cost"]
        current = _price(p["ticker"])
        mark = current if current is not None else avg_cost
        market_value = qty * mark
        pnl = (mark - avg_cost) * qty
        positions_value += market_value
        cost_basis += qty * avg_cost
        positions.append(
            {
                "ticker": p["ticker"],
                "quantity": qty,
                "avg_cost": round(avg_cost, 4),
                "current_price": current,
                "market_value": round(market_value, 2),
                "unrealized_pnl": round(pnl, 2),
                "unrealized_pnl_percent": round((mark / avg_cost - 1) * 100, 4)
                if avg_cost
                else 0.0,
                "_market_value": market_value,
            }
        )
    total_value = cash + positions_value
    for pos in positions:
        raw = pos.pop("_market_value")
        pos["weight"] = round(raw / total_value, 6) if total_value > 0 else 0.0
    unrealized = positions_value - cost_basis
    return {
        "cash_balance": round(cash, 2),
        "positions_value": round(positions_value, 2),
        "total_value": round(total_value, 2),
        "unrealized_pnl": round(unrealized, 2),
        "unrealized_pnl_percent": round(unrealized / cost_basis * 100, 4) if cost_basis else 0.0,
        "positions": positions,
    }


def record_snapshot() -> None:
    """Store the current total portfolio value. The one snapshot helper."""
    db.insert_snapshot(get_portfolio()["total_value"])


def get_history() -> list[dict[str, Any]]:
    """Snapshots from the last 24 h, evenly downsampled to at most 500 points (last kept)."""
    since = (datetime.now(UTC) - timedelta(hours=HISTORY_WINDOW_HOURS)).isoformat()
    snaps = db.list_snapshots(since_iso=since)
    n = len(snaps)
    if n <= HISTORY_MAX_POINTS:
        return snaps
    step = (n - 1) / (HISTORY_MAX_POINTS - 1)
    return [snaps[round(i * step)] for i in range(HISTORY_MAX_POINTS)]


async def execute_trade(ticker: str, side: str, quantity: float) -> dict[str, Any]:
    """Validate, price and execute a market order. Raises TradeRejected (400/409)."""
    try:
        ticker = normalize_ticker(ticker if isinstance(ticker, str) else "")
    except InvalidTickerError as exc:
        raise TradeRejected(400, str(exc)) from exc
    side = side.strip().lower() if isinstance(side, str) else ""
    if side not in ("buy", "sell"):
        raise TradeRejected(400, "Side must be 'buy' or 'sell'")
    try:
        qty = float(quantity)
    except (TypeError, ValueError):
        raise TradeRejected(400, "Quantity must be a number") from None
    if not math.isfinite(qty):
        raise TradeRejected(400, "Quantity must be a finite number")
    qty = round(qty, QUANTITY_DECIMALS)
    if qty <= 0:
        raise TradeRejected(400, "Quantity must be greater than 0 (up to 4 decimals)")

    price = await get_market().ensure_price(ticker)
    if price is None:
        await sync_market()  # drop the ticker again if nobody needs it
        raise TradeRejected(409, f"Price for {ticker} not yet available, retry")

    try:
        trade = await asyncio.to_thread(db.apply_trade, ticker, side, qty, price)
    except db.TradeError as exc:
        await sync_market()
        raise TradeRejected(400, str(exc)) from exc

    await sync_market()
    try:
        await asyncio.to_thread(record_snapshot)
    except Exception:  # a snapshot failure must not undo a filled trade
        logger.exception("Snapshot after trade failed")
    return trade


# --- Watchlist -------------------------------------------------------------


def _watchlist_item(ticker: str, added_at: str) -> dict[str, Any]:
    update = _cache.get(ticker) if _cache is not None else None
    return {
        "ticker": ticker,
        "added_at": added_at,
        "price": update.price if update else None,
        "previous_price": update.previous_price if update else None,
        "reference_price": update.reference_price if update else None,
        "day_change_percent": update.day_change_percent if update else None,
        "direction": update.direction if update else None,
    }


def get_watchlist() -> list[dict[str, Any]]:
    return [_watchlist_item(w["ticker"], w["added_at"]) for w in db.list_watchlist()]


async def add_watchlist_ticker(ticker: str) -> tuple[dict[str, Any], bool]:
    """Add a ticker; returns (item, created). created is False if it was already present."""
    ticker = _normalize(ticker)
    created = await asyncio.to_thread(db.add_watchlist, ticker)
    if created:
        await sync_market()
    for w in await asyncio.to_thread(db.list_watchlist):
        if w["ticker"] == ticker:
            return _watchlist_item(ticker, w["added_at"]), created
    raise ApiError(409, f"{ticker} could not be added to the watchlist")  # pragma: no cover


async def add_to_watchlist(ticker: str) -> dict[str, Any]:
    """Add a ticker (idempotent). Raises ApiError(400) for an invalid symbol."""
    item, _ = await add_watchlist_ticker(ticker)
    return item


async def remove_from_watchlist(ticker: str) -> None:
    """Remove a ticker. Raises ApiError(404) if it is not on the watchlist."""
    ticker = _normalize(ticker)
    removed = await asyncio.to_thread(db.remove_watchlist, ticker)
    if not removed:
        raise ApiError(404, f"{ticker} is not on the watchlist")
    await sync_market()


# --- Reset (E2E only) ------------------------------------------------------


async def reset() -> None:
    """Drop all user data, re-seed, re-sync the market and record a fresh snapshot."""
    await asyncio.to_thread(db.reset_db)
    await sync_market()
    await asyncio.to_thread(record_snapshot)
