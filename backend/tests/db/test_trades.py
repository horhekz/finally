"""apply_trade: position math, cash, trade log, validation and atomicity."""

from concurrent.futures import ThreadPoolExecutor

import pytest

import app.db as db
from app.db import repository


def test_buy_creates_position(db_path):
    trade = db.apply_trade("AAPL", "buy", 10, 190.0)
    assert set(trade) == {"id", "ticker", "side", "quantity", "price", "executed_at"}
    assert trade["side"] == "buy" and trade["quantity"] == 10 and trade["price"] == 190.0
    assert db.get_cash() == pytest.approx(10000 - 1900)
    pos = db.get_position("AAPL")
    assert pos["quantity"] == 10 and pos["avg_cost"] == 190.0
    assert set(pos) == {"ticker", "quantity", "avg_cost", "updated_at"}


def test_weighted_average_cost_on_buys(db_path):
    db.apply_trade("AAPL", "buy", 10, 100.0)
    db.apply_trade("AAPL", "buy", 30, 200.0)
    pos = db.get_position("AAPL")
    assert pos["quantity"] == 40
    assert pos["avg_cost"] == pytest.approx((10 * 100 + 30 * 200) / 40)
    assert db.get_cash() == pytest.approx(10000 - 1000 - 6000)


def test_sell_keeps_avg_cost_and_adds_cash(db_path):
    db.apply_trade("AAPL", "buy", 10, 100.0)
    db.apply_trade("AAPL", "sell", 4, 150.0)
    pos = db.get_position("AAPL")
    assert pos["quantity"] == 6
    assert pos["avg_cost"] == 100.0
    assert db.get_cash() == pytest.approx(10000 - 1000 + 600)


def test_sell_at_a_loss(db_path):
    db.apply_trade("TSLA", "buy", 10, 250.0)
    db.apply_trade("TSLA", "sell", 5, 200.0)
    assert db.get_position("TSLA")["avg_cost"] == 250.0
    assert db.get_cash() == pytest.approx(10000 - 2500 + 1000)


def test_buy_after_partial_sell_reaverages(db_path):
    db.apply_trade("AAPL", "buy", 10, 100.0)
    db.apply_trade("AAPL", "sell", 5, 300.0)
    db.apply_trade("AAPL", "buy", 5, 200.0)
    assert db.get_position("AAPL")["avg_cost"] == pytest.approx(150.0)


def test_sell_all_deletes_position(db_path):
    db.apply_trade("AAPL", "buy", 3, 100.0)
    db.apply_trade("AAPL", "sell", 3, 110.0)
    assert db.get_position("AAPL") is None
    assert db.list_positions() == []
    assert db.get_cash() == pytest.approx(10030.0)


def test_float_leftover_counts_as_zero(db_path):
    db.apply_trade("AAPL", "buy", 0.1, 100.0)
    db.apply_trade("AAPL", "buy", 0.2, 100.0)
    # 0.1 + 0.2 == 0.30000000000000004
    db.apply_trade("AAPL", "sell", 0.3, 100.0)
    assert db.get_position("AAPL") is None


def test_fractional_shares(db_path):
    db.apply_trade("NVDA", "buy", 0.5, 800.0)
    db.apply_trade("NVDA", "buy", 0.25, 1000.0)
    pos = db.get_position("NVDA")
    assert pos["quantity"] == pytest.approx(0.75)
    assert pos["avg_cost"] == pytest.approx((400 + 250) / 0.75)
    assert db.get_cash() == pytest.approx(10000 - 650)


def test_list_positions_sorted(db_path):
    db.apply_trade("MSFT", "buy", 1, 10.0)
    db.apply_trade("AAPL", "buy", 1, 10.0)
    assert [p["ticker"] for p in db.list_positions()] == ["AAPL", "MSFT"]


def test_buy_exactly_all_cash(db_path):
    db.apply_trade("AAPL", "buy", 100, 100.0)
    assert db.get_cash() == 0.0


def test_insufficient_cash(db_path):
    with pytest.raises(db.InsufficientCashError, match="Insufficient cash"):
        db.apply_trade("AAPL", "buy", 101, 100.0)
    assert db.get_cash() == 10000.0
    assert db.get_position("AAPL") is None
    assert db.list_trades() == []


def test_oversell(db_path):
    db.apply_trade("AAPL", "buy", 2, 100.0)
    with pytest.raises(db.InsufficientSharesError, match="Insufficient shares"):
        db.apply_trade("AAPL", "sell", 3, 100.0)
    assert db.get_position("AAPL")["quantity"] == 2
    assert db.get_cash() == 9800.0
    assert len(db.list_trades()) == 1


def test_insufficient_cash_message_has_no_exponent(db_path):
    with pytest.raises(db.InsufficientCashError) as exc:
        db.apply_trade("NVDA", "buy", 1_000_000, 875.5)
    assert str(exc.value) == (
        "Insufficient cash: buying 1,000,000 NVDA at $875.50 "
        "costs $875,500,000.00, but only $10,000.00 is available"
    )


def test_insufficient_shares_message_format(db_path):
    db.apply_trade("AAPL", "buy", 0.12345, 100.0)
    with pytest.raises(db.InsufficientSharesError) as exc:
        db.apply_trade("AAPL", "sell", 2_500_000.5, 100.0)
    assert str(exc.value) == (
        "Insufficient shares: cannot sell 2,500,000.5 AAPL, only 0.1235 held"
    )


def test_sell_unheld(db_path):
    with pytest.raises(db.InsufficientSharesError):
        db.apply_trade("MSFT", "sell", 1, 100.0)


def test_error_hierarchy():
    assert issubclass(db.InsufficientCashError, db.TradeError)
    assert issubclass(db.InsufficientSharesError, db.TradeError)
    assert issubclass(db.TradeError, ValueError)


@pytest.mark.parametrize(
    ("side", "qty", "price"),
    [("hold", 1, 100), ("buy", 0, 100), ("buy", -1, 100), ("sell", 1, 0), ("buy", 1, -5)],
)
def test_invalid_input(db_path, side, qty, price):
    with pytest.raises(db.TradeError):
        db.apply_trade("AAPL", side, qty, price)
    assert db.get_cash() == 10000.0
    assert db.list_trades() == []


def test_trade_log_appended(db_path):
    db.apply_trade("AAPL", "buy", 2, 100.0)
    db.apply_trade("AAPL", "sell", 1, 120.0)
    trades = db.list_trades()
    assert [(t["side"], t["quantity"], t["price"]) for t in trades] == [
        ("buy", 2, 100.0), ("sell", 1, 120.0),
    ]
    assert len({t["id"] for t in trades}) == 2


def test_rollback_on_mid_transaction_failure(db_path, monkeypatch):
    calls = {"n": 0}
    real_new_id = repository.new_id

    def flaky_new_id():
        calls["n"] += 1
        if calls["n"] == 2:  # 1st: position row, 2nd: trade row
            raise RuntimeError("disk on fire")
        return real_new_id()

    monkeypatch.setattr(repository, "new_id", flaky_new_id)
    with pytest.raises(RuntimeError):
        db.apply_trade("AAPL", "buy", 1, 100.0)
    assert db.get_cash() == 10000.0
    assert db.get_position("AAPL") is None
    assert db.list_trades() == []


def test_positions_isolated_by_user(db_path):
    db.apply_trade("AAPL", "buy", 1, 100.0, user_id="alice")
    assert db.get_cash(user_id="alice") == 9900.0
    assert db.get_cash() == 10000.0
    assert db.list_positions() == []
    assert len(db.list_positions(user_id="alice")) == 1


def test_concurrent_buys_are_serialized(db_path):
    def buy(_):
        try:
            db.apply_trade("AAPL", "buy", 1, 1000.0)
            return True
        except db.InsufficientCashError:
            return False

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(buy, range(15)))
    assert sum(results) == 10
    assert db.get_cash() == pytest.approx(0.0)
    assert db.get_position("AAPL")["quantity"] == 10
