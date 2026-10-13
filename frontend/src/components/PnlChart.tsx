"use client";

import { useEffect, useRef } from "react";
import type { IChartApi, ISeriesApi } from "lightweight-charts";
import { CHART_COLORS, createAreaChart, toChartData } from "@/lib/chart";
import { formatSignedMoney, signClass } from "@/lib/format";
import type { Snapshot } from "@/lib/types";

export function PnlChart({ snapshots }: { snapshots: Snapshot[] }) {
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<{ chart: IChartApi; series: ISeriesApi<"Area"> } | null>(null);

  useEffect(() => {
    if (!containerRef.current) return;
    chartRef.current = createAreaChart(containerRef.current, CHART_COLORS.accent);
    chartRef.current.chart.applyOptions({ timeScale: { secondsVisible: false } });
    return () => {
      chartRef.current?.chart.remove();
      chartRef.current = null;
    };
  }, []);

  useEffect(() => {
    const handle = chartRef.current;
    if (!handle) return;
    const points = snapshots
      .map((s) => ({ time: Date.parse(s.recorded_at) / 1000, value: s.total_value }))
      .filter((p) => Number.isFinite(p.time))
      .sort((a, b) => a.time - b.time);
    handle.series.setData(toChartData(points));
    handle.chart.timeScale().fitContent();
  }, [snapshots]);

  const change = snapshots.length >= 2 ? snapshots[snapshots.length - 1].total_value - snapshots[0].total_value : 0;

  return (
    <section className="flex h-full min-h-0 flex-col" aria-label="Portfolio value over time">
      <div className="flex items-baseline justify-between border-b border-line px-3 py-2">
        <h2 className="font-semibold">Portfolio value</h2>
        <span className={`num ${signClass(change)}`} title="Change across the chart window">
          {snapshots.length >= 2 ? formatSignedMoney(change) : "—"}
        </span>
      </div>
      <div className="relative min-h-0 flex-1">
        <div ref={containerRef} data-testid="pnl-chart" data-points={snapshots.length} className="absolute inset-0" />
        {snapshots.length < 2 && (
          <p className="pointer-events-none absolute inset-0 flex items-center justify-center px-4 text-center text-muted">
            Value is recorded every 30 seconds and after each trade.
          </p>
        )}
      </div>
    </section>
  );
}
