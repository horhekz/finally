"use client";

import { useEffect, useRef, useState, type FormEvent } from "react";
import { formatPercent, formatPrice, signClass } from "@/lib/format";
import type { PricePoint, PriceMap, WatchlistItem } from "@/lib/types";
import { Sparkline } from "./Sparkline";

export const FLASH_MS = 500;

type Flash = "" | "flash-up" | "flash-down";

export function PriceCell({ ticker, price }: { ticker: string; price: number | null }) {
  const prev = useRef(price);
  const [flash, setFlash] = useState<Flash>("");

  useEffect(() => {
    const before = prev.current;
    prev.current = price;
    if (price == null || before == null || price === before) return;
    setFlash(price > before ? "flash-up" : "flash-down");
    const timer = setTimeout(() => setFlash(""), FLASH_MS);
    return () => clearTimeout(timer);
  }, [price]);

  return (
    <span data-testid={`price-${ticker}`} className={`price-cell num rounded-sm px-1 py-0.5 text-right ${flash}`}>
      {formatPrice(price)}
    </span>
  );
}

interface WatchlistProps {
  items: WatchlistItem[];
  prices: PriceMap;
  history: Map<string, PricePoint[]>;
  selected: string | null;
  onSelect: (ticker: string) => void;
  onAdd: (ticker: string) => Promise<string | null>;
  onRemove: (ticker: string) => Promise<string | null>;
}

export function Watchlist({ items, prices, history, selected, onSelect, onAdd, onRemove }: WatchlistProps) {
  const [draft, setDraft] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    const ticker = draft.trim().toUpperCase();
    if (!ticker) {
      setError("Enter a ticker symbol, like PYPL.");
      return;
    }
    setBusy(true);
    const err = await onAdd(ticker);
    setBusy(false);
    setError(err);
    if (!err) setDraft("");
  };

  const remove = async (ticker: string) => {
    setError(await onRemove(ticker));
  };

  return (
    <section data-testid="watchlist" className="flex h-full min-h-0 flex-col" aria-label="Watchlist">
      <div className="flex items-center justify-between border-b border-line px-3 py-2">
        <h2 className="font-semibold">Watchlist</h2>
        <span className="text-muted">{items.length} tickers</span>
      </div>

      <div className="grid grid-cols-[1fr_64px_auto_56px_18px] gap-x-2 border-b border-line px-3 py-1 text-[11px] text-muted">
        <span>Symbol</span>
        <span>Since load</span>
        <span className="text-right">Last</span>
        <span className="text-right">Day</span>
        <span />
      </div>

      <ul className="min-h-0 flex-1 overflow-y-auto">
        {items.map((item) => {
          const tick = prices[item.ticker];
          const price = tick?.price ?? item.price;
          const dayPct = tick ? tick.day_change_percent : item.day_change_percent;
          const isSelected = item.ticker === selected;
          return (
            <li
              key={item.ticker}
              data-testid={`watchlist-row-${item.ticker}`}
              aria-selected={isSelected}
              onClick={() => onSelect(item.ticker)}
              className={`group grid cursor-pointer grid-cols-[1fr_64px_auto_56px_18px] items-center gap-x-2 border-b border-line/60 px-3 py-[7px] hover:bg-raised ${
                isSelected ? "bg-raised shadow-[inset_2px_0_0_var(--color-accent)]" : ""
              }`}
            >
              <button
                type="button"
                className="text-left font-semibold tracking-wide"
                onClick={(e) => {
                  e.stopPropagation();
                  onSelect(item.ticker);
                }}
              >
                {item.ticker}
              </button>
              <Sparkline testId={`sparkline-${item.ticker}`} points={history.get(item.ticker)} width={64} height={18} />
              <PriceCell ticker={item.ticker} price={price ?? null} />
              <span data-testid={`day-change-${item.ticker}`} className={`num text-right ${signClass(dayPct)}`}>
                {formatPercent(dayPct)}
              </span>
              <button
                type="button"
                data-testid={`watchlist-remove-${item.ticker}`}
                aria-label={`Remove ${item.ticker} from watchlist`}
                title={`Remove ${item.ticker}`}
                onClick={(e) => {
                  e.stopPropagation();
                  void remove(item.ticker);
                }}
                className="rounded text-[15px] leading-none text-muted opacity-40 hover:text-down hover:opacity-100 focus-visible:opacity-100 group-hover:opacity-100"
              >
                ×
              </button>
            </li>
          );
        })}
        {items.length === 0 && (
          <li className="px-3 py-6 text-center text-muted">Add a ticker below to start watching prices.</li>
        )}
      </ul>

      <form onSubmit={submit} className="border-t border-line p-2">
        <div className="flex gap-1.5">
          <input
            data-testid="watchlist-add-input"
            value={draft}
            onChange={(e) => setDraft(e.target.value.toUpperCase())}
            placeholder="Add ticker"
            aria-label="Ticker to add"
            maxLength={10}
            className="min-w-0 flex-1 rounded border border-line bg-ink px-2 py-1 uppercase placeholder:normal-case placeholder:text-faint"
          />
          <button
            data-testid="watchlist-add-button"
            type="submit"
            disabled={busy}
            className="rounded border border-blue/60 px-3 py-1 font-medium text-blue hover:bg-blue/10 disabled:opacity-50"
          >
            Add
          </button>
        </div>
        <p data-testid="watchlist-error" role="alert" className={`mt-1 text-down ${error ? "" : "hidden"}`}>
          {error}
        </p>
      </form>
    </section>
  );
}
