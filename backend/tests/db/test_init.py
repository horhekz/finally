"""Schema creation, seeding, path resolution and idempotency."""

import sqlite3

import app.db as db
from app.db import connection
from app.market import DEFAULT_TICKERS

TABLES = {
    "users_profile", "watchlist", "positions", "trades", "portfolio_snapshots", "chat_messages",
}


def _table_sql(path):
    conn = sqlite3.connect(path)
    try:
        rows = conn.execute("SELECT name, sql FROM sqlite_master WHERE type = 'table'").fetchall()
    finally:
        conn.close()
    return dict(rows)


def test_creates_parent_dir_and_tables(db_path):
    assert db_path.exists()
    assert TABLES <= set(_table_sql(db_path))


def test_seed_defaults(db_path):
    assert db.get_cash() == 10000.0
    assert [w["ticker"] for w in db.list_watchlist()] == list(DEFAULT_TICKERS)
    assert db.list_positions() == []
    assert db.list_snapshots() == []
    assert db.list_chat_messages() == []


def test_wal_enabled(db_path):
    conn = sqlite3.connect(db_path)
    try:
        assert conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
    finally:
        conn.close()


def test_init_is_idempotent(db_path):
    db.apply_trade("AAPL", "buy", 1, 100)
    db.remove_watchlist("NFLX")
    db.init_db(db_path)
    db.init_db(db_path)
    assert db.get_cash() == 9900.0
    assert len(db.list_watchlist()) == 9
    assert db.get_position("AAPL")["quantity"] == 1


def test_emptied_watchlist_is_not_reseeded(db_path):
    for t in DEFAULT_TICKERS:
        db.remove_watchlist(t)
    db.init_db(db_path)
    assert db.list_watchlist() == []


def test_init_existing_empty_file(tmp_path):
    path = tmp_path / "empty.db"
    path.touch()
    db.init_db(path)
    assert TABLES <= set(_table_sql(path))
    assert db.get_cash() == 10000.0


def test_db_path_env_var(tmp_path, monkeypatch):
    path = tmp_path / "env" / "x.db"
    monkeypatch.setenv("DB_PATH", str(path))
    db.init_db()
    assert db.get_db_path() == path
    assert path.exists()


def test_default_path_is_repo_db():
    default = connection._DEFAULT_PATH
    assert default.name == "finally.db" and default.parent.name == "db"
    assert (default.parents[1] / "backend" / "app" / "db").is_dir()


def test_lazy_init_on_first_call(tmp_path, monkeypatch):
    path = tmp_path / "lazy" / "x.db"
    monkeypatch.setattr(connection, "_db_path", path)
    assert not path.exists()
    assert db.get_cash() == 10000.0
    assert path.exists()


def test_constraints_and_defaults(db_path):
    sql = _table_sql(db_path)
    assert "UNIQUE (user_id, ticker)" in sql["watchlist"]
    assert "UNIQUE (user_id, ticker)" in sql["positions"]
    for table in TABLES - {"users_profile"}:
        assert "user_id" in sql[table] and "DEFAULT 'default'" in sql[table]


def test_timestamps_are_utc_iso(db_path):
    assert db.list_watchlist()[0]["added_at"].endswith("+00:00")
