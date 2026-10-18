import { formatMoney, formatPercent, formatSignedMoney, signClass } from "@/lib/format";
import type { ConnectionStatus } from "@/lib/types";

const STATUS_LABEL: Record<ConnectionStatus, string> = {
  connected: "Live",
  reconnecting: "Reconnecting",
  disconnected: "Disconnected",
};

const STATUS_COLOR: Record<ConnectionStatus, string> = {
  connected: "bg-up",
  reconnecting: "bg-accent",
  disconnected: "bg-down",
};

interface HeaderProps {
  totalValue: number;
  cash: number;
  unrealizedPnl: number;
  unrealizedPnlPercent: number;
  status: ConnectionStatus;
  loaded: boolean;
}

export function Header({ totalValue, cash, unrealizedPnl, unrealizedPnlPercent, status, loaded }: HeaderProps) {
  return (
    <header className="flex flex-wrap items-center gap-x-8 gap-y-2 border-b border-line bg-panel px-4 py-2.5">
      <div className="flex items-baseline gap-2">
        <span className="text-[19px] font-bold tracking-tight" style={{ fontStretch: "75%" }}>
          Fin<span className="text-accent">Ally</span>
        </span>
        <span className="hidden text-muted sm:inline">trading workstation</span>
      </div>

      <div className="flex items-baseline gap-2">
        <span className="text-muted">Portfolio</span>
        <span data-testid="header-total-value" className="num text-[22px] font-semibold leading-none text-accent">
          {loaded ? formatMoney(totalValue) : "—"}
        </span>
        {loaded && (
          <span className={`num ${signClass(unrealizedPnl)}`}>
            {formatSignedMoney(unrealizedPnl)} ({formatPercent(unrealizedPnlPercent)})
          </span>
        )}
      </div>

      <div className="flex items-baseline gap-2">
        <span className="text-muted">Cash</span>
        <span data-testid="header-cash" className="num text-[15px] font-medium">
          {loaded ? formatMoney(cash) : "—"}
        </span>
      </div>

      <div
        data-testid="connection-status"
        data-status={status}
        className="ml-auto flex items-center gap-2 text-muted"
        title={`Price stream: ${STATUS_LABEL[status].toLowerCase()}`}
      >
        <span
          data-status={status}
          className={`status-dot inline-block h-2 w-2 rounded-full ${STATUS_COLOR[status]}`}
          aria-hidden
        />
        <span>{STATUS_LABEL[status]}</span>
      </div>
    </header>
  );
}
