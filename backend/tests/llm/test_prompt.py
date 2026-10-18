"""Prompt, context and history construction."""

import json

from app.llm.prompt import (
    HISTORY_LIMIT,
    SYSTEM_PROMPT,
    build_context,
    build_messages,
    history_to_messages,
)

PORTFOLIO = {
    "cash_balance": 5000.0,
    "positions_value": 5100.0,
    "total_value": 10100.0,
    "unrealized_pnl": 100.0,
    "unrealized_pnl_percent": 2.0,
    "positions": [
        {
            "ticker": "AAPL",
            "quantity": 25,
            "avg_cost": 200.0,
            "current_price": 204.0,
            "market_value": 5100.0,
            "unrealized_pnl": 100.0,
            "unrealized_pnl_percent": 2.0,
            "weight": 0.505,
        }
    ],
}
WATCHLIST = [
    {"ticker": "AAPL", "price": 204.0, "day_change_percent": 1.25},
    {"ticker": "PYPL", "price": None, "day_change_percent": None},
]


def test_system_prompt_rules():
    assert "FinAlly" in SYSTEM_PROMPT
    assert "Never trade on your own initiative" in SYSTEM_PROMPT
    assert "proactively" in SYSTEM_PROMPT


def test_context_includes_cash_positions_and_watchlist():
    ctx = build_context(PORTFOLIO, WATCHLIST)
    assert "Cash: $5,000.00" in ctx
    assert "Total value: $10,100.00" in ctx
    assert "AAPL: qty 25, avg cost $200.00, price $204.00" in ctx
    assert "weight 50.5%" in ctx
    assert "AAPL: price $204.00, day change +1.25%" in ctx
    assert "PYPL: price n/a, day change n/a" in ctx


def test_context_handles_missing_data():
    ctx = build_context(None, None)
    assert "Portfolio: unavailable" in ctx and "Watchlist: unavailable" in ctx
    empty = build_context({**PORTFOLIO, "positions": []}, [])
    assert "Positions: none" in empty and "Watchlist: empty" in empty


def test_position_without_price():
    p = {**PORTFOLIO, "positions": [{**PORTFOLIO["positions"][0], "current_price": None}]}
    assert "price n/a" in build_context(p, [])


def test_history_includes_executed_actions_for_assistant():
    actions = {
        "trades": [
            {
                "ticker": "AAPL",
                "side": "buy",
                "quantity": 1,
                "status": "failed",
                "error": "Insufficient cash",
            }
        ],
        "watchlist_changes": [],
    }
    history = [
        {"role": "user", "content": "buy aapl", "actions": None},
        {"role": "assistant", "content": "Buying.", "actions": actions},
        {"role": "assistant", "content": "Hi", "actions": {"trades": [], "watchlist_changes": []}},
    ]
    msgs = history_to_messages(history)
    assert msgs[0] == {"role": "user", "content": "buy aapl"}
    assert msgs[1]["content"].startswith("Buying.\n\n[Executed actions: ")
    assert json.loads(msgs[1]["content"].split("[Executed actions: ", 1)[1][:-1]) == actions
    assert msgs[2]["content"] == "Hi"


def test_history_capped_and_skips_unknown_roles():
    history = [{"role": "user", "content": str(i), "actions": None} for i in range(30)]
    history.append({"role": "system", "content": "x"})
    msgs = history_to_messages(history)
    assert len(msgs) == HISTORY_LIMIT - 1  # last 20 rows, minus the system row
    assert msgs[-1]["content"] == "29"


def test_build_messages_order():
    msgs = build_messages("what now?", [{"role": "user", "content": "hi"}], PORTFOLIO, WATCHLIST)
    assert [m["role"] for m in msgs] == ["system", "system", "user", "user"]
    assert msgs[0]["content"] == SYSTEM_PROMPT
    assert "Cash:" in msgs[1]["content"]
    assert msgs[-1] == {"role": "user", "content": "what now?"}
