"use client";

import { useEffect, useRef, useState, type FormEvent, type KeyboardEvent } from "react";
import { formatPrice, formatQuantity } from "@/lib/format";
import type { ChatActions, ChatMessage } from "@/lib/types";

function ActionList({ actions }: { actions: ChatActions | null }) {
  if (!actions) return null;
  const trades = actions.trades ?? [];
  const changes = actions.watchlist_changes ?? [];
  if (!trades.length && !changes.length) return null;
  return (
    <ul className="mt-1.5 flex flex-col gap-1">
      {trades.map((t, i) => {
        const ok = t.status === "executed";
        return (
          <li
            key={`t${i}`}
            data-testid="chat-action"
            data-kind="trade"
            data-status={t.status}
            className={`num rounded border px-2 py-1 text-[12px] ${ok ? "border-up/40 bg-up/10" : "border-down/40 bg-down/10"}`}
          >
            <span className={ok ? "text-up" : "text-down"}>{ok ? "Filled" : "Not filled"}</span>{" "}
            {t.side === "buy" ? "Buy" : "Sell"} {formatQuantity(t.quantity)} {t.ticker}
            {ok && t.price != null && <> at {formatPrice(t.price)}</>}
            {!ok && t.error && <span className="text-muted"> — {t.error}</span>}
          </li>
        );
      })}
      {changes.map((c, i) => {
        const ok = c.status === "executed";
        return (
          <li
            key={`w${i}`}
            data-testid="chat-action"
            data-kind="watchlist"
            data-status={c.status}
            className={`rounded border px-2 py-1 text-[12px] ${ok ? "border-blue/40 bg-blue/10" : "border-down/40 bg-down/10"}`}
          >
            <span className={ok ? "text-blue" : "text-down"}>
              {ok ? (c.action === "add" ? "Watching" : "Removed") : "Watchlist unchanged"}
            </span>{" "}
            {c.ticker}
            {!ok && c.error && <span className="text-muted"> — {c.error}</span>}
          </li>
        );
      })}
    </ul>
  );
}

interface ChatPanelProps {
  messages: ChatMessage[];
  loading: boolean;
  open: boolean;
  onToggle: () => void;
  onSend: (message: string) => void;
}

export function ChatPanel({ messages, loading, open, onToggle, onSend }: ChatPanelProps) {
  const [draft, setDraft] = useState("");
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const el = scrollRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [messages.length, loading, open]);

  const send = (e?: FormEvent) => {
    e?.preventDefault();
    const text = draft.trim();
    if (!text || loading) return;
    onSend(text);
    setDraft("");
  };

  const onKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) send(e);
  };

  return (
    <aside
      data-testid="chat-panel"
      data-open={open}
      aria-label="AI assistant"
      className={`flex min-h-0 flex-col border-line bg-panel ${open ? "lg:w-[360px]" : "lg:w-11"} border-t lg:border-t-0 lg:border-l`}
    >
      <div className={`flex items-center border-b border-line px-3 py-2 ${open ? "justify-between" : "justify-center lg:px-0"}`}>
        {open && (
          <h2 className="font-semibold">
            Ask Fin<span className="text-accent">Ally</span>
          </h2>
        )}
        <button
          type="button"
          data-testid="chat-toggle"
          onClick={onToggle}
          aria-expanded={open}
          aria-label={open ? "Collapse assistant" : "Open assistant"}
          className="rounded px-2 py-0.5 text-muted hover:bg-raised hover:text-text"
        >
          {open ? "Hide" : <span className="lg:[writing-mode:vertical-rl]">Ask FinAlly</span>}
        </button>
      </div>

      {open && (
        <>
          <div ref={scrollRef} className="flex min-h-[220px] flex-1 flex-col gap-3 overflow-y-auto px-3 py-3">
            {messages.length === 0 && !loading && (
              <div className="text-muted">
                <p>Ask about your portfolio or tell FinAlly what to do. For example:</p>
                <ul className="mt-2 flex flex-col gap-1 text-text/80">
                  <li>“How concentrated is my portfolio?”</li>
                  <li>“Buy 5 shares of NVDA”</li>
                  <li>“Add PYPL to my watchlist”</li>
                </ul>
              </div>
            )}
            {messages.map((m) => (
              <div
                key={m.id}
                data-testid="chat-message"
                data-role={m.role}
                className={m.role === "user" ? "ml-8 self-end rounded-md bg-raised px-2.5 py-1.5" : "mr-4"}
              >
                {m.role === "assistant" && <div className="mb-0.5 text-[11px] text-accent">FinAlly</div>}
                <p className="whitespace-pre-wrap">{m.content}</p>
                {m.role === "assistant" && <ActionList actions={m.actions} />}
              </div>
            ))}
            {loading && (
              <div data-testid="chat-loading" role="status" className="chat-loading-dots flex items-center gap-1 text-muted">
                FinAlly is thinking
                <span>.</span>
                <span>.</span>
                <span>.</span>
              </div>
            )}
          </div>

          <form onSubmit={send} className="flex gap-1.5 border-t border-line p-2">
            <textarea
              data-testid="chat-input"
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              onKeyDown={onKeyDown}
              rows={2}
              placeholder="Message FinAlly"
              aria-label="Message FinAlly"
              className="min-w-0 flex-1 resize-none rounded border border-line bg-ink px-2 py-1 placeholder:text-faint"
            />
            <button
              data-testid="chat-send"
              type="submit"
              disabled={loading || !draft.trim()}
              className="self-end rounded bg-purple px-3 py-1.5 font-semibold text-white hover:brightness-115 disabled:opacity-50"
            >
              Send
            </button>
          </form>
        </>
      )}
    </aside>
  );
}
