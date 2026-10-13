"""Portfolio endpoints: valuation, trades, history."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel

from app.services import portfolio

router = APIRouter(prefix="/api/portfolio", tags=["portfolio"])


class TradeRequest(BaseModel):
    # Loose types on purpose: the service validates and answers {"error": ...} with 400.
    ticker: Any = None
    quantity: Any = None
    side: Any = None


@router.get("")
def get_portfolio() -> dict:
    return portfolio.get_portfolio()


@router.post("/trade")
async def trade(body: TradeRequest) -> dict:
    executed = await portfolio.execute_trade(body.ticker, body.side, body.quantity)
    return {"trade": executed, "portfolio": portfolio.get_portfolio()}


@router.get("/history")
def history() -> dict:
    return {"snapshots": portfolio.get_history()}
