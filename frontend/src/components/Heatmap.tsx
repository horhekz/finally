"use client";

import { useEffect, useRef, useState } from "react";
import { formatPercent, formatSignedMoney } from "@/lib/format";
import { pnlSign, type LivePosition } from "@/lib/portfolio";
import { squarify } from "@/lib/squarify";

/** Color deepens with the size of the move and saturates at ±3% P&L. */
export function tileColor(pnlPercent: number): string {
  const t = Math.min(Math.abs(pnlPercent) / 3, 1);
  if (Math.abs(pnlPercent) < 0.005) return "rgb(39 48 61)";
  const [r, g, b] = pnlPercent > 0 ? [47, 182, 124] : [229, 83, 75];
  const base = [30, 38, 49];
  const mix = (a: number, c: number) => Math.round(a + (c - a) * (0.15 + 0.6 * t));
  return `rgb(${mix(base[0], r)} ${mix(base[1], g)} ${mix(base[2], b)})`;
}

interface HeatmapProps {
  positions: LivePosition[];
  onSelect: (ticker: string) => void;
}

export function Heatmap({ positions, onSelect }: HeatmapProps) {
  const ref = useRef<HTMLDivElement>(null);
  const [size, setSize] = useState({ w: 0, h: 0 });

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const measure = () => setSize({ w: el.clientWidth, h: el.clientHeight });
    measure();
    if (typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  // Before layout is measured (or in tests), fall back to a nominal box; tiles use percentages anyway.
  const bounds = { x: 0, y: 0, w: size.w || 100, h: size.h || 100 };
  const tiles = squarify(
    positions.map((p) => ({ value: p.market_value, item: p })),
    bounds,
  );

  return (
    <section className="flex h-full min-h-0 flex-col" aria-label="Portfolio heatmap">
      <div className="flex items-baseline justify-between border-b border-line px-3 py-2">
        <h2 className="font-semibold">Holdings</h2>
        <span className="text-muted">Size by weight, color by P&amp;L</span>
      </div>
      <div ref={ref} data-testid="portfolio-heatmap" className="relative min-h-0 flex-1 overflow-hidden">
        {tiles.map(({ x, y, w, h, item }) => {
          const small = (w / bounds.w) * (size.w || 400) < 70 || (h / bounds.h) * (size.h || 200) < 38;
          return (
            <button
              type="button"
              key={item.ticker}
              data-testid={`heatmap-tile-${item.ticker}`}
              data-pnl={pnlSign(item.unrealized_pnl)}
              onClick={() => onSelect(item.ticker)}
              title={`${item.ticker}: ${(item.weight * 100).toFixed(1)}% of portfolio, ${formatSignedMoney(item.unrealized_pnl)} (${formatPercent(item.unrealized_pnl_percent)})`}
              className="absolute flex flex-col items-start justify-start overflow-hidden border border-ink p-1.5 text-left transition-[background-color] duration-500 hover:brightness-110"
              style={{
                left: `${(x / bounds.w) * 100}%`,
                top: `${(y / bounds.h) * 100}%`,
                width: `${(w / bounds.w) * 100}%`,
                height: `${(h / bounds.h) * 100}%`,
                backgroundColor: tileColor(item.unrealized_pnl_percent),
              }}
            >
              <span className="font-semibold leading-tight">{item.ticker}</span>
              {!small && (
                <span className="num text-[11px] leading-tight text-text/85">{formatPercent(item.unrealized_pnl_percent)}</span>
              )}
            </button>
          );
        })}
        {positions.length === 0 && (
          <p className="absolute inset-0 flex items-center justify-center px-4 text-center text-muted">
            Your holdings appear here once you buy something.
          </p>
        )}
      </div>
    </section>
  );
}
