"use client";

import { formatCurrency, formatPercent, pnlClass, cn } from "@/lib/utils";

// Phase 3: Replace with real data from useQuery + api.portfolio.getSummary()
const MOCK_SUMMARY = {
  total_equity: 48562.34,
  total_pnl: 10234.56,
  total_pnl_pct: 26.72,
  day_change: 156.78,
  day_change_pct: 0.32,
  cash_balance: 1042.17,
};

export function PortfolioSummaryCard() {
  const data = MOCK_SUMMARY;

  return (
    <div className="space-y-2">
      {/* Total equity — large hero number */}
      <div>
        <p className="text-sm text-text-secondary">Total Portfolio Value</p>
        <p className="text-4xl font-display text-text-primary tracking-tight">
          {formatCurrency(data.total_equity)}
        </p>
      </div>

      {/* Day change + total P&L */}
      <div className="flex items-center gap-4">
        <span className={cn("text-lg font-semibold", pnlClass(data.day_change))}>
          {formatCurrency(data.day_change)} ({formatPercent(data.day_change_pct)})
        </span>
        <span className="text-xs text-text-muted">Today</span>
      </div>

      {/* Summary pills */}
      <div className="flex gap-3 pt-2 overflow-x-auto">
        <SummaryPill
          label="Total Return"
          value={formatCurrency(data.total_pnl)}
          subvalue={formatPercent(data.total_pnl_pct)}
          positive={data.total_pnl >= 0}
        />
        <SummaryPill
          label="Cash"
          value={formatCurrency(data.cash_balance)}
          neutral
        />
      </div>
    </div>
  );
}

function SummaryPill({
  label,
  value,
  subvalue,
  positive,
  neutral,
}: {
  label: string;
  value: string;
  subvalue?: string;
  positive?: boolean;
  neutral?: boolean;
}) {
  return (
    <div className="card-glass px-4 py-2 min-w-[140px]">
      <p className="text-xs text-text-muted">{label}</p>
      <p
        className={cn(
          "text-sm font-semibold",
          neutral
            ? "text-text-primary"
            : positive
            ? "pnl-positive"
            : "pnl-negative"
        )}
      >
        {value}
        {subvalue && (
          <span className="text-xs ml-1 opacity-70">{subvalue}</span>
        )}
      </p>
    </div>
  );
}
