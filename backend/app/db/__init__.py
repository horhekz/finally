"""SQLite persistence for FinAlly. Import everything from here.

The DB is created lazily: the first call to any function (or an explicit `init_db()`)
creates the parent dir, the tables and the seed data. Path: `init_db(path)` argument,
else `DB_PATH`, else `<repo>/db/finally.db`. Every function opens its own connection,
so it is safe to call from FastAPI's threadpool. Every function takes `user_id="default"`.
"""

from .connection import DEFAULT_CASH, DEFAULT_USER, get_db_path, init_db
from .repository import (
    InsufficientCashError,
    InsufficientSharesError,
    TradeError,
    add_watchlist,
    apply_trade,
    get_cash,
    get_position,
    insert_chat_message,
    insert_snapshot,
    list_chat_messages,
    list_positions,
    list_snapshots,
    list_trades,
    list_watchlist,
    remove_watchlist,
    reset_db,
)

__all__ = [
    "DEFAULT_CASH",
    "DEFAULT_USER",
    "InsufficientCashError",
    "InsufficientSharesError",
    "TradeError",
    "add_watchlist",
    "apply_trade",
    "get_cash",
    "get_db_path",
    "get_position",
    "init_db",
    "insert_chat_message",
    "insert_snapshot",
    "list_chat_messages",
    "list_positions",
    "list_snapshots",
    "list_trades",
    "list_watchlist",
    "remove_watchlist",
    "reset_db",
]
