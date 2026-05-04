"use client";

import { useState, useEffect, useCallback, useRef } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { cn, formatCurrency, formatPercent } from "@/lib/utils";
import {
  useRecommendations,
  useRefreshRecommendations,
  useResolveRecommendation,
  useDecisionLog,
  useAgentJob,
  useLatestAgentRun,
  useStrategyPerformance,
  invalidateRecommendationAggregateQueries,
} from "@/lib/hooks";
import { AgentInsightCard } from "@/components/cards/AgentInsightCard";
import { AgentProgressTracker } from "@/components/cards/AgentProgressTracker";
import { PortfolioSynthesisPanel } from "@/components/cards/PortfolioSynthesisPanel";
import { DataQualityBanner } from "@/components/cards/DataQualityBanner";
import { computePortfolioSynthesisFromCards } from "@/components/cards/portfolioSynthesisRuntime";
import { InlineLoader } from "@/components/ui/Spinner";
import { EmptyState } from "@/components/ui/EmptyState";
import { Spinner } from "@/components/ui/Spinner";
import type { InsightCardData, DecisionLogEntry, StrategyPerformance } from "@/lib/api";

// Intel posture filter buckets (v3): advisor-facing posture decoupled from
// broker-style BUY/HOLD/SELL which collapses all tickers into HOLD under
// insufficient_data. These buckets are derived deterministically from safe signals.
const INTEL_FILTERS = [
  { key: "ALL", label: "All", color: "bg-surface-elevated text-text-primary" },
  { key: "Add Candidate", label: "Add Candidate", color: "bg-green-500/10 text-green-400 border-green-500/30" },
  { key: "Watchlist", label: "Watchlist", color: "bg-blue-500/10 text-blue-400 border-blue-500/30" },
  { key: "Review", label: "Review", color: "bg-purple-500/10 text-purple-400 border-purple-500/30" },
  { key: "Risk Watch", label: "Risk Watch", color: "bg-red-500/10 text-red-400 border-red-500/30" },
  { key: "Trim Candidate", label: "Trim Candidate", color: "bg-yellow-500/10 text-yellow-400 border-yellow-500/30" },
] as const;

const ACTION_STYLES: Record<string, { bg: string; text: string; border: string }> = {
  BUY:    { bg: "bg-green-500/10", text: "text-green-400", border: "border-green-500/30" },
  SELL:   { bg: "bg-red-500/10", text: "text-red-400", border: "border-red-500/30" },
  TRIM:   { bg: "bg-yellow-500/10", text: "text-yellow-400", border: "border-yellow-500/30" },
  HOLD:   { bg: "bg-blue-500/10", text: "text-blue-400", border: "border-blue-500/30" },
  REVIEW: { bg: "bg-purple-500/10", text: "text-purple-400", border: "border-purple-500/30" },
};

const DECISION_STYLES: Record<string, string> = {
  accepted: "bg-green-500/10 text-green-400 border-green-500/30",
  deferred: "bg-yellow-500/10 text-yellow-400 border-yellow-500/30",
  rejected: "bg-red-500/10 text-red-400 border-red-500/30",
};

type ViewMode = "recommendations" | "performance";

export default function RecommendationsPage() {
  const [view, setView] = useState<ViewMode>("recommendations");
  const [filter, setFilter] = useState("ALL");
  const [selectedCard, setSelectedCard] = useState<InsightCardData | null>(null);
  const [toast, setToast] = useState<string | null>(null);
  const [decisionLogOpen, setDecisionLogOpen] = useState(false);
  const [activeJobId, setActiveJobId] = useState<string | null>(null);
  const [finalizingJob, setFinalizingJob] = useState(false);
  const queryClient = useQueryClient();

  const { data: recs, isLoading, error } = useRecommendations();
  const refreshRecs = useRefreshRecommendations();
  const resolveRec = useResolveRecommendation();
  const { data: decisions } = useDecisionLog(20, decisionLogOpen);
  // Only one poll owner at a time:
  // - while a specific job is active, useAgentJob owns polling
  // - otherwise, useLatestAgentRun can restore any in-flight run on mount
  const { data: latestRun } = useLatestAgentRun(!activeJobId && !finalizingJob);
  const { data: jobStatus } = useAgentJob(activeJobId);
  const { data: strategyPerf, isLoading: perfLoading } = useStrategyPerformance();
  const finalizedJobRef = useRef<string | null>(null);

  // Restore the progress tracker from the last run if it's still in-flight.
  useEffect(() => {
    if (!activeJobId && latestRun && (latestRun.status === "running" || latestRun.status === "queued" || latestRun.status === "in_progress")) {
      setActiveJobId(latestRun.id);
    }
  }, [latestRun, activeJobId]);

  // Clear the tracker shortly after a run completes so it doesn't linger.
  // Keep it visible slightly longer on failure so the user can read the
  // degraded summary before it disappears.
  useEffect(() => {
    if (!activeJobId || !jobStatus) return;
    if (!["completed", "failed", "cancelled"].includes(jobStatus.status)) return;
    if (finalizedJobRef.current === activeJobId) return;
    finalizedJobRef.current = activeJobId;
    setFinalizingJob(true);
    console.log(`[Intel] Polling stopped — ${jobStatus.status}`);

    (async () => {
      if (jobStatus.status === "completed") {
        await Promise.all([
          queryClient.invalidateQueries({ queryKey: ["recommendations"] }),
          queryClient.invalidateQueries({ queryKey: ["recommendations", "insights"] }),
        ]);
        await Promise.all([
          queryClient.refetchQueries({ queryKey: ["recommendations"], type: "active" }),
          queryClient.refetchQueries({ queryKey: ["recommendations", "insights"], type: "active" }),
        ]);
      }
      if (jobStatus.status === "failed") {
        setToast(jobStatus.error_message || "LLM failed: run ended in fallback mode");
      }
      setTimeout(() => {
        setActiveJobId(null);
        setFinalizingJob(false);
      }, 300);
    })().catch((err) => {
      console.error("[Intel] Final run refresh failed:", err);
      setTimeout(() => {
        setActiveJobId(null);
        setFinalizingJob(false);
      }, 500);
    });
  }, [activeJobId, jobStatus, queryClient]);

  // Intel posture filter uses intel_filter_bucket (v3); falls back to "Watchlist"
  // for legacy cards that pre-date this field.
  const filtered =
    filter === "ALL"
      ? recs || []
      : (recs || []).filter((r) => (r.intel_filter_bucket || "Watchlist") === filter);
  const runtimeSynthesis = computePortfolioSynthesisFromCards(recs ?? []);
  const synthesis = runtimeSynthesis ?? (jobStatus?.portfolio_synthesis ?? latestRun?.portfolio_synthesis) ?? null;

  // Count per Intel posture bucket (not per raw action).
  const counts: Record<string, number> = { ALL: (recs || []).length };
  for (const r of recs || []) {
    const bucket = r.intel_filter_bucket || "Watchlist";
    counts[bucket] = (counts[bucket] || 0) + 1;
  }

  // Close modal on Escape
  useEffect(() => {
    function handleKey(e: KeyboardEvent) {
      if (e.key === "Escape") setSelectedCard(null);
    }
    window.addEventListener("keydown", handleKey);
    return () => window.removeEventListener("keydown", handleKey);
  }, []);

  // Auto-dismiss toast
  useEffect(() => {
    if (toast) {
      const t = setTimeout(() => setToast(null), 2500);
      return () => clearTimeout(t);
    }
  }, [toast]);

  const handleResolve = useCallback(
    (resolution: string) => {
      if (!selectedCard) return;
      resolveRec.mutate(
        { recId: selectedCard.id, resolution },
        {
          onSuccess: () => {
            setSelectedCard(null);
            setToast("Decision logged");
          },
        }
      );
    },
    [selectedCard, resolveRec]
  );

  return (
    <>
      <header className="sticky top-0 z-50 bg-background/80 backdrop-blur-xl border-b border-border px-4 py-3">
        <div className="max-w-7xl mx-auto flex items-center justify-between">
          <div className="flex items-center gap-3">
            <h1 className="text-xl font-display text-text-primary">Intel</h1>
            <div className="flex rounded-md border border-border overflow-hidden text-xs">
              <button
                onClick={() => setView("recommendations")}
                className={cn(
                  "px-3 py-1 font-semibold transition-colors",
                  view === "recommendations"
                    ? "bg-accent text-background"
                    : "text-text-muted hover:text-text-primary"
                )}
              >
                Signals
              </button>
              <button
                onClick={() => setView("performance")}
                className={cn(
                  "px-3 py-1 font-semibold transition-colors border-l border-border",
                  view === "performance"
                    ? "bg-accent text-background"
                    : "text-text-muted hover:text-text-primary"
                )}
              >
                Performance
              </button>
            </div>
          </div>
          {view === "recommendations" && (
            <button
              onClick={() =>
                refreshRecs.mutate(undefined, {
                  onSuccess: (data) => {
                    invalidateRecommendationAggregateQueries(queryClient);
                    finalizedJobRef.current = null;
                    setFinalizingJob(false);
                    setActiveJobId(data.job_id);
                    setToast("Agent pipeline queued");
                  },
                })
              }
              disabled={refreshRecs.isPending || (jobStatus?.status === "running" || jobStatus?.status === "queued" || jobStatus?.status === "in_progress")}
              className="text-xs px-3 py-1.5 rounded-md bg-accent text-background font-semibold hover:bg-accent-hover transition-colors disabled:opacity-50"
            >
              {refreshRecs.isPending || jobStatus?.status === "running" || jobStatus?.status === "queued" || jobStatus?.status === "in_progress" ? (
                <Spinner className="h-3 w-3" />
              ) : (
                "Run Agents"
              )}
            </button>
          )}
        </div>
      </header>

      <main className="max-w-7xl mx-auto px-4 py-6 space-y-4 md:space-y-5">
        {view === "performance" ? (
          <StrategyPerformanceTable data={strategyPerf} isLoading={perfLoading} />
        ) : (
          <>
            {/* Agent Pipeline Progress Tracker */}
            {activeJobId && jobStatus && (
              <AgentProgressTracker status={jobStatus} />
            )}

            {/* Phase 6 — run-mode + data-quality banner. Surfaces
                FULL / DEGRADED state, HIGH / MEDIUM / LOW quality band,
                and the per-run cost ledger above the recommendations. */}
            <DataQualityBanner
              runMode={(jobStatus?.run_mode ?? latestRun?.run_mode) ?? null}
              decision={
                (jobStatus?.run_mode_decision ?? latestRun?.run_mode_decision) ?? null
              }
              cost={(jobStatus?.cost_metrics ?? latestRun?.cost_metrics) ?? null}
              cards={recs ?? []}
              synthesis={synthesis}
            />

            {/* Phase 6 — portfolio-synthesis panel. Cross-ticker themes,
                risk concentrations, and rebalancing suggestions from the
                Phase 4 synthesis LLM call. */}
            <PortfolioSynthesisPanel
              synthesis={synthesis}
            />

            {/* Filter cards — Intel posture buckets */}
            <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-2">
              {INTEL_FILTERS.map((f) => (
                <button
                  key={f.key}
                  onClick={() => setFilter(f.key)}
                  className={cn(
                    "px-3 py-2.5 rounded-lg text-xs font-semibold text-center border transition-all duration-150",
                    filter === f.key
                      ? cn(f.color, "border-current shadow-[inset_0_1px_0_rgba(255,255,255,0.06)]")
                      : "border-border/70 bg-surface-elevated/20 text-text-muted hover:bg-surface-elevated/50 hover:text-text-primary"
                  )}
                >
                  <span className="uppercase tracking-wide text-[10px]">{f.label}</span>
                  {counts[f.key] ? (
                    <span className="block text-lg font-display mt-1 leading-none">
                      {counts[f.key]}
                    </span>
                  ) : (
                    <span className="block text-lg font-display mt-1 leading-none opacity-30">
                      0
                    </span>
                  )}
                </button>
              ))}
            </div>

            {/* Recommendations list — the UI must NEVER show a blank Intel
                panel. Below we always render either the SkeletonCards,
                a targeted EmptyState tied to run status, or the cards. */}
            {isLoading ? (
              <SkeletonCards />
            ) : error ? (
              <EmptyState title="Failed to load recommendations" description="Check your connection and try again." />
            ) : filtered.length === 0 ? (
              jobStatus?.status === "running" || jobStatus?.status === "queued" || jobStatus?.status === "in_progress" ? (
                <EmptyState
                  title="AI agents analyzing..."
                  description="Signals will appear here once the pipeline completes."
                />
              ) : jobStatus?.status === "failed" || latestRun?.status === "failed" ? (
                <EmptyState
                  title="Analysis temporarily unavailable"
                  description={
                    jobStatus?.error_message ||
                    jobStatus?.summary ||
                    latestRun?.error_message ||
                    latestRun?.summary ||
                    "The agent pipeline hit an error. Tap Run Agents to retry."
                  }
                />
              ) : latestRun?.status === "completed" && !activeJobId && filter === "ALL" ? (
                <EmptyState
                  title="No analysis available"
                  description={
                    latestRun?.summary ||
                    "The last run completed but produced no signals. Check your Anthropic API key or run agents again."
                  }
                />
              ) : (
                <EmptyState
                  title={filter === "ALL" ? "No analysis yet" : `No ${filter} tickers`}
                  description={
                    filter === "ALL"
                      ? "Run agents to generate AI-powered signals for your portfolio."
                      : `No tickers in the ${filter} bucket right now.`
                  }
                />
              )
            ) : (
              <div className="space-y-2.5 md:space-y-3">
                {filtered.map((card) => (
                  <AgentInsightCard
                    key={card.id}
                    card={card}
                    onClick={() => setSelectedCard(card)}
                  />
                ))}
              </div>
            )}

            {/* Decision Log */}
            <div className="card-glass overflow-hidden">
              <button
                onClick={() => setDecisionLogOpen((o) => !o)}
                className="w-full flex items-center justify-between px-4 py-3 text-sm text-text-secondary hover:text-text-primary transition-colors"
              >
                <span className="font-semibold uppercase tracking-wide text-xs">Decision Log</span>
                <div className="flex items-center gap-2">
                  {decisions && decisions.length > 0 && (
                    <span className="text-xs text-text-muted">{decisions.length} entries</span>
                  )}
                  <ChevronIcon
                    className={cn(
                      "w-4 h-4 transition-transform",
                      decisionLogOpen ? "rotate-180" : ""
                    )}
                  />
                </div>
              </button>

              {decisionLogOpen && (
                <div className="border-t border-border">
                  {!decisions || decisions.length === 0 ? (
                    <div className="px-4 py-8 text-center text-xs text-text-muted">
                      No decisions recorded yet. Accept, defer, or reject recommendations to log them.
                    </div>
                  ) : (
                    <div className="divide-y divide-border/50 max-h-80 overflow-y-auto">
                      {decisions.map((entry) => (
                        <DecisionRow key={entry.id} entry={entry} />
                      ))}
                    </div>
                  )}
                </div>
              )}
            </div>
          </>
        )}
      </main>

      {/* Recommendation Modal */}
      {selectedCard && (
        <RecommendationModal
          card={selectedCard}
          onClose={() => setSelectedCard(null)}
          onResolve={handleResolve}
          isPending={resolveRec.isPending}
        />
      )}

      {/* Toast notification */}
      {toast && (
        <div className="fixed bottom-24 left-1/2 -translate-x-1/2 z-[100] px-4 py-2 rounded-full bg-accent text-background text-xs font-semibold shadow-lg animate-in fade-in slide-in-from-bottom-2">
          {toast}
        </div>
      )}
    </>
  );
}

type SortKey = "strategy_tag" | "avg_return" | "win_rate" | "total_trades";

function StrategyPerformanceTable({
  data,
  isLoading,
}: {
  data: StrategyPerformance[] | undefined;
  isLoading: boolean;
}) {
  const [sortKey, setSortKey] = useState<SortKey>("total_trades");
  const [sortAsc, setSortAsc] = useState(false);

  const handleSort = (key: SortKey) => {
    if (sortKey === key) {
      setSortAsc((a) => !a);
    } else {
      setSortKey(key);
      setSortAsc(false);
    }
  };

  const sorted = [...(data || [])].sort((a, b) => {
    const av = a[sortKey] ?? (sortKey === "strategy_tag" ? "" : -Infinity);
    const bv = b[sortKey] ?? (sortKey === "strategy_tag" ? "" : -Infinity);
    if (av < bv) return sortAsc ? -1 : 1;
    if (av > bv) return sortAsc ? 1 : -1;
    return 0;
  });

  const ColHeader = ({ col, label }: { col: SortKey; label: string }) => (
    <th
      className="px-4 py-2 text-left text-xs font-semibold uppercase tracking-wide text-text-muted cursor-pointer select-none hover:text-text-primary transition-colors"
      onClick={() => handleSort(col)}
    >
      {label}
      {sortKey === col && (
        <span className="ml-1">{sortAsc ? "↑" : "↓"}</span>
      )}
    </th>
  );

  if (isLoading) return <InlineLoader text="Loading strategy data..." />;
  if (!data || data.length === 0)
    return (
      <EmptyState
        title="No strategy data"
        description="Tag decisions with a strategy_tag to see performance grouped here."
      />
    );

  return (
    <div className="card-glass overflow-hidden rounded-xl">
      <div className="px-4 py-3 border-b border-border">
        <span className="text-xs font-semibold uppercase tracking-wide text-text-secondary">
          Strategy Performance
        </span>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead className="bg-surface-elevated/50">
            <tr>
              <ColHeader col="strategy_tag" label="Strategy" />
              <ColHeader col="avg_return" label="Avg Return" />
              <ColHeader col="win_rate" label="Win Rate" />
              <ColHeader col="total_trades" label="Trades" />
            </tr>
          </thead>
          <tbody className="divide-y divide-border/50">
            {sorted.map((row) => (
              <tr key={row.strategy_tag} className="hover:bg-surface-elevated/30 transition-colors">
                <td className="px-4 py-3 font-mono text-xs text-text-primary font-semibold">
                  {row.strategy_tag}
                </td>
                <td className="px-4 py-3 font-mono text-xs">
                  {row.avg_return == null ? (
                    <span className="text-text-muted">—</span>
                  ) : (
                    <span className={row.avg_return >= 0 ? "text-green-400" : "text-red-400"}>
                      {row.avg_return >= 0 ? "+" : ""}
                      {row.avg_return.toFixed(2)}%
                    </span>
                  )}
                </td>
                <td className="px-4 py-3 font-mono text-xs">
                  {row.win_rate == null ? (
                    <span className="text-text-muted">—</span>
                  ) : (
                    <span className="text-text-primary">{row.win_rate.toFixed(1)}%</span>
                  )}
                </td>
                <td className="px-4 py-3 font-mono text-xs text-text-secondary">
                  {row.total_trades}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function RecommendationModal({
  card,
  onClose,
  onResolve,
  isPending,
}: {
  card: InsightCardData;
  onClose: () => void;
  onResolve: (resolution: string) => void;
  isPending: boolean;
}) {
  const styles = ACTION_STYLES[card.action] || ACTION_STYLES.HOLD;

  // Urgency dots (1-5)
  const urgency = Math.max(1, Math.min(5, card.urgency || 1));
  const urgencyColor =
    urgency >= 4
      ? "bg-red-400"
      : urgency === 3
      ? "bg-yellow-400"
      : "bg-accent";

  return (
    <div
      className="fixed inset-0 z-[80] bg-background/95 backdrop-blur-sm flex items-center justify-center p-4"
      onClick={onClose}
    >
      <div
        className={cn(
          "card-glass w-full max-w-lg border rounded-xl p-6 space-y-5 shadow-2xl",
          styles.border
        )}
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="flex items-start justify-between gap-3">
          <div className="flex items-center gap-2 flex-wrap">
            <span
              className={cn(
                "px-2.5 py-1 rounded text-xs font-bold uppercase",
                styles.bg,
                styles.text
              )}
            >
              {card.action}
            </span>
            <span className="font-mono font-bold text-text-primary text-lg">
              {card.ticker}
            </span>
            <span className="text-sm text-text-muted">{card.name}</span>
          </div>
          <button
            onClick={onClose}
            className="text-text-muted hover:text-text-primary transition-colors shrink-0 mt-0.5"
            aria-label="Close"
          >
            <XIcon className="w-5 h-5" />
          </button>
        </div>

        {/* Price & P&L row */}
        {(card.current_price !== undefined || card.pnl_pct !== undefined) && (
          <div className="flex items-center gap-3">
            {card.current_price !== undefined && (
              <span className="font-mono text-text-primary font-semibold">
                {formatCurrency(card.current_price)}
              </span>
            )}
            {card.pnl_pct !== undefined && card.pnl_pct !== null && (
              <span
                className={cn(
                  "text-xs px-2 py-0.5 rounded-full font-semibold",
                  card.pnl_pct >= 0
                    ? "bg-green-500/10 text-green-400"
                    : "bg-red-500/10 text-red-400"
                )}
              >
                {formatPercent(card.pnl_pct)}
              </span>
            )}
          </div>
        )}

        {/* Detail text */}
        <p className="text-base text-text-primary leading-relaxed">{card.detail}</p>

        {/* Rationale */}
        {card.rationale && (
          <p className="text-sm text-text-secondary italic leading-relaxed">
            {card.rationale}
          </p>
        )}

        {/* Tax note */}
        {card.tax_note && (
          <div className="flex items-start gap-2 px-3 py-2 rounded-lg bg-yellow-500/10 border border-yellow-500/20">
            <TaxIcon className="w-4 h-4 text-yellow-400 shrink-0 mt-0.5" />
            <p className="text-xs text-yellow-300 leading-relaxed">{card.tax_note}</p>
          </div>
        )}

        {/* DRIP note */}
        {card.drip_note && (
          <div className="flex items-start gap-2 px-3 py-2 rounded-lg bg-blue-500/10 border border-blue-500/20">
            <DropletModalIcon className="w-4 h-4 text-blue-400 shrink-0 mt-0.5" />
            <p className="text-xs text-blue-300 leading-relaxed">{card.drip_note}</p>
          </div>
        )}

        {/* Urgency bar */}
        <div className="space-y-1">
          <p className="text-xs text-text-muted uppercase tracking-wide">Urgency</p>
          <div className="flex gap-1.5">
            {[1, 2, 3, 4, 5].map((dot) => (
              <div
                key={dot}
                className={cn(
                  "h-2 flex-1 rounded-full transition-colors",
                  dot <= urgency ? urgencyColor : "bg-surface-elevated"
                )}
              />
            ))}
          </div>
        </div>

        {/* Action buttons */}
        <div className="flex gap-3 pt-1">
          <button
            onClick={() => onResolve("accepted")}
            disabled={isPending}
            className="flex-1 py-2.5 rounded-lg bg-accent text-background font-semibold text-sm hover:bg-accent-hover transition-colors disabled:opacity-50"
          >
            {isPending ? <Spinner className="h-4 w-4 mx-auto" /> : "Accept"}
          </button>
          <button
            onClick={() => onResolve("deferred")}
            disabled={isPending}
            className="flex-1 py-2.5 rounded-lg bg-surface-elevated text-text-secondary font-semibold text-sm hover:text-text-primary hover:bg-border transition-colors disabled:opacity-50"
          >
            Defer
          </button>
          <button
            onClick={() => onResolve("rejected")}
            disabled={isPending}
            className="flex-1 py-2.5 rounded-lg border border-red-500/30 text-red-400 font-semibold text-sm hover:bg-red-500/10 transition-colors disabled:opacity-50"
          >
            Reject
          </button>
        </div>
      </div>
    </div>
  );
}

function outcomeLabel(entry: DecisionLogEntry): { label: string; style: string } | null {
  if (entry.return_pct == null) return null;
  if (entry.status === "closed") {
    return entry.return_pct >= 0
      ? { label: "WIN", style: "bg-green-500/10 text-green-400 border-green-500/30" }
      : { label: "LOSS", style: "bg-red-500/10 text-red-400 border-red-500/30" };
  }
  return { label: "ACTIVE", style: "bg-blue-500/10 text-blue-400 border-blue-500/30" };
}

function DecisionRow({ entry }: { entry: DecisionLogEntry }) {
  const decisionStyle = DECISION_STYLES[entry.decision.toLowerCase()] || "bg-surface-elevated text-text-muted";
  const outcome = outcomeLabel(entry);

  return (
    <div className="flex items-start gap-3 px-4 py-3 hover:bg-surface-elevated/30 transition-colors">
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2 flex-wrap">
          <span className="font-mono text-xs font-semibold text-text-primary">
            {entry.ticker}
          </span>
          <span
            className={cn(
              "text-[10px] px-2 py-0.5 rounded-full border font-semibold uppercase",
              decisionStyle
            )}
          >
            {entry.decision}
          </span>
          {outcome && (
            <span
              className={cn(
                "text-[10px] px-2 py-0.5 rounded-full border font-semibold uppercase",
                outcome.style
              )}
            >
              {outcome.label}
            </span>
          )}
          {entry.return_pct != null && (
            <span
              className={cn(
                "text-xs font-mono font-semibold",
                entry.return_pct >= 0 ? "text-green-400" : "text-red-400"
              )}
            >
              {entry.return_pct >= 0 ? "+" : ""}
              {entry.return_pct.toFixed(2)}%
            </span>
          )}
          {entry.notes && (
            <span className="text-xs text-text-muted italic truncate">{entry.notes}</span>
          )}
        </div>
        <p className="text-xs text-text-muted mt-0.5">
          {new Date(entry.created_at).toLocaleDateString("en-US", {
            month: "short",
            day: "numeric",
            year: "numeric",
          })}
          {entry.price_at_decision != null && (
            <span className="ml-2 font-mono">{formatCurrency(entry.price_at_decision)}</span>
          )}
          {entry.current_price != null && entry.price_at_decision != null && (
            <span className="ml-1 font-mono text-text-muted">
              {" "}→ {formatCurrency(entry.current_price)}
            </span>
          )}
        </p>
      </div>
    </div>
  );
}

function SkeletonCards() {
  return (
    <div className="space-y-3">
      {[1, 2, 3].map((n) => (
        <div
          key={n}
          className="card-glass rounded-xl p-4 animate-pulse space-y-3"
        >
          <div className="flex items-center gap-3">
            <div className="h-5 w-12 rounded bg-surface-elevated" />
            <div className="h-5 w-16 rounded bg-surface-elevated" />
            <div className="h-5 w-24 rounded bg-surface-elevated ml-auto" />
          </div>
          <div className="h-4 w-full rounded bg-surface-elevated" />
          <div className="h-4 w-3/4 rounded bg-surface-elevated" />
          <div className="flex gap-4 pt-1">
            <div className="h-3 w-20 rounded bg-surface-elevated" />
            <div className="h-3 w-20 rounded bg-surface-elevated" />
            <div className="h-3 w-20 rounded bg-surface-elevated" />
          </div>
        </div>
      ))}
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

function XIcon({ className }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2}>
      <path d="M18 6L6 18M6 6l12 12" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

function TaxIcon({ className }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2}>
      <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" strokeLinecap="round" strokeLinejoin="round" />
      <polyline points="14 2 14 8 20 8" />
      <line x1="9" y1="15" x2="15" y2="9" />
      <line x1="9" y1="9" x2="9" y2="9" />
      <line x1="15" y1="15" x2="15" y2="15" />
    </svg>
  );
}

function DropletModalIcon({ className }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2}>
      <path d="M12 2.69l5.66 5.66a8 8 0 1 1-11.31 0z" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}
