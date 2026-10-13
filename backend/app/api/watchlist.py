"""Watchlist endpoints."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.services import portfolio

router = APIRouter(prefix="/api/watchlist", tags=["watchlist"])


class WatchlistAdd(BaseModel):
    ticker: Any = None


@router.get("")
def get_watchlist() -> dict:
    return {"tickers": portfolio.get_watchlist()}


@router.post("")
async def add(body: WatchlistAdd) -> JSONResponse:
    item, created = await portfolio.add_watchlist_ticker(body.ticker)
    return JSONResponse(item, status_code=201 if created else 200)


@router.delete("/{ticker}", status_code=204)
async def remove(ticker: str) -> Response:
    await portfolio.remove_from_watchlist(ticker)
    return Response(status_code=204)
