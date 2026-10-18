"use client";

import { useEffect, useRef } from "react";
import type { IChartApi, ISeriesApi } from "lightweight-charts";
import { CHART_COLORS, createAreaChart, toChartData } from "@/lib/chart";
import { formatPercent, formatPrice, signClass } from "@/lib/format";
import type { PricePoint, PriceTick } from "@/lib/types";

interface MainChartProps {
  ticker: string | null;
  points: PricePoint[] | undefined;
  /** Bumps whenever `points` grows (it is mutated in place). */
  version: number;
  tick: PriceTick | undefined;
}

export function MainChart({ ticker, points, version, tick }: MainChartProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<{ chart: IChartApi; series: ISeriesApi<"Area"> } | null>(null);
  const drawnRef = useRef<{ ticker: string | null; lastTime: number }>({ ticker: null, lastTime: 0 });

  useEffect(() => {
    if (!containerRef.current) return;
    chartRef.current = createAreaChart(containerRef.current, CHART_COLORS.blue);
    return () => {
      chartRef.current?.chart.remove();
      chartRef.current = null;
    };
  }, []);

  useEffect(() => {
    const handle = chartRef.current;
    if (!handle) return;
    const data = toChartData(points ?? []);
    const drawn = drawnRef.current;
    if (drawn.ticker !== ticker) {
      handle.series.setData(data);
    } else {
      for (const p of data) if (p.time >= drawn.lastTime) handle.series.update(p);
    }
    // The whole session stays in view as history accumulates.
    handle.chart.timeScale().fitContent();
    drawnRef.current = { ticker, lastTime: data.length ? data[data.length - 1].time : 0 };
  }, [ticker, points, version]);

  return (
    <section className="flex h-full min-h-0 flex-col" aria-label="Price chart">
      <div className="flex items-baseline gap-3 border-b border-line px-3 py-2">
        <h2 className="text-[17px] font-semibold tracking-wide">{ticker ?? "No ticker selected"}</h2>
        {tick && (
          <>
            <span className="num text-[17px] font-medium">{formatPrice(tick.price)}</span>
            <span className={`num ${signClass(tick.day_change_percent)}`}>{formatPercent(tick.day_change_percent)} today</span>
          </>
        )}
        <span className="ml-auto text-muted">Since page load</span>
      </div>
      <div className="relative min-h-0 flex-1">
        <div ref={containerRef} data-testid="main-chart" data-ticker={ticker ?? ""} className="absolute inset-0" />
        {(points?.length ?? 0) < 2 && (
          <p className="pointer-events-none absolute inset-0 flex items-center justify-center text-muted">
            {ticker ? `Collecting ${ticker} prices…` : "Select a ticker in the watchlist to chart it."}
          </p>
        )}
      </div>
    </section>
  );
}
