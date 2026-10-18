import type { PricePoint } from "@/lib/types";

interface SparklineProps {
  points: PricePoint[] | undefined;
  width?: number;
  height?: number;
  /** Number of most recent points drawn. */
  window?: number;
  testId?: string;
}

/** Inline SVG polyline, colored by direction over the visible window. */
export function Sparkline({ points, width = 84, height = 24, window = 120, testId }: SparklineProps) {
  const visible = points ? points.slice(-window) : [];
  let path = "";
  let color = "var(--color-faint)";

  if (visible.length >= 2) {
    const values = visible.map((p) => p.value);
    const min = Math.min(...values);
    const max = Math.max(...values);
    const range = max - min || 1;
    const step = width / (visible.length - 1);
    path = visible
      .map((p, i) => `${(i * step).toFixed(1)},${(height - 1 - ((p.value - min) / range) * (height - 2)).toFixed(1)}`)
      .join(" ");
    const delta = values[values.length - 1] - values[0];
    color = delta > 0 ? "var(--color-up)" : delta < 0 ? "var(--color-down)" : "var(--color-muted)";
  }

  return (
    <svg
      data-testid={testId}
      data-points={visible.length}
      width={width}
      height={height}
      viewBox={`0 0 ${width} ${height}`}
      className="block"
      aria-hidden
    >
      {path ? (
        <polyline points={path} fill="none" stroke={color} strokeWidth={1.25} strokeLinejoin="round" />
      ) : (
        <line x1={0} x2={width} y1={height / 2} y2={height / 2} stroke="var(--color-line)" strokeDasharray="2 3" />
      )}
    </svg>
  );
}
