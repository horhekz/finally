"use client";

import { useEffect, useRef, useState } from "react";
import type { ConnectionStatus, PricePoint, PriceMap } from "@/lib/types";

export const STREAM_URL = "/api/stream/prices";
/** Per-ticker history kept for the main chart (~30 min at 2 ticks/s). */
export const MAX_HISTORY = 3600;
/** Delay before re-opening a stream the browser gave up on. */
const MANUAL_RETRY_MS = 3000;

export interface PriceStream {
  prices: PriceMap;
  /** Price history since page load, keyed by ticker. Mutated in place; `version` changes on every append. */
  history: Map<string, PricePoint[]>;
  version: number;
  status: ConnectionStatus;
}

/** Applies one SSE payload to the history buffers. Returns true if anything was appended. */
export function appendHistory(history: Map<string, PricePoint[]>, payload: PriceMap): boolean {
  let changed = false;
  for (const [ticker, tick] of Object.entries(payload)) {
    if (!tick || typeof tick.price !== "number") continue;
    let points = history.get(ticker);
    if (!points) {
      points = [];
      history.set(ticker, points);
    }
    const last = points[points.length - 1];
    if (last && last.time === tick.timestamp) continue;
    points.push({ time: tick.timestamp, value: tick.price });
    if (points.length > MAX_HISTORY) points.splice(0, points.length - MAX_HISTORY);
    changed = true;
  }
  return changed;
}

/**
 * Subscribes to the SSE price stream. EventSource retries on its own while
 * `readyState` is CONNECTING ("reconnecting"); if it closes for good, the
 * hook reports "disconnected" and opens a new stream after a short delay.
 */
export function usePriceStream(): PriceStream {
  const [prices, setPrices] = useState<PriceMap>({});
  const [status, setStatus] = useState<ConnectionStatus>("reconnecting");
  const [version, setVersion] = useState(0);
  const historyRef = useRef(new Map<string, PricePoint[]>());

  useEffect(() => {
    let es: EventSource | null = null;
    let retryTimer: ReturnType<typeof setTimeout> | undefined;
    let disposed = false;

    const connect = () => {
      es = new EventSource(STREAM_URL);
      es.onopen = () => setStatus("connected");
      es.onerror = () => {
        if (!es) return;
        if (es.readyState === EventSource.CLOSED) {
          setStatus("disconnected");
          es.close();
          if (!disposed) retryTimer = setTimeout(connect, MANUAL_RETRY_MS);
        } else {
          setStatus("reconnecting");
        }
      };
      es.onmessage = (event: MessageEvent<string>) => {
        let payload: PriceMap;
        try {
          payload = JSON.parse(event.data) as PriceMap;
        } catch {
          return;
        }
        setStatus("connected");
        setPrices(payload);
        if (appendHistory(historyRef.current, payload)) setVersion((v) => v + 1);
      };
    };

    connect();
    return () => {
      disposed = true;
      clearTimeout(retryTimer);
      es?.close();
    };
  }, []);

  return { prices, history: historyRef.current, version, status };
}
