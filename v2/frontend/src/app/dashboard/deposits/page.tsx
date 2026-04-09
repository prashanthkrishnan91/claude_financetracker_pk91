"use client";

import { useState } from "react";
import { cn, formatCurrency, formatPercent } from "@/lib/utils";
import { usePortfolioSummary, useRebalance, useTargets } from "@/lib/hooks";
import { api, type RebalanceResult } from "@/lib/api";
import { InlineLoader } from "@/components/ui/Spinner";
import { EmptyState } from "@/components/ui/EmptyState";
import { useQuery } from "@tanstack/react-query";

export default function DepositsPage() {
  const [amount, setAmount] = useState(900);
  const { data: summary } = usePortfolioSummary();

  const {
    data: rebalance,
    isLoading,
    refetch,
    isFetching,
  } = useQuery({
    queryKey: ["portfolio", "rebalance", amount],
    queryFn: () => api.portfolio.getRebalance(amount),
    enabled: false,
  });

  return (
    <>
      <header className="sticky top-0 z-50 bg-background/80 backdrop-blur-xl border-b border-border px-4 py-3">
        <div className="max-w-7xl mx-auto flex items-center justify-between">
          <h1 className="text-xl font-display text-text-primary">Deploy</h1>
        </div>
      </header>

      <main className="max-w-7xl mx-auto px-4 py-6 space-y-6">
        {/* KPI row */}
        {summary && (
          <div className="grid grid-cols-3 gap-3">
            <div className="card-glass p-3 text-center">
              <p className="text-xs text-text-muted">Portfolio</p>
              <p className="font-mono text-sm text-text-primary">
                {formatCurrency(summary.total_equity)}
              </p>
            </div>
            <div className="card-glass p-3 text-center">
              <p className="text-xs text-text-muted">Cash</p>
              <p className="font-mono text-sm text-text-primary">
                {formatCurrency(summary.cash_balance)}
              </p>
            </div>
            <div className="card-glass p-3 text-center">
              <p className="text-xs text-text-muted">Positions</p>
              <p className="font-mono text-sm text-text-primary">
                {summary.positions_count}
              </p>
            </div>
          </div>
        )}

        {/* Deploy amount input */}
        <div className="card-glass p-4 space-y-3">
          <p className="text-sm text-text-secondary">Deposit Amount</p>
          <div className="flex gap-3 items-center">
            <div className="relative flex-1">
              <span className="absolute left-3 top-1/2 -translate-y-1/2 text-text-muted">
                $
              </span>
              <input
                type="number"
                value={amount}
                onChange={(e) => setAmount(Number(e.target.value))}
                className="w-full pl-7 pr-3 py-2.5 bg-surface border border-border rounded-lg text-text-primary font-mono focus:outline-none focus:ring-1 focus:ring-accent"
              />
            </div>
            <button
              onClick={() => refetch()}
              disabled={isFetching || amount <= 0}
              className="px-4 py-2.5 bg-accent text-background font-semibold rounded-lg hover:bg-accent-hover transition-colors disabled:opacity-50"
            >
              {isFetching ? "..." : "Calculate"}
            </button>
          </div>
          <div className="flex gap-2">
            {[500, 900, 1200, 1800].map((preset) => (
              <button
                key={preset}
                onClick={() => setAmount(preset)}
                className={cn(
                  "px-3 py-1 text-xs rounded-md transition-colors",
                  amount === preset
                    ? "bg-accent text-background font-semibold"
                    : "text-text-muted bg-surface-elevated hover:text-text-primary"
                )}
              >
                ${preset}
              </button>
            ))}
          </div>
        </div>

        {/* Rebalance results */}
        {isLoading || isFetching ? (
          <InlineLoader text="Calculating rebalance..." />
        ) : rebalance && rebalance.length > 0 ? (
          <div className="space-y-3">
            <h2 className="text-sm text-text-secondary font-medium">
              Suggested Allocation
            </h2>
            <div className="space-y-1.5">
              {rebalance.map((r) => (
                <RebalanceRow key={r.ticker} result={r} />
              ))}
            </div>
          </div>
        ) : rebalance && rebalance.length === 0 ? (
          <EmptyState
            title="No target allocations set"
            description="Set target allocations in Settings to see rebalance suggestions."
          />
        ) : null}
      </main>
    </>
  );
}

function RebalanceRow({ result }: { result: RebalanceResult }) {
  const isBuy = result.suggested_amount > 0;
  const isSell = result.suggested_amount < 0;
  const onTarget = result.suggested_action === "ON TARGET";

  return (
    <div className="card-glass px-4 py-3 flex items-center justify-between">
      <div className="flex-1">
        <div className="flex items-center gap-2">
          <span className="font-mono font-semibold text-text-primary text-sm">
            {result.ticker}
          </span>
          <span
            className={cn(
              "text-[10px] px-1.5 py-0.5 rounded",
              onTarget
                ? "bg-accent/10 text-accent"
                : isBuy
                ? "bg-green-500/10 text-green-400"
                : "bg-red-500/10 text-red-400"
            )}
          >
            {result.suggested_action}
          </span>
        </div>
        <div className="flex gap-3 mt-1 text-xs text-text-muted">
          <span>Current: {result.current_pct.toFixed(1)}%</span>
          <span>Target: {result.target_pct.toFixed(1)}%</span>
          <span
            className={cn(
              result.drift_pct > 0.5
                ? "text-red-400"
                : result.drift_pct < -0.5
                ? "text-green-400"
                : "text-text-muted"
            )}
          >
            Drift: {result.drift_pct > 0 ? "+" : ""}
            {result.drift_pct.toFixed(1)}%
          </span>
        </div>
      </div>
      {!onTarget && (
        <p className="font-mono text-sm text-text-primary ml-4">
          {formatCurrency(Math.abs(result.suggested_amount))}
        </p>
      )}
    </div>
  );
}
