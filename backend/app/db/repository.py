"""Repository API for FinAlly (contract §4). Synchronous, one connection per call."""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from .connection import (
    DEFAULT_CASH,
    DEFAULT_USER,
    _seed,
    connect,
    new_id,
    now_iso,
    to_utc_iso,
    transaction,
)

# Quantities smaller than this are floating-point leftovers and count as zero.
QTY_EPSILON = 1e-9
# Tolerance for cash comparisons so 10000.0 can buy exactly $10,000 of stock.
CASH_EPSILON = 1e-6

_USER_TABLES = ("watchlist", "positions", "trades", "portfolio_snapshots", "chat_messages")


def _fmt_qty(q: float) -> str:
    """1000000 -> '1,000,000'; 0.12345 -> '0.1235' (max 4 decimals, never an exponent)."""
    return f"{q:,.4f}".rstrip("0").rstrip(".")


class TradeError(ValueError):
    """A trade was rejected; nothing was written."""


class InsufficientCashError(TradeError):
    """A buy costs more than the available cash."""


class InsufficientSharesError(TradeError):
    """A sell is for more shares than are held."""


# --- Profile ---------------------------------------------------------------


def get_cash(user_id: str = DEFAULT_USER) -> float:
    with connect() as conn:
        row = conn.execute(
            "SELECT cash_balance FROM users_profile WHERE id = ?", (user_id,)
        ).fetchone()
    if row is not None:
        return float(row["cash_balance"])
    with transaction() as conn:
        _seed(conn, user_id)
    return DEFAULT_CASH


def reset_db(user_id: str = DEFAULT_USER) -> None:
    """Drop all of the user's data and re-seed ($10,000 cash, default watchlist)."""
    with transaction() as conn:
        for table in _USER_TABLES:
            conn.execute(f"DELETE FROM {table} WHERE user_id = ?", (user_id,))
        conn.execute("DELETE FROM users_profile WHERE id = ?", (user_id,))
        _seed(conn, user_id)


# --- Positions -------------------------------------------------------------


def _position_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "ticker": row["ticker"],
        "quantity": float(row["quantity"]),
        "avg_cost": float(row["avg_cost"]),
        "updated_at": row["updated_at"],
    }


def list_positions(user_id: str = DEFAULT_USER) -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT ticker, quantity, avg_cost, updated_at FROM positions "
            "WHERE user_id = ? ORDER BY ticker",
            (user_id,),
        ).fetchall()
    return [_position_dict(r) for r in rows]


def get_position(ticker: str, user_id: str = DEFAULT_USER) -> dict[str, Any] | None:
    with connect() as conn:
        row = conn.execute(
            "SELECT ticker, quantity, avg_cost, updated_at FROM positions "
            "WHERE user_id = ? AND ticker = ?",
            (user_id, ticker),
        ).fetchone()
    return _position_dict(row) if row else None


# --- Watchlist -------------------------------------------------------------


def list_watchlist(user_id: str = DEFAULT_USER) -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT ticker, added_at FROM watchlist WHERE user_id = ? ORDER BY added_at, rowid",
            (user_id,),
        ).fetchall()
    return [{"ticker": r["ticker"], "added_at": r["added_at"]} for r in rows]


def add_watchlist(ticker: str, user_id: str = DEFAULT_USER) -> bool:
    """Add a ticker. Returns False if it was already on the watchlist."""
    with transaction() as conn:
        cur = conn.execute(
            "INSERT OR IGNORE INTO watchlist (id, user_id, ticker, added_at) VALUES (?, ?, ?, ?)",
            (new_id(), user_id, ticker, now_iso()),
        )
        return cur.rowcount == 1


def remove_watchlist(ticker: str, user_id: str = DEFAULT_USER) -> bool:
    """Remove a ticker. Returns False if it was not on the watchlist."""
    with transaction() as conn:
        cur = conn.execute(
            "DELETE FROM watchlist WHERE user_id = ? AND ticker = ?", (user_id, ticker)
        )
        return cur.rowcount > 0


# --- Trades ----------------------------------------------------------------


def apply_trade(
    ticker: str, side: str, quantity: float, price: float, user_id: str = DEFAULT_USER
) -> dict[str, Any]:
    """Atomically update cash and position and append to the trade log.

    Buys use a weighted-average cost; sells keep avg_cost unchanged; a position whose
    quantity drops below QTY_EPSILON is deleted. Raises TradeError (or a subclass) with
    no writes on rejection.
    """
    side = (side or "").lower()
    if side not in ("buy", "sell"):
        raise TradeError(f"Invalid side {side!r}: must be 'buy' or 'sell'")
    quantity = float(quantity)
    price = float(price)
    if not quantity > 0:
        raise TradeError("Quantity must be greater than 0")
    if not price > 0:
        raise TradeError("Price must be greater than 0")

    ts = now_iso()
    with transaction() as conn:
        _seed(conn, user_id)
        cash = float(
            conn.execute(
                "SELECT cash_balance FROM users_profile WHERE id = ?", (user_id,)
            ).fetchone()["cash_balance"]
        )
        pos = conn.execute(
            "SELECT quantity, avg_cost FROM positions WHERE user_id = ? AND ticker = ?",
            (user_id, ticker),
        ).fetchone()
        held = float(pos["quantity"]) if pos else 0.0
        avg_cost = float(pos["avg_cost"]) if pos else 0.0
        notional = quantity * price

        if side == "buy":
            if notional > cash + CASH_EPSILON:
                raise InsufficientCashError(
                    f"Insufficient cash: buying {_fmt_qty(quantity)} {ticker} at ${price:,.2f} "
                    f"costs ${notional:,.2f}, but only ${cash:,.2f} is available"
                )
            new_qty = held + quantity
            new_avg = (held * avg_cost + notional) / new_qty
            new_cash = max(cash - notional, 0.0)
        else:
            if quantity > held + QTY_EPSILON:
                raise InsufficientSharesError(
                    f"Insufficient shares: cannot sell {_fmt_qty(quantity)} {ticker}, "
                    f"only {_fmt_qty(held)} held"
                )
            new_qty = held - quantity
            new_avg = avg_cost
            new_cash = cash + notional

        conn.execute(
            "UPDATE users_profile SET cash_balance = ? WHERE id = ?", (new_cash, user_id)
        )
        if abs(new_qty) < QTY_EPSILON:
            conn.execute(
                "DELETE FROM positions WHERE user_id = ? AND ticker = ?", (user_id, ticker)
            )
        elif pos:
            conn.execute(
                "UPDATE positions SET quantity = ?, avg_cost = ?, updated_at = ? "
                "WHERE user_id = ? AND ticker = ?",
                (new_qty, new_avg, ts, user_id, ticker),
            )
        else:
            conn.execute(
                "INSERT INTO positions (id, user_id, ticker, quantity, avg_cost, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (new_id(), user_id, ticker, new_qty, new_avg, ts),
            )

        trade = {
            "id": new_id(),
            "ticker": ticker,
            "side": side,
            "quantity": quantity,
            "price": price,
            "executed_at": ts,
        }
        conn.execute(
            "INSERT INTO trades (id, user_id, ticker, side, quantity, price, executed_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (trade["id"], user_id, ticker, side, quantity, price, ts),
        )
    return trade


def list_trades(user_id: str = DEFAULT_USER) -> list[dict[str, Any]]:
    """Trade log, ascending. Not part of contract §4; handy for tests and debugging."""
    with connect() as conn:
        rows = conn.execute(
            "SELECT id, ticker, side, quantity, price, executed_at FROM trades "
            "WHERE user_id = ? ORDER BY executed_at, rowid",
            (user_id,),
        ).fetchall()
    return [dict(r) for r in rows]


# --- Snapshots -------------------------------------------------------------


def insert_snapshot(total_value: float, user_id: str = DEFAULT_USER) -> None:
    with transaction() as conn:
        conn.execute(
            "INSERT INTO portfolio_snapshots (id, user_id, total_value, recorded_at) "
            "VALUES (?, ?, ?, ?)",
            (new_id(), user_id, float(total_value), now_iso()),
        )


def list_snapshots(
    since_iso: str | None = None, user_id: str = DEFAULT_USER
) -> list[dict[str, Any]]:
    """Snapshots ascending by time; `since_iso` (inclusive) accepts any ISO-8601 form."""
    sql = "SELECT total_value, recorded_at FROM portfolio_snapshots WHERE user_id = ?"
    params: list[Any] = [user_id]
    if since_iso:
        sql += " AND recorded_at >= ?"
        params.append(to_utc_iso(since_iso))
    sql += " ORDER BY recorded_at, rowid"
    with connect() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [
        {"total_value": float(r["total_value"]), "recorded_at": r["recorded_at"]} for r in rows
    ]


# --- Chat ------------------------------------------------------------------


def _chat_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "role": row["role"],
        "content": row["content"],
        "actions": json.loads(row["actions"]) if row["actions"] is not None else None,
        "created_at": row["created_at"],
    }


def insert_chat_message(
    role: str, content: str, actions: dict | None = None, user_id: str = DEFAULT_USER
) -> dict[str, Any]:
    if role not in ("user", "assistant"):
        raise ValueError(f"Invalid role {role!r}: must be 'user' or 'assistant'")
    msg_id, ts = new_id(), now_iso()
    actions_json = json.dumps(actions) if actions is not None else None
    with transaction() as conn:
        conn.execute(
            "INSERT INTO chat_messages (id, user_id, role, content, actions, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (msg_id, user_id, role, content, actions_json, ts),
        )
        row = conn.execute("SELECT * FROM chat_messages WHERE id = ?", (msg_id,)).fetchone()
    return _chat_dict(row)


def list_chat_messages(limit: int = 50, user_id: str = DEFAULT_USER) -> list[dict[str, Any]]:
    """The most recent `limit` messages, returned ascending by created_at."""
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM ("
            "  SELECT *, rowid AS rid FROM chat_messages WHERE user_id = ? "
            "  ORDER BY created_at DESC, rowid DESC LIMIT ?"
            ") ORDER BY created_at, rid",
            (user_id, max(int(limit), 0)),
        ).fetchall()
    return [_chat_dict(r) for r in rows]
