"""DB path resolution, connections, lazy schema init and seeding."""

from __future__ import annotations

import os
import sqlite3
import threading
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from app.market import DEFAULT_TICKERS

DEFAULT_USER = "default"
DEFAULT_CASH = 10000.0

# backend/app/db/connection.py -> parents[3] is the repo root.
_DEFAULT_PATH = Path(__file__).resolve().parents[3] / "db" / "finally.db"
_SCHEMA_PATH = Path(__file__).with_name("schema.sql")

_lock = threading.Lock()
_db_path: Path | None = None
_initialized: set[Path] = set()


def now_iso() -> str:
    """Current UTC time as ISO-8601 with fixed microsecond precision (sortable)."""
    return datetime.now(UTC).isoformat(timespec="microseconds")


def to_utc_iso(value: str) -> str:
    """Normalise any ISO-8601 string ('Z', offset, or naive = UTC) to the stored format."""
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC).isoformat(timespec="microseconds")


def new_id() -> str:
    return str(uuid.uuid4())


def _resolve_path(path: str | os.PathLike[str] | None) -> Path:
    if path is not None:
        return Path(path)
    env = os.environ.get("DB_PATH", "").strip()
    return Path(env) if env else _DEFAULT_PATH


def get_db_path() -> Path:
    """The active DB path (from init_db, else DB_PATH, else <repo>/db/finally.db)."""
    return _db_path if _db_path is not None else _resolve_path(None)


def _open(path: Path) -> sqlite3.Connection:
    # isolation_level=None: we issue BEGIN/COMMIT ourselves.
    conn = sqlite3.connect(path, timeout=30.0, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 30000")
    return conn


def _seed(conn: sqlite3.Connection, user_id: str) -> None:
    """Create the user profile and default watchlist if the profile does not exist yet.

    Keyed on the profile so a user who empties their watchlist is not re-seeded.
    """
    exists = conn.execute("SELECT 1 FROM users_profile WHERE id = ?", (user_id,)).fetchone()
    if exists:
        return
    ts = now_iso()
    conn.execute(
        "INSERT INTO users_profile (id, cash_balance, created_at) VALUES (?, ?, ?)",
        (user_id, DEFAULT_CASH, ts),
    )
    conn.executemany(
        "INSERT OR IGNORE INTO watchlist (id, user_id, ticker, added_at) VALUES (?, ?, ?, ?)",
        [(new_id(), user_id, t, ts) for t in DEFAULT_TICKERS],
    )


def init_db(path: str | os.PathLike[str] | None = None) -> None:
    """Idempotently create the parent dir, tables and seed data; make `path` the active DB."""
    global _db_path
    resolved = _resolve_path(path)
    with _lock:
        resolved.parent.mkdir(parents=True, exist_ok=True)
        conn = _open(resolved)
        try:
            conn.execute("PRAGMA journal_mode = WAL")
            conn.executescript(_SCHEMA_PATH.read_text(encoding="utf-8"))
            conn.execute("BEGIN IMMEDIATE")
            try:
                _seed(conn, DEFAULT_USER)
                conn.execute("COMMIT")
            except BaseException:
                conn.execute("ROLLBACK")
                raise
        finally:
            conn.close()
        _db_path = resolved
        _initialized.add(resolved)


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    """A fresh connection to the active DB, lazily initialising it on first use."""
    path = get_db_path()
    if path not in _initialized:
        init_db(path)
    conn = _open(path)
    try:
        yield conn
    finally:
        conn.close()


@contextmanager
def transaction() -> Iterator[sqlite3.Connection]:
    """BEGIN IMMEDIATE ... COMMIT on a fresh connection; ROLLBACK on any exception."""
    with connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            yield conn
        except BaseException:
            conn.execute("ROLLBACK")
            raise
        conn.execute("COMMIT")
