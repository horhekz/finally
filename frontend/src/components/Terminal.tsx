"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { api, ApiError } from "@/lib/api";
import { computeLivePortfolio } from "@/lib/portfolio";
import type { ChatMessage, Portfolio, Snapshot, WatchlistItem } from "@/lib/types";
import { usePriceStream } from "@/hooks/usePriceStream";
import { ChatPanel } from "./ChatPanel";
import { Header } from "./Header";
import { Heatmap } from "./Heatmap";
import { MainChart } from "./MainChart";
import { PnlChart } from "./PnlChart";
import { PositionsTable } from "./PositionsTable";
import { TradeBar } from "./TradeBar";
import { Watchlist } from "./Watchlist";

const PORTFOLIO_REFRESH_MS = 15_000;
const HISTORY_REFRESH_MS = 30_000;

function errorMessage(err: unknown): string {
  return err instanceof ApiError ? err.message : "Something went wrong. Try again.";
}

export function Terminal() {
  const { prices, history, version, status } = usePriceStream();
  const [portfolio, setPortfolio] = useState<Portfolio | null>(null);
  const [watchlist, setWatchlist] = useState<WatchlistItem[]>([]);
  const [snapshots, setSnapshots] = useState<Snapshot[]>([]);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [chatLoading, setChatLoading] = useState(false);
  const [chatOpen, setChatOpen] = useState(true);
  const [selected, setSelected] = useState<string | null>(null);

  const loadPortfolio = useCallback(() => api.portfolio().then(setPortfolio).catch(() => {}), []);
  const loadWatchlist = useCallback(() => api.watchlist().then((r) => setWatchlist(r.tickers)).catch(() => {}), []);
  const loadHistory = useCallback(() => api.history().then((r) => setSnapshots(r.snapshots)).catch(() => {}), []);

  useEffect(() => {
    void loadPortfolio();
    void loadWatchlist();
    void loadHistory();
    api
      .chatHistory()
      .then((r) => setMessages(r.messages))
      .catch(() => {});
    const p = setInterval(loadPortfolio, PORTFOLIO_REFRESH_MS);
    const h = setInterval(loadHistory, HISTORY_REFRESH_MS);
    return () => {
      clearInterval(p);
      clearInterval(h);
    };
  }, [loadPortfolio, loadWatchlist, loadHistory]);

  // Keep a valid selection as the watchlist changes.
  useEffect(() => {
    if (selected && (watchlist.some((w) => w.ticker === selected) || prices[selected])) return;
    setSelected(watchlist[0]?.ticker ?? null);
  }, [watchlist, selected, prices]);

  const live = useMemo(() => computeLivePortfolio(portfolio, prices), [portfolio, prices]);

  const handleTrade = useCallback(
    async (ticker: string, quantity: number, side: "buy" | "sell") => {
      try {
        const res = await api.trade(ticker, quantity, side);
        setPortfolio(res.portfolio);
        void loadHistory();
        return null;
      } catch (err) {
        return errorMessage(err);
      }
    },
    [loadHistory],
  );

  const handleAdd = useCallback(
    async (ticker: string) => {
      try {
        await api.addTicker(ticker);
        await loadWatchlist();
        setSelected(ticker);
        return null;
      } catch (err) {
        return errorMessage(err);
      }
    },
    [loadWatchlist],
  );

  const handleRemove = useCallback(
    async (ticker: string) => {
      try {
        await api.removeTicker(ticker);
        setWatchlist((items) => items.filter((w) => w.ticker !== ticker));
        void loadWatchlist();
        return null;
      } catch (err) {
        return errorMessage(err);
      }
    },
    [loadWatchlist],
  );

  const handleSend = useCallback(
    async (text: string) => {
      const pending: ChatMessage = {
        id: `local-${Date.now()}`,
        role: "user",
        content: text,
        actions: null,
        created_at: new Date().toISOString(),
      };
      setMessages((m) => [...m, pending]);
      setChatLoading(true);
      try {
        const reply = await api.chat(text);
        setMessages((m) => [...m, reply]);
        const acted = (reply.actions?.trades?.length ?? 0) + (reply.actions?.watchlist_changes?.length ?? 0) > 0;
        if (acted) {
          void loadPortfolio();
          void loadWatchlist();
          void loadHistory();
        }
      } catch (err) {
        setMessages((m) => [
          ...m,
          {
            id: `local-err-${Date.now()}`,
            role: "assistant",
            content: `I couldn't get a reply: ${errorMessage(err)}`,
            actions: null,
            created_at: new Date().toISOString(),
          },
        ]);
      } finally {
        setChatLoading(false);
      }
    },
    [loadPortfolio, loadWatchlist, loadHistory],
  );

  return (
    <div className="flex min-h-screen flex-col lg:h-screen lg:min-h-0">
      <Header
        totalValue={live.totalValue}
        cash={live.cash}
        unrealizedPnl={live.unrealizedPnl}
        unrealizedPnlPercent={live.unrealizedPnlPercent}
        status={status}
        loaded={portfolio != null}
      />

      <div className="flex min-h-0 flex-1 flex-col lg:flex-row">
        <div className="h-[420px] border-b border-line bg-panel lg:h-auto lg:w-[290px] lg:shrink-0 lg:border-r lg:border-b-0">
          <Watchlist
            items={watchlist}
            prices={prices}
            history={history}
            selected={selected}
            onSelect={setSelected}
            onAdd={handleAdd}
            onRemove={handleRemove}
          />
        </div>

        <main className="flex min-h-0 min-w-0 flex-1 flex-col gap-px bg-line">
          <div className="flex h-[340px] min-h-0 flex-col bg-panel lg:h-auto lg:flex-[1.25]">
            <div className="min-h-0 flex-1">
              <MainChart ticker={selected} points={selected ? history.get(selected) : undefined} version={version} tick={selected ? prices[selected] : undefined} />
            </div>
            <TradeBar selectedTicker={selected} onTrade={handleTrade} />
          </div>
          <div className="grid min-h-0 grid-cols-1 gap-px md:grid-cols-2 lg:flex-1">
            <div className="h-[240px] bg-panel lg:h-auto lg:min-h-0">
              <Heatmap positions={live.positions} onSelect={setSelected} />
            </div>
            <div className="h-[240px] bg-panel lg:h-auto lg:min-h-0">
              <PnlChart snapshots={snapshots} />
            </div>
          </div>
          <div className="h-[220px] bg-panel lg:h-auto lg:min-h-0 lg:flex-[0.85]">
            <PositionsTable positions={live.positions} onSelect={setSelected} />
          </div>
        </main>

        <ChatPanel
          messages={messages}
          loading={chatLoading}
          open={chatOpen}
          onToggle={() => setChatOpen((o) => !o)}
          onSend={handleSend}
        />
      </div>
    </div>
  );
}
