import { formatMoney, formatPercent, formatPrice, formatQuantity, formatSignedMoney, signClass } from "@/lib/format";
import type { LivePosition } from "@/lib/portfolio";

interface PositionsTableProps {
  positions: LivePosition[];
  onSelect: (ticker: string) => void;
}

export function PositionsTable({ positions, onSelect }: PositionsTableProps) {
  return (
    <section className="flex h-full min-h-0 flex-col" aria-label="Positions">
      <div className="flex items-baseline justify-between border-b border-line px-3 py-2">
        <h2 className="font-semibold">Positions</h2>
        <span className="text-muted">{positions.length} open</span>
      </div>
      <div className="min-h-0 flex-1 overflow-auto">
        <table data-testid="positions-table" className="w-full border-collapse">
          <thead className="sticky top-0 bg-panel text-[11px] text-muted">
            <tr className="border-b border-line">
              <th className="px-3 py-1 text-left font-normal">Symbol</th>
              <th className="px-2 py-1 text-right font-normal">Qty</th>
              <th className="px-2 py-1 text-right font-normal">Avg cost</th>
              <th className="px-2 py-1 text-right font-normal">Last</th>
              <th className="px-2 py-1 text-right font-normal">Value</th>
              <th className="px-2 py-1 text-right font-normal">Unrealized P&amp;L</th>
              <th className="px-3 py-1 text-right font-normal">Return</th>
            </tr>
          </thead>
          <tbody>
            {positions.map((p) => (
              <tr
                key={p.ticker}
                data-testid={`position-row-${p.ticker}`}
                onClick={() => onSelect(p.ticker)}
                className="num cursor-pointer border-b border-line/60 hover:bg-raised"
              >
                <td className="px-3 py-1.5 font-semibold">{p.ticker}</td>
                <td className="px-2 py-1.5 text-right">{formatQuantity(p.quantity)}</td>
                <td className="px-2 py-1.5 text-right">{formatPrice(p.avg_cost)}</td>
                <td className="px-2 py-1.5 text-right">{formatPrice(p.current_price)}</td>
                <td className="px-2 py-1.5 text-right">{formatMoney(p.market_value)}</td>
                <td className={`px-2 py-1.5 text-right ${signClass(p.unrealized_pnl)}`}>{formatSignedMoney(p.unrealized_pnl)}</td>
                <td className={`px-3 py-1.5 text-right ${signClass(p.unrealized_pnl_percent)}`}>
                  {formatPercent(p.unrealized_pnl_percent)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {positions.length === 0 && (
          <p data-testid="positions-empty" className="px-3 py-6 text-center text-muted">
            No open positions. Buy from the trade bar, or ask FinAlly to place a trade.
          </p>
        )}
      </div>
    </section>
  );
}
