import type { ChatMessage, Portfolio, PriceTick, WatchlistItem } from "@/lib/types";

export function tick(ticker: string, price: number, extra: Partial<PriceTick> = {}): PriceTick {
  return {
    ticker,
    price,
    previous_price: price,
    timestamp: 1_791_706_748 + Math.random(),
    change: 0,
    change_percent: 0,
    direction: "flat",
    reference_price: 100,
    day_change: price - 100,
    day_change_percent: price - 100,
    ...extra,
  };
}

export function watchItem(ticker: string, price: number | null = null): WatchlistItem {
  return {
    ticker,
    added_at: "2026-10-13T00:00:00Z",
    price,
    previous_price: price,
    reference_price: price,
    day_change_percent: price == null ? null : 0,
    direction: price == null ? null : "flat",
  };
}

export const portfolioFixture: Portfolio = {
  cash_balance: 8000,
  positions_value: 2000,
  total_value: 10000,
  unrealized_pnl: 0,
  unrealized_pnl_percent: 0,
  positions: [
    {
      ticker: "AAPL",
      quantity: 10,
      avg_cost: 150,
      current_price: 150,
      market_value: 1500,
      unrealized_pnl: 0,
      unrealized_pnl_percent: 0,
      weight: 0.15,
    },
    {
      ticker: "TSLA",
      quantity: 2,
      avg_cost: 250,
      current_price: null,
      market_value: 500,
      unrealized_pnl: 0,
      unrealized_pnl_percent: 0,
      weight: 0.05,
    },
  ],
};

export const emptyPortfolio: Portfolio = {
  cash_balance: 10000,
  positions_value: 0,
  total_value: 10000,
  unrealized_pnl: 0,
  unrealized_pnl_percent: 0,
  positions: [],
};

export function assistant(content: string, actions: ChatMessage["actions"] = null): ChatMessage {
  return { id: `a-${content}`, role: "assistant", content, actions, created_at: "2026-10-13T00:00:00Z" };
}
