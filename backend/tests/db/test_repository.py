"""Watchlist, snapshots, chat and reset."""

import pytest

import app.db as db
from app.db import repository
from app.market import DEFAULT_TICKERS


def test_watchlist_add_and_remove(db_path):
    assert db.add_watchlist("PYPL") is True
    assert db.list_watchlist()[-1]["ticker"] == "PYPL"
    assert db.remove_watchlist("PYPL") is True
    assert "PYPL" not in [w["ticker"] for w in db.list_watchlist()]


def test_watchlist_uniqueness(db_path):
    assert db.add_watchlist("AAPL") is False
    assert db.add_watchlist("PYPL") is True
    assert db.add_watchlist("PYPL") is False
    assert [w["ticker"] for w in db.list_watchlist()].count("PYPL") == 1


def test_remove_absent(db_path):
    assert db.remove_watchlist("ZZZZ") is False


def test_watchlist_item_shape(db_path):
    assert set(db.list_watchlist()[0]) == {"ticker", "added_at"}


def _fake_clock(monkeypatch, stamps):
    it = iter(stamps)
    monkeypatch.setattr(repository, "now_iso", lambda: next(it))


def test_snapshots_ascending(db_path, monkeypatch):
    _fake_clock(monkeypatch, [
        "2026-10-13T10:00:02.000000+00:00",
        "2026-10-13T10:00:00.000000+00:00",
        "2026-10-13T10:00:01.000000+00:00",
    ])
    db.insert_snapshot(3.0)
    db.insert_snapshot(1.0)
    db.insert_snapshot(2.0)
    snaps = db.list_snapshots()
    assert [s["total_value"] for s in snaps] == [1.0, 2.0, 3.0]
    assert set(snaps[0]) == {"total_value", "recorded_at"}


def test_snapshots_since_filter(db_path, monkeypatch):
    _fake_clock(monkeypatch, [
        "2026-10-12T09:00:00.000000+00:00",
        "2026-10-13T09:00:00.000000+00:00",
        "2026-10-13T11:00:00.000000+00:00",
    ])
    for v in (1.0, 2.0, 3.0):
        db.insert_snapshot(v)

    def values(since):
        return [s["total_value"] for s in db.list_snapshots(since)]

    assert values("2026-10-13T09:00:00+00:00") == [2.0, 3.0]  # inclusive
    assert values("2026-10-13T10:00:00Z") == [3.0]
    assert values("2026-10-13T12:00:00+02:00") == [3.0]  # == 10:00Z
    assert values("2026-10-13T10:00:00+02:00") == [2.0, 3.0]  # == 08:00Z
    assert values("2026-10-13T10:00:00") == [3.0]  # naive = UTC
    assert values(None) == [1.0, 2.0, 3.0]


def test_snapshot_real_clock(db_path):
    db.insert_snapshot(10000)
    db.insert_snapshot(10050.5)
    snaps = db.list_snapshots()
    assert [s["total_value"] for s in snaps] == [10000.0, 10050.5]
    assert snaps[0]["recorded_at"] <= snaps[1]["recorded_at"]


def test_chat_round_trip(db_path):
    user = db.insert_chat_message("user", "buy 1 AAPL", None)
    actions = {
        "trades": [
            {"ticker": "AAPL", "side": "buy", "quantity": 1, "status": "executed", "price": 190.5}
        ],
        "watchlist_changes": [
            {"ticker": "PYPL", "action": "add", "status": "failed", "error": "nope"}
        ],
    }
    bot = db.insert_chat_message("assistant", "Done.", actions)
    assert user["actions"] is None
    assert bot["actions"] == actions
    assert set(bot) == {"id", "role", "content", "actions", "created_at"}
    msgs = db.list_chat_messages()
    assert [m["id"] for m in msgs] == [user["id"], bot["id"]]
    assert msgs[1]["actions"] == actions


def test_chat_limit_returns_latest_ascending(db_path):
    for i in range(30):
        db.insert_chat_message("user" if i % 2 == 0 else "assistant", f"m{i}")
    msgs = db.list_chat_messages(limit=20)
    assert [m["content"] for m in msgs] == [f"m{i}" for i in range(10, 30)]
    assert len(db.list_chat_messages()) == 30


def test_chat_invalid_role(db_path):
    with pytest.raises(ValueError):
        db.insert_chat_message("system", "hi")


def test_reset(db_path):
    db.apply_trade("AAPL", "buy", 5, 100.0)
    db.add_watchlist("PYPL")
    db.remove_watchlist("AAPL")
    db.insert_snapshot(9999.0)
    db.insert_chat_message("user", "hi")
    db.reset_db()
    assert db.get_cash() == 10000.0
    assert db.list_positions() == []
    assert db.list_trades() == []
    assert db.list_snapshots() == []
    assert db.list_chat_messages() == []
    assert [w["ticker"] for w in db.list_watchlist()] == list(DEFAULT_TICKERS)


def test_reset_leaves_other_users(db_path):
    db.apply_trade("AAPL", "buy", 1, 100.0, user_id="alice")
    db.reset_db()
    assert db.get_cash(user_id="alice") == 9900.0


def test_unknown_user_gets_seeded(db_path):
    assert db.get_cash(user_id="bob") == 10000.0
    assert len(db.list_watchlist(user_id="bob")) == 10
