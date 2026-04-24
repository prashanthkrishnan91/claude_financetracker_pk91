"use client";

import { useState } from "react";
import Link from "next/link";
import { cn, formatCurrency } from "@/lib/utils";
import {
  usePortfolioSummary,
  useCashBalance,
  useSetCash,
  useDepositPlan,
  useDecisionOutcomes,
} from "@/lib/hooks";
import type {
  AllocationExclusion,
  DepositPlanResult,
  DepositRecommendation,
  DecisionLogEntry,
} from "@/lib/api";
import { InlineLoader } from "@/components/ui/Spinner";
import { Spinner } from "@/components/ui/Spinner";

export default function DepositsPage() {
  const [amount, setAmount] = useState(900);
  const { data: summary } = usePortfolioSummary();
  const portfolioBalance = summary?.total_equity ?? 0;
  const { data: deployPlan, isLoading: isPlanLoading } = useDepositPlan(amount, portfolioBalance);
  const { data: outcomes } = useDecisionOutcomes();

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

        {outcomes && outcomes.some((o) => o.return_pct != null) && (
          <OutcomePLSection outcomes={outcomes} />
        )}

        <CashOverrideWidget />

        {/* Deploy amount input */}
        <div className="card-glass p-4 space-y-3">
          <p className="text-sm text-text-secondary font-medium">Cash to deploy</p>
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
            {[500, 900, 1500, 2000].map((preset) => (
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
  const { plan, recommendations, summary, trims, notes, warning, explanation, exclusions } = deployPlan;
  const allocs = recommendations ?? [];

  return (
    <div className="space-y-4">
      {/* Recommended Deployment summary card */}
      <RecommendedDeploymentCard
        plan={plan}
        summary={summary}
        allocationCount={allocs.length}
      />

      {warning && (
        <div className="card-glass border border-yellow-500/30 bg-yellow-500/5 p-3 flex items-start gap-2">
          <span className="text-yellow-400 mt-0.5">⚠</span>
          <p className="text-sm text-yellow-300">{warning}</p>
        </div>
      )}

      {/* Link to Intel tab */}
      <Link
        href="/dashboard/recommendations"
        className="flex items-center gap-1.5 text-xs text-accent hover:text-accent-hover transition-colors font-semibold"
      >
        View full AI analysis
        <ArrowRightIcon className="w-3.5 h-3.5" />
      </Link>

      {/* Top allocation table */}
      {allocs.length === 0 ? (
        <div className="card-glass px-4 py-6 text-center text-sm text-text-muted">
          No deployment — cash preserved. See exclusions below.
        </div>
      ) : (
        <TopAllocationTable allocations={allocs} />
      )}

      {/* Why this plan */}
      <WhyThisPlanCard explanation={explanation ?? plan.intel_summary ?? notes.join(" ")} />

      {/* Skipped / excluded */}
      {exclusions && exclusions.length > 0 && (
        <SkippedSection exclusions={exclusions} />
      )}

      {/* Trims */}
      {trims.length > 0 && (
        <div className="card-glass p-4 space-y-2">
          <p className="text-xs font-semibold uppercase tracking-wide text-text-muted">
            Trim watchlist
          </p>
          {trims.map((trim) => (
            <div key={trim.ticker} className="border border-border rounded-lg px-3 py-2">
              <p className="text-sm font-mono text-text-primary">
                {trim.action} {trim.ticker}
              </p>
              <p className="text-xs text-text-secondary">{trim.market_note}</p>
              {trim.tax_note && (
                <p className="text-xs text-yellow-300 mt-1">{trim.tax_note}</p>
              )}
            </div>
          ))}
        </div>
      )}

      {/* Advanced details (collapsed by default) */}
      {allocs.length > 0 && <AdvancedDetails allocations={allocs} />}
    </div>
  );
}

function RecommendedDeploymentCard({
  plan,
  summary,
  allocationCount,
}: {
  plan: DepositPlanResult["plan"];
  summary: DepositPlanResult["summary"];
  allocationCount: number;
}) {
  return (
    <div className="card-glass p-4 space-y-3 border border-accent/20">
      <div className="flex items-start justify-between gap-3 flex-wrap">
        <div>
          <p className="text-[10px] uppercase tracking-wide font-semibold text-accent">
            Recommended Deployment
          </p>
          <p className="text-2xl font-display text-text-primary mt-1">
            Deploy {formatCurrency(plan.total_amount)}
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
      <div className="grid grid-cols-3 gap-2 text-xs">
        <div className="bg-surface-elevated rounded-md p-2">
          <p className="text-text-muted">Tickers</p>
          <p className="font-mono text-text-primary">{allocationCount}</p>
        </div>
        <div className="bg-surface-elevated rounded-md p-2">
          <p className="text-text-muted">Deployed</p>
          <p className="font-mono text-text-primary">
            {formatCurrency(summary.total_deployed)}
          </p>
        </div>
        <div className="bg-surface-elevated rounded-md p-2">
          <p className="text-text-muted">Considered</p>
          <p className="font-mono text-text-primary">
            {summary.candidates_considered ?? summary.ranked_candidates}
          </p>
        </div>
      </div>
    </div>
  );
}

function TopAllocationTable({ allocations }: { allocations: DepositRecommendation[] }) {
  return (
    <div className="card-glass overflow-hidden">
      <div className="px-4 py-3 border-b border-border">
        <p className="text-xs font-semibold uppercase tracking-wide text-text-muted">
          Top allocation
        </p>
      </div>
      <div className="divide-y divide-border">
        {/* Header */}
        <div className="hidden sm:grid grid-cols-12 gap-2 px-4 py-2 text-[10px] uppercase tracking-wide text-text-muted font-semibold bg-surface-elevated/40">
          <div className="col-span-2">Ticker</div>
          <div className="col-span-1">Action</div>
          <div className="col-span-2 text-right">Amount</div>
          <div className="col-span-2 text-right">Current</div>
          <div className="col-span-2 text-right">After</div>
          <div className="col-span-3">Reason</div>
        </div>
        {allocations.map((rec) => (
          <div
            key={rec.symbol}
            className="grid grid-cols-12 gap-2 px-4 py-3 items-center text-sm"
          >
            <div className="col-span-4 sm:col-span-2 font-mono font-bold text-text-primary">
              {rec.symbol}
            </div>
            <div className="col-span-2 sm:col-span-1">
              <span className="text-[10px] px-1.5 py-0.5 rounded bg-green-500/10 text-green-400 font-bold uppercase">
                {rec.action}
              </span>
            </div>
            <div className="col-span-6 sm:col-span-2 text-right font-mono font-semibold text-text-primary">
              {formatCurrency(rec.amount)}
            </div>
            <div className="col-span-4 sm:col-span-2 text-right font-mono text-xs text-text-muted">
              {(rec.current_weight ?? rec.portfolio_weight ?? 0).toFixed(1)}%
            </div>
            <div className="col-span-4 sm:col-span-2 text-right font-mono text-xs text-accent">
              {(rec.after_weight ?? 0).toFixed(1)}%
            </div>
            <div className="col-span-12 sm:col-span-3 text-xs text-text-secondary leading-snug">
              {rec.do || rec.rationale}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

function WhyThisPlanCard({ explanation }: { explanation?: string }) {
  if (!explanation) return null;
  return (
    <div className="card-glass p-4 space-y-2">
      <p className="text-xs font-semibold uppercase tracking-wide text-text-muted">
        Why this plan
      </p>
      <p className="text-sm text-text-secondary leading-relaxed">{explanation}</p>
    </div>
  );
}

function SkippedSection({ exclusions }: { exclusions: AllocationExclusion[] }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="card-glass overflow-hidden">
      <button
        onClick={() => setOpen(!open)}
        className="w-full px-4 py-3 flex items-center justify-between hover:bg-surface-elevated/40 transition-colors"
      >
        <p className="text-xs font-semibold uppercase tracking-wide text-text-muted">
          Skipped / excluded · {exclusions.length}
        </p>
        <ChevronIcon className={cn("w-4 h-4 text-text-muted transition-transform", open && "rotate-180")} />
      </button>
      {open && (
        <div className="border-t border-border divide-y divide-border">
          {exclusions.map((e) => (
            <div key={e.ticker} className="px-4 py-2 flex items-center justify-between gap-3 text-xs">
              <span className="font-mono font-semibold text-text-primary">{e.ticker}</span>
              <span className="text-text-muted text-right leading-snug">{e.reason}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function AdvancedDetails({ allocations }: { allocations: DepositRecommendation[] }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="card-glass overflow-hidden">
      <button
        onClick={() => setOpen(!open)}
        className="w-full px-4 py-3 flex items-center justify-between hover:bg-surface-elevated/40 transition-colors"
      >
        <p className="text-xs font-semibold uppercase tracking-wide text-text-muted">
          Advanced details
        </p>
        <ChevronIcon className={cn("w-4 h-4 text-text-muted transition-transform", open && "rotate-180")} />
      </button>
      {open && (
        <div className="border-t border-border p-4 space-y-3">
          {allocations.map((rec) => (
            <div key={rec.symbol} className="border border-border rounded-lg p-3 space-y-2">
              <div className="flex items-center justify-between text-sm">
                <span className="font-mono font-bold text-text-primary">{rec.symbol}</span>
                <span className="text-[10px] px-1.5 py-0.5 rounded bg-surface-elevated text-text-muted uppercase">
                  {rec.conviction_level ?? "—"} · conf {rec.confidence.toFixed(0)}%
                </span>
              </div>
              {rec.why && <DeployMemo label="WHY" text={rec.why} tone="positive" />}
              {rec.risk && <DeployMemo label="RISK" text={rec.risk} tone="negative" />}
              {rec.do && <DeployMemo label="ACTION" text={rec.do} tone="neutral" />}
              {rec.alt_view && rec.alt_view !== "—" && (
                <DeployMemo label="ALT VIEW" text={rec.alt_view} tone="neutral" />
              )}
              <div className="flex gap-3 flex-wrap text-[10px] text-text-muted">
                <span>Score: {rec.score?.toFixed(2) ?? "—"}</span>
                <span>Conviction: {rec.conviction_score?.toFixed(2) ?? "—"}</span>
                {rec.category && <span>Category: {rec.category}</span>}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function OutcomePLSection({ outcomes }: { outcomes: DecisionLogEntry[] }) {
  const tracked = outcomes.filter((o) => o.return_pct != null);
  if (tracked.length === 0) return null;

  return (
    <div className="space-y-2">
      <p className="text-xs font-semibold uppercase tracking-wide text-text-muted px-0.5">
        Decision P&amp;L
      </p>
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
        {tracked.map((entry) => {
          const isGain = (entry.return_pct ?? 0) >= 0;
          const daysHeld = Math.round(
            (Date.now() - new Date(entry.created_at).getTime()) / 86_400_000
          );
          const statusLabel =
            entry.status === "closed" ? (isGain ? "WIN" : "LOSS") : "ACTIVE";
          const statusStyle =
            entry.status === "closed"
              ? isGain
                ? "bg-green-500/10 text-green-400 border-green-500/30"
                : "bg-red-500/10 text-red-400 border-red-500/30"
              : "bg-blue-500/10 text-blue-400 border-blue-500/30";

          return (
            <div
              key={entry.id}
              className={cn(
                "card-glass p-3 border-l-2",
                isGain ? "border-l-green-500" : "border-l-red-500"
              )}
            >
              <div className="flex items-center justify-between gap-2">
                <div className="flex items-center gap-2 min-w-0">
                  <span className="font-mono font-bold text-text-primary text-sm">
                    {entry.ticker}
                  </span>
                  <span
                    className={cn(
                      "text-[10px] px-1.5 py-0.5 rounded-full border font-semibold uppercase shrink-0",
                      statusStyle
                    )}
                  >
                    {statusLabel}
                  </span>
                </div>
                <span
                  className={cn(
                    "font-mono font-bold text-sm shrink-0",
                    isGain ? "text-green-400" : "text-red-400"
                  )}
                >
                  {isGain ? "+" : ""}
                  {entry.return_pct!.toFixed(2)}%
                </span>
              </div>
              <div className="flex items-center justify-between mt-1.5 text-[10px] text-text-muted">
                <span>
                  {entry.price_at_decision != null && (
                    <>{formatCurrency(entry.price_at_decision)}</>
                  )}
                  {entry.current_price != null && entry.price_at_decision != null && (
                    <> → {formatCurrency(entry.current_price)}</>
                  )}
                </span>
                <span>{daysHeld}d held</span>
              </div>
            </div>
          );
        })}
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

function DeployMemo({
  label,
  text,
  tone,
}: {
  label: string;
  text: string;
  tone: "positive" | "negative" | "neutral";
}) {
  const labelCls =
    tone === "positive"
      ? "text-green-400"
      : tone === "negative"
      ? "text-red-400"
      : "text-text-muted";
  return (
    <div className="rounded-md bg-surface-elevated/40 px-3 py-1.5">
      <p className={cn("text-[10px] uppercase tracking-wide font-semibold mb-0.5", labelCls)}>
        {label}
      </p>
      <p className="text-xs text-text-secondary leading-relaxed">{text}</p>
    </div>
  );
}

// Icons
function ArrowRightIcon({ className }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2.5}>
      <path d="M5 12h14M12 5l7 7-7 7" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

function ChevronIcon({ className }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2}>
      <path d="M6 9l6 6 6-6" strokeLinecap="round" strokeLinejoin="round" />
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
