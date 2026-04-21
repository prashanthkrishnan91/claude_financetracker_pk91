"use client";

import { useState } from "react";
import Link from "next/link";
import { cn, formatCurrency } from "@/lib/utils";
import {
  usePortfolioSummary,
  useCashBalance,
  useSetCash,
  useDepositPlan,
} from "@/lib/hooks";
import type { DepositRecommendation, DepositPlanResult } from "@/lib/api";
import { InlineLoader } from "@/components/ui/Spinner";
import { Spinner } from "@/components/ui/Spinner";

export default function DepositsPage() {
  const [amount, setAmount] = useState(900);
  const { data: summary } = usePortfolioSummary();
  const { data: deployPlan, isLoading: isPlanLoading } = useDepositPlan(amount);

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

        {/* Cash Override */}
        <CashOverrideWidget />

        {/* Deploy amount input */}
        <div className="card-glass p-4 space-y-3">
          <p className="text-sm text-text-secondary font-medium">Deposit Amount</p>
          <div className="flex gap-3 items-center">
            <div className="relative flex-1">
              <span className="absolute left-3 top-1/2 -translate-y-1/2 text-text-muted">$</span>
              <input
                type="number"
                value={amount}
                onChange={(e) => setAmount(Math.max(0, Number(e.target.value)))}
                className="w-full pl-7 pr-3 py-2.5 bg-surface border border-border rounded-lg text-text-primary font-mono focus:outline-none focus:ring-1 focus:ring-accent"
                min={0}
                step={50}
              />
            </div>
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

        {/* Deployment Plan */}
        {isPlanLoading ? (
          <InlineLoader text="Building deployment plan..." />
        ) : deployPlan ? (
          <DeploymentPlan deployPlan={deployPlan} />
        ) : null}
      </main>
    </>
  );
}

function DeploymentPlan({ deployPlan }: { deployPlan: DepositPlanResult }) {
  const [debugOpen, setDebugOpen] = useState(false);
  const { plan, recommendations, summary, debug } = deployPlan;

  return (
    <div className="space-y-4">
      {/* Plan Summary */}
      <PlanSummary plan={plan} summary={summary} />

      {/* Link to Intel tab */}
      <Link
        href="/dashboard/recommendations"
        className="flex items-center gap-1.5 text-xs text-accent hover:text-accent-hover transition-colors font-semibold"
      >
        View full AI analysis
        <ArrowRightIcon className="w-3.5 h-3.5" />
      </Link>

      {/* Recommendation List */}
      {recommendations.length === 0 ? (
        <div className="card-glass px-4 py-6 text-center text-sm text-text-muted">
          No action needed. Portfolio is aligned with targets.
        </div>
      ) : (
        <div className="space-y-3">
          {recommendations.map((rec) => (
            <RecommendationCard key={rec.symbol} rec={rec} />
          ))}
        </div>
      )}

      {/* Debug Toggle */}
      <div className="card-glass overflow-hidden">
        <button
          onClick={() => setDebugOpen((o) => !o)}
          className="w-full flex items-center justify-between px-4 py-3 text-sm text-text-secondary hover:text-text-primary transition-colors"
        >
          <span className="text-xs font-semibold uppercase tracking-wide">
            Show Advanced
          </span>
          <ChevronIcon
            className={cn(
              "w-4 h-4 transition-transform",
              debugOpen ? "rotate-180" : ""
            )}
          />
        </button>
        {debugOpen && debug?.original_plan?.actions && (
          <div className="border-t border-border p-4 space-y-3">
            {debug.original_plan.actions.map((a: { symbol: string; amount: number; delta_weight: number; deposit_date: string }, i: number) => (
              <div key={i} className="p-3 rounded-xl border border-border bg-neutral-900">
                <div className="flex justify-between">
                  <span className="font-semibold text-text-primary">{a.symbol}</span>
                  <span className="font-mono text-text-primary">${a.amount.toFixed(2)}</span>
                </div>
                <div className="text-sm text-gray-400 mt-1">
                  Target Change: {(a.delta_weight * 100).toFixed(1)}%
                </div>
                <div className="w-full bg-gray-800 h-2 rounded mt-2 overflow-hidden">
                  <div
                    className="bg-green-500 h-2 rounded"
                    style={{ width: `${Math.min(100, a.delta_weight * 100)}%` }}
                  />
                </div>
                <div className="text-xs text-gray-500 mt-1">
                  Date: {a.deposit_date}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

function PlanSummary({
  plan,
  summary,
}: {
  plan: DepositPlanResult["plan"];
  summary: DepositPlanResult["summary"];
}) {
  return (
    <div className="card-glass p-4 space-y-3">
      <div className="flex items-start justify-between gap-3 flex-wrap">
        <div>
          <p className="text-lg font-display text-text-primary">
            Investing {formatCurrency(plan.total_amount)}
          </p>
          <p className="text-xs text-text-muted mt-0.5">{plan.strategy}</p>
        </div>
        <span
          className={cn(
            "text-xs px-2.5 py-1 rounded-full font-semibold uppercase tracking-wide",
            summary.fully_allocated
              ? "bg-green-500/10 text-green-400 border border-green-500/20"
              : "bg-yellow-500/10 text-yellow-400 border border-yellow-500/20"
          )}
        >
          {summary.fully_allocated ? "Fully Allocated" : "Partial"}
        </span>
      </div>
      <div className="flex gap-4 text-xs text-text-muted">
        <span>{summary.positions_count} positions</span>
        <span>Rotating: {summary.rotating_pick}</span>
        <span className="capitalize">{summary.strategy_mode} mode</span>
      </div>
    </div>
  );
}

function RecommendationCard({ rec }: { rec: DepositRecommendation }) {
  return (
    <div className="card-glass px-4 py-4 space-y-3">
      {/* Header row */}
      <div className="flex items-center justify-between gap-3">
        <div className="flex items-center gap-2">
          <span className="font-mono font-bold text-text-primary text-base">
            {rec.symbol}
          </span>
          <span className="text-xs px-2 py-0.5 rounded bg-green-500/10 text-green-400 font-bold uppercase">
            {rec.action}
          </span>
        </div>
        <span className="font-mono font-semibold text-text-primary">
          {formatCurrency(rec.amount)}
        </span>
      </div>

      {/* Allocation bar */}
      <div className="space-y-1">
        <div className="flex items-center justify-between text-[10px] text-text-muted">
          <span>Target weight</span>
          <span className="font-mono">{rec.target_weight.toFixed(1)}%</span>
        </div>
        <div className="h-1.5 bg-surface-elevated rounded-full overflow-hidden">
          <div
            className="h-full bg-accent rounded-full transition-all"
            style={{ width: `${Math.min(100, rec.target_weight)}%` }}
          />
        </div>
      </div>

      {/* Rationale */}
      <ul className="space-y-0.5">
        <li className="flex gap-2 text-xs text-text-secondary leading-relaxed">
          <span className="text-accent shrink-0 mt-0.5">•</span>
          <span>{rec.rationale}</span>
        </li>
      </ul>

      {/* Confidence bar */}
      <div className="space-y-1">
        <div className="flex items-center justify-between text-[10px] text-text-muted">
          <span>Confidence</span>
          <span className="font-mono">{rec.confidence}%</span>
        </div>
        <div className="h-1 bg-surface-elevated rounded-full overflow-hidden">
          <div
            className={cn(
              "h-full rounded-full transition-all",
              rec.confidence >= 80
                ? "bg-green-400"
                : rec.confidence >= 60
                ? "bg-yellow-400"
                : "bg-text-muted"
            )}
            style={{ width: `${rec.confidence}%` }}
          />
        </div>
      </div>
    </div>
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

// Icons
function ChevronIcon({ className }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2}>
      <path d="M6 9l6 6 6-6" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

function ArrowRightIcon({ className }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2.5}>
      <path d="M5 12h14M12 5l7 7-7 7" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
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
