"use client";

import { useState } from "react";
import { cn, formatCurrency, formatPercent } from "@/lib/utils";
import {
  usePortfolioSummary,
  useCashBalance,
  useSetCash,
  useAiRebalance,
} from "@/lib/hooks";
import { api, type RebalanceResult, type AiAllocation } from "@/lib/api";
import { InlineLoader } from "@/components/ui/Spinner";
import { EmptyState } from "@/components/ui/EmptyState";
import { Spinner } from "@/components/ui/Spinner";
import { useQuery } from "@tanstack/react-query";

const DEPOSIT_FORMULA = [
  { ticker: "NVDA", pct: 28 },
  { ticker: "VOO", pct: 22 },
  { ticker: "VYM", pct: 17 },
  { ticker: "QQQ", pct: 17 },
  { ticker: "ROTATING", pct: 16 },
];

export default function DepositsPage() {
  const [amount, setAmount] = useState(900);
  const { data: summary } = usePortfolioSummary();
  const aiRebalance = useAiRebalance();

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
        {/* Section 1: Deposit Formula */}
        <div className="card-glass p-4 space-y-3">
          <p className="text-xs text-text-muted uppercase tracking-wide font-semibold">
            Deposit Formula
          </p>
          <div className="flex flex-wrap gap-2">
            {DEPOSIT_FORMULA.map((item) => (
              <span
                key={item.ticker}
                className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-full bg-accent/10 border border-accent/20 text-accent text-xs font-semibold"
              >
                {item.ticker}
                <span className="opacity-70">{item.pct}%</span>
              </span>
            ))}
          </div>
        </div>

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

        {/* Section 2: Cash Override */}
        <CashOverrideWidget />

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

        {/* Section 3: AI Rebalance */}
        <AiRebalanceSection />
      </main>
    </>
  );
}

function CashOverrideWidget() {
  const { data: cash, isLoading } = useCashBalance();
  const setCash = useSetCash();
  const [editing, setEditing] = useState(false);
  const [inputVal, setInputVal] = useState("");

  function startEdit() {
    setInputVal(cash?.manual_override?.toString() ?? cash?.cash_balance?.toString() ?? "0");
    setEditing(true);
  }

  function handleSave() {
    const parsed = parseFloat(inputVal);
    if (!isNaN(parsed)) {
      setCash.mutate(parsed, {
        onSuccess: () => setEditing(false),
      });
    }
  }

  function handleClear() {
    setCash.mutate(null, {
      onSuccess: () => setEditing(false),
    });
  }

  if (isLoading) return null;

  const sourceStyle =
    cash?.source === "plaid"
      ? "bg-blue-500/10 text-blue-400 border-blue-500/20"
      : cash?.source === "manual"
      ? "bg-yellow-500/10 text-yellow-400 border-yellow-500/20"
      : "bg-surface-elevated text-text-muted border-border";

  return (
    <div className="card-glass p-4 space-y-3">
      <div className="flex items-center justify-between">
        <p className="text-xs text-text-muted uppercase tracking-wide font-semibold">
          Cash Balance
        </p>
        {cash?.source && (
          <span className={cn("text-[10px] px-2 py-0.5 rounded-full border font-semibold uppercase", sourceStyle)}>
            {cash.source}
          </span>
        )}
      </div>

      {editing ? (
        <div className="space-y-2">
          <div className="flex gap-2 items-center">
            <div className="relative flex-1">
              <span className="absolute left-3 top-1/2 -translate-y-1/2 text-text-muted text-sm">$</span>
              <input
                type="number"
                value={inputVal}
                onChange={(e) => setInputVal(e.target.value)}
                className="w-full pl-7 pr-3 py-2 bg-surface border border-border rounded-lg text-text-primary font-mono text-sm focus:outline-none focus:ring-1 focus:ring-accent"
                autoFocus
              />
            </div>
            <button
              onClick={handleSave}
              disabled={setCash.isPending}
              className="px-3 py-2 bg-accent text-background rounded-lg text-xs font-semibold hover:bg-accent-hover disabled:opacity-50 transition-colors"
            >
              {setCash.isPending ? <Spinner className="h-3 w-3" /> : "Save"}
            </button>
            <button
              onClick={() => setEditing(false)}
              className="px-3 py-2 bg-surface-elevated text-text-muted rounded-lg text-xs hover:text-text-primary transition-colors"
            >
              Cancel
            </button>
          </div>
          {cash?.manual_override !== null && cash?.manual_override !== undefined && (
            <button
              onClick={handleClear}
              className="text-xs text-danger hover:text-danger/80 transition-colors"
            >
              Clear override
            </button>
          )}
        </div>
      ) : (
        <div className="flex items-center justify-between">
          <span className="font-mono text-text-primary font-semibold">
            {cash ? formatCurrency(cash.cash_balance) : "—"}
          </span>
          <button
            onClick={startEdit}
            className="p-1.5 text-text-muted hover:text-text-primary transition-colors rounded-md hover:bg-surface-elevated"
            aria-label="Edit cash balance"
          >
            <PencilIcon className="w-4 h-4" />
          </button>
        </div>
      )}
    </div>
  );
}

function AiRebalanceSection() {
  const aiRebalance = useAiRebalance();

  return (
    <div className="card-glass p-4 space-y-4">
      <div className="flex items-center justify-between">
        <h2 className="text-sm font-semibold text-text-secondary uppercase tracking-wide">
          AI Portfolio Analysis
        </h2>
      </div>

      {!aiRebalance.data && !aiRebalance.isPending && !aiRebalance.isError && (
        <p className="text-xs text-text-muted">
          Generate AI-powered portfolio allocation suggestions based on your current holdings.
        </p>
      )}

      <button
        onClick={() => aiRebalance.mutate()}
        disabled={aiRebalance.isPending}
        className="flex items-center gap-2 px-4 py-2.5 bg-surface-elevated text-text-primary font-semibold text-sm rounded-lg hover:bg-border transition-colors disabled:opacity-50 border border-border"
      >
        {aiRebalance.isPending ? (
          <>
            <Spinner className="h-4 w-4" />
            Analyzing portfolio...
          </>
        ) : (
          <>
            <span>✨</span>
            Generate AI Targets
          </>
        )}
      </button>

      {aiRebalance.isError && (
        <div className="px-3 py-2 rounded-lg bg-yellow-500/10 border border-yellow-500/20">
          <p className="text-xs text-yellow-300">
            Configure Anthropic API key in Settings to use AI features.
          </p>
        </div>
      )}

      {aiRebalance.data && (
        <div className="space-y-5">
          {/* Allocation table */}
          <div className="space-y-2">
            <h3 className="text-xs font-semibold text-text-muted uppercase tracking-wide">
              Suggested Allocations
            </h3>
            <div className="overflow-x-auto">
              <table className="w-full text-xs">
                <thead>
                  <tr className="border-b border-border text-text-muted">
                    <th className="text-left py-2 pr-3 font-medium">Ticker</th>
                    <th className="text-right py-2 px-3 font-medium">Current %</th>
                    <th className="text-right py-2 px-3 font-medium">Suggested %</th>
                    <th className="text-right py-2 px-3 font-medium">Change</th>
                    <th className="text-left py-2 pl-3 font-medium">Rationale</th>
                  </tr>
                </thead>
                <tbody>
                  {aiRebalance.data.allocation_table.map((row) => (
                    <AiAllocationRow key={row.ticker} row={row} />
                  ))}
                </tbody>
              </table>
            </div>
          </div>

          {/* Narrative */}
          {aiRebalance.data.narrative && (
            <div className="space-y-2">
              <h3 className="text-xs font-semibold text-text-muted uppercase tracking-wide">
                Analysis
              </h3>
              <div className="space-y-1.5">
                {aiRebalance.data.narrative
                  .split(/\n|•|·/)
                  .map((line) => line.trim())
                  .filter(Boolean)
                  .map((line, i) => (
                    <p key={i} className="text-xs text-text-secondary leading-relaxed flex gap-2">
                      <span className="text-accent shrink-0 mt-0.5">•</span>
                      <span>{line}</span>
                    </p>
                  ))}
              </div>
            </div>
          )}

          {/* Timestamp */}
          <p className="text-[10px] text-text-muted">
            Generated:{" "}
            {new Date(aiRebalance.data.generated_at).toLocaleString("en-US", {
              month: "short",
              day: "numeric",
              hour: "2-digit",
              minute: "2-digit",
            })}
          </p>
        </div>
      )}
    </div>
  );
}

function AiAllocationRow({ row }: { row: AiAllocation }) {
  const changePositive = row.change_pct > 0;
  const changeNegative = row.change_pct < 0;

  return (
    <tr className="border-b border-border/50 hover:bg-surface-elevated/50 transition-colors">
      <td className="py-2 pr-3">
        <span className="font-mono font-semibold text-text-primary">{row.ticker}</span>
      </td>
      <td className="py-2 px-3 text-right font-mono text-text-muted">
        {row.current_pct.toFixed(1)}%
      </td>
      <td className="py-2 px-3 text-right font-mono text-text-primary">
        {row.suggested_pct.toFixed(1)}%
      </td>
      <td className="py-2 px-3 text-right">
        <span
          className={cn(
            "font-mono font-semibold",
            changePositive ? "text-accent" : changeNegative ? "text-danger" : "text-text-muted"
          )}
        >
          {row.change_pct > 0 ? "+" : ""}
          {row.change_pct.toFixed(1)}%
        </span>
      </td>
      <td className="py-2 pl-3 text-text-muted max-w-[200px]">
        <span className="truncate block" title={row.rationale}>
          {row.rationale}
        </span>
      </td>
    </tr>
  );
}

function RebalanceRow({ result }: { result: RebalanceResult }) {
  const isBuy = result.suggested_amount > 0;
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

function PencilIcon({ className }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2}>
      <path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7" strokeLinecap="round" strokeLinejoin="round" />
      <path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}
