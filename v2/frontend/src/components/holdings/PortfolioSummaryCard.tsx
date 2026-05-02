"use client";

import { formatCurrency, formatPercent, pnlClass, cn } from "@/lib/utils";
import { usePortfolioSummary, useRefreshPrices } from "@/lib/hooks";
import { Spinner } from "@/components/ui/Spinner";

export function PortfolioSummaryCard() {
  const { data, isLoading, error } = usePortfolioSummary();
  const refreshPrices = useRefreshPrices();

  if (isLoading) {
    return (
      <div className="space-y-3 animate-pulse">
        <div className="h-3 w-28 bg-surface-elevated rounded" />
        <div className="h-10 w-52 bg-surface-elevated rounded" />
        <div className="h-4 w-44 bg-surface-elevated rounded" />
      </div>
    );
  }

  if (error || !data) {
    return (
      <div className="space-y-1.5">
        <p className="metric-label">Total Portfolio Value</p>
        <p className="text-4xl font-display text-text-primary tracking-tight">--</p>
        <p className="text-xs text-text-muted">
          {error ? "Could not load portfolio data" : "No data available"}
        </p>
      </div>
    );
  }

  return (
    <div className="space-y-3">
      {/* Total equity */}
      <div>
        <div className="flex items-center gap-2 mb-1">
          <p className="metric-label">Total Portfolio Value</p>
          <button
            onClick={() => refreshPrices.mutate()}
            disabled={refreshPrices.isPending}
            className="text-text-muted hover:text-accent transition-colors disabled:opacity-50"
            title="Refresh all prices"
          >
            {refreshPrices.isPending ? (
              <Spinner className="h-3 w-3" />
            ) : (
              <RefreshIcon />
            )}
          </button>
        </div>
        <p className="text-4xl font-display text-text-primary tracking-tight">
          {formatCurrency(data.total_equity)}
        </p>
      </div>

      {/* Day change */}
      <div className="flex items-center gap-3 flex-wrap">
        <span className={cn("text-lg font-semibold font-mono tabular-nums", pnlClass(data.day_change))}>
          {data.day_change >= 0 ? "+" : ""}{formatCurrency(data.day_change)}
          <span className="text-sm ml-1 opacity-70">({formatPercent(data.day_change_pct)})</span>
        </span>
        <span className="metric-label">Today</span>
        <PriceHealthBadge fresh={data.prices_fresh} stale={data.prices_stale} />
      </div>

      {/* Summary stat pills */}
      <div className="flex gap-2 pt-1 overflow-x-auto pb-1">
        <SummaryPill
          label="Total Return"
          value={formatCurrency(data.total_pnl)}
          subvalue={formatPercent(data.total_pnl_pct)}
          positive={data.total_pnl >= 0}
        />
        <SummaryPill label="Cash" value={formatCurrency(data.cash_balance)} neutral />
        <SummaryPill label="Stocks" value={formatCurrency(data.stocks_value, true)} neutral />
        <SummaryPill label="ETFs" value={formatCurrency(data.etfs_value, true)} neutral />
        <SummaryPill label="Crypto" value={formatCurrency(data.crypto_value, true)} neutral />
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
    <div className="data-card px-3 py-2 min-w-[110px] shrink-0">
      <p className="metric-label mb-0.5">{label}</p>
      <p
        className={cn(
          "text-sm font-semibold font-mono tabular-nums leading-tight",
          neutral ? "text-text-primary" : positive ? "text-positive" : "text-negative"
        )}
      >
        {value}
        {subvalue && (
          <span className="text-[10px] ml-1 opacity-60 font-normal">{subvalue}</span>
        )}
      </p>
    </div>
  );
}

function PriceHealthBadge({ fresh, stale }: { fresh: number; stale: number }) {
  const total = fresh + stale;
  if (total === 0) return null;
  const allFresh = stale === 0;

  return (
    <span className={cn(allFresh ? "badge-positive" : "badge-caution")}>
      {allFresh ? "Live" : `${fresh}/${total} live`}
    </span>
  );
}

function RefreshIcon() {
  return (
    <svg className="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2}>
      <path d="M21 12a9 9 0 0 0-9-9 9.75 9.75 0 0 0-6.74 2.74L3 8" strokeLinecap="round" strokeLinejoin="round" />
      <path d="M3 3v5h5" strokeLinecap="round" strokeLinejoin="round" />
      <path d="M3 12a9 9 0 0 0 9 9 9.75 9.75 0 0 0 6.74-2.74L21 16" strokeLinecap="round" strokeLinejoin="round" />
      <path d="M16 16h5v5" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}
