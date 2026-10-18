"use client";

import { useEffect, useState, type FormEvent } from "react";

interface TradeBarProps {
  /** Pre-fills the ticker when the user selects one elsewhere. */
  selectedTicker: string | null;
  onTrade: (ticker: string, quantity: number, side: "buy" | "sell") => Promise<string | null>;
}

export function TradeBar({ selectedTicker, onTrade }: TradeBarProps) {
  const [ticker, setTicker] = useState(selectedTicker ?? "");
  const [quantity, setQuantity] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (selectedTicker) setTicker(selectedTicker);
  }, [selectedTicker]);

  const submit = async (side: "buy" | "sell", e?: FormEvent) => {
    e?.preventDefault();
    setNotice(null);
    const symbol = ticker.trim().toUpperCase();
    const qty = Number(quantity);
    if (!symbol) return setError("Enter a ticker symbol.");
    if (!quantity.trim() || !Number.isFinite(qty) || qty <= 0) return setError("Enter a quantity greater than 0.");
    setBusy(true);
    const err = await onTrade(symbol, qty, side);
    setBusy(false);
    setError(err);
    if (!err) {
      setNotice(`${side === "buy" ? "Bought" : "Sold"} ${qty} ${symbol}`);
      setQuantity("");
    }
  };

  return (
    <form
      onSubmit={(e) => submit("buy", e)}
      className="flex flex-wrap items-center gap-2 border-t border-line bg-panel px-3 py-2"
      aria-label="Trade"
    >
      <span className="font-semibold">Trade</span>
      <input
        data-testid="trade-ticker"
        value={ticker}
        onChange={(e) => setTicker(e.target.value.toUpperCase())}
        placeholder="Ticker"
        aria-label="Ticker"
        maxLength={10}
        className="w-24 rounded border border-line bg-ink px-2 py-1 uppercase placeholder:normal-case placeholder:text-faint"
      />
      <input
        data-testid="trade-quantity"
        value={quantity}
        onChange={(e) => setQuantity(e.target.value)}
        placeholder="Quantity"
        aria-label="Quantity"
        type="number"
        inputMode="decimal"
        min="0"
        step="any"
        className="num w-28 rounded border border-line bg-ink px-2 py-1 placeholder:text-faint"
      />
      <button
        data-testid="trade-buy"
        type="submit"
        disabled={busy}
        className="rounded bg-purple px-4 py-1 font-semibold text-white hover:brightness-115 disabled:opacity-50"
      >
        Buy
      </button>
      <button
        data-testid="trade-sell"
        type="button"
        disabled={busy}
        onClick={() => submit("sell")}
        className="rounded border border-purple px-4 py-1 font-semibold text-text hover:bg-purple/25 disabled:opacity-50"
      >
        Sell
      </button>
      <span className="text-muted">Market order, fills at the last price</span>
      <p data-testid="trade-error" role="alert" className={`basis-full text-down ${error ? "" : "hidden"}`}>
        {error}
      </p>
      {notice && !error && (
        <p role="status" className="basis-full text-up">
          {notice}
        </p>
      )}
    </form>
  );
}
