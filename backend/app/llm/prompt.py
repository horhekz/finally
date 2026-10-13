"""System prompt, portfolio context and history construction for the chat LLM."""

from __future__ import annotations

import json
from typing import Any

HISTORY_LIMIT = 20

SYSTEM_PROMPT = """You are FinAlly, an AI trading assistant inside a simulated trading workstation.
The user trades a virtual portfolio with fake money. Market orders only, instant fill at the
current price, no fees. Fractional shares are allowed.

Your job:
- Analyze portfolio composition, risk concentration and P&L using the context provided.
- Suggest trades with brief reasoning when useful.
- Be concise and data-driven: quote numbers (prices, weights, P&L) from the context.

Rules for actions:
- Only put entries in "trades" when the user explicitly asks you to trade or clearly agrees to a
  trade you proposed. Never trade on your own initiative.
- Only trade tickers with a known price in the context, and check cash for buys and held
  quantity for sells before proposing them.
- You may add tickers to the watchlist proactively when relevant ("watchlist_changes" with
  action "add"). Only remove watchlist tickers when the user asks.
- Tickers are uppercase symbols like AAPL. Quantities are share counts > 0.
- Your "message" describes what you are doing; the system executes the actions and the user sees
  each result. Previous assistant turns show the executed results in [Executed actions: ...].

Always respond with a single JSON object matching the schema:
{"message": string, "trades": [{"ticker", "side": "buy"|"sell", "quantity"}],
 "watchlist_changes": [{"ticker", "action": "add"|"remove"}]}
Use empty lists when there are no actions."""


def build_context(portfolio: dict[str, Any] | None, watchlist: list[dict[str, Any]] | None) -> str:
    """Render the live portfolio and watchlist as a compact context block."""
    lines = ["Current state of the user's account (live):"]
    if portfolio is None:
        lines.append("Portfolio: unavailable")
    else:
        lines.append(
            f"Cash: {_money(portfolio.get('cash_balance'))} | "
            f"Positions value: {_money(portfolio.get('positions_value'))} | "
            f"Total value: {_money(portfolio.get('total_value'))} | "
            f"Unrealized P&L: {_money(portfolio.get('unrealized_pnl'))} "
            f"({_pct(portfolio.get('unrealized_pnl_percent'))})"
        )
        positions = portfolio.get("positions") or []
        if positions:
            lines.append("Positions:")
            for p in positions:
                lines.append(
                    f"- {p.get('ticker')}: qty {_num(p.get('quantity'))}, "
                    f"avg cost {_money(p.get('avg_cost'))}, "
                    f"price {_money(p.get('current_price'))}, "
                    f"value {_money(p.get('market_value'))}, "
                    f"P&L {_money(p.get('unrealized_pnl'))} "
                    f"({_pct(p.get('unrealized_pnl_percent'))}), "
                    f"weight {_weight(p.get('weight'))}"
                )
        else:
            lines.append("Positions: none")

    if watchlist is None:
        lines.append("Watchlist: unavailable")
    elif watchlist:
        lines.append("Watchlist:")
        for w in watchlist:
            lines.append(
                f"- {w.get('ticker')}: price {_money(w.get('price'))}, "
                f"day change {_pct(w.get('day_change_percent'))}"
            )
    else:
        lines.append("Watchlist: empty")
    return "\n".join(lines)


def history_to_messages(history: list[dict[str, Any]]) -> list[dict[str, str]]:
    """Convert stored chat rows to LLM messages; assistant turns carry their executed actions."""
    messages: list[dict[str, str]] = []
    for row in history[-HISTORY_LIMIT:]:
        role = row.get("role")
        if role not in ("user", "assistant"):
            continue
        content = row.get("content") or ""
        actions = row.get("actions")
        if role == "assistant" and actions and _has_actions(actions):
            content = (
                f"{content}\n\n[Executed actions: {json.dumps(actions, separators=(',', ':'))}]"
            )
        messages.append({"role": role, "content": content})
    return messages


def build_messages(
    user_message: str,
    history: list[dict[str, Any]],
    portfolio: dict[str, Any] | None,
    watchlist: list[dict[str, Any]] | None,
) -> list[dict[str, str]]:
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "system", "content": build_context(portfolio, watchlist)},
        *history_to_messages(history),
        {"role": "user", "content": user_message},
    ]


def _has_actions(actions: Any) -> bool:
    if not isinstance(actions, dict):
        return False
    return bool(actions.get("trades") or actions.get("watchlist_changes"))


def _money(value: Any) -> str:
    return "n/a" if value is None else f"${float(value):,.2f}"


def _pct(value: Any) -> str:
    return "n/a" if value is None else f"{float(value):+.2f}%"


def _num(value: Any) -> str:
    return "n/a" if value is None else f"{float(value):g}"


def _weight(value: Any) -> str:
    return "n/a" if value is None else f"{float(value) * 100:.1f}%"
