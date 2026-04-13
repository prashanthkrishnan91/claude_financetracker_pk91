"use client";

import { useState, useEffect, useCallback } from "react";
import { cn, formatCurrency, formatPercent } from "@/lib/utils";
import {
  useRecommendations,
  useRefreshRecommendations,
  useResolveRecommendation,
  useDecisionLog,
  useAgentJob,
} from "@/lib/hooks";
import { AgentInsightCard } from "@/components/cards/AgentInsightCard";
import { AgentProgressTracker } from "@/components/cards/AgentProgressTracker";
import { InlineLoader } from "@/components/ui/Spinner";
import { EmptyState } from "@/components/ui/EmptyState";
import { Spinner } from "@/components/ui/Spinner";
import type { InsightCardData, DecisionLogEntry } from "@/lib/api";

const ACTION_FILTERS = [
  { key: "ALL", label: "All", color: "bg-surface-elevated text-text-primary" },
  { key: "SELL", label: "Sell", color: "bg-red-500/10 text-red-400 border-red-500/30" },
  { key: "BUY", label: "Buy", color: "bg-green-500/10 text-green-400 border-green-500/30" },
  { key: "TRIM", label: "Trim", color: "bg-yellow-500/10 text-yellow-400 border-yellow-500/30" },
  { key: "REVIEW", label: "Review", color: "bg-purple-500/10 text-purple-400 border-purple-500/30" },
  { key: "HOLD", label: "Hold", color: "bg-blue-500/10 text-blue-400 border-blue-500/30" },
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

export default function RecommendationsPage() {
  const [filter, setFilter] = useState("ALL");
  const [selectedCard, setSelectedCard] = useState<InsightCardData | null>(null);
  const [toast, setToast] = useState<string | null>(null);
  const [decisionLogOpen, setDecisionLogOpen] = useState(false);
  const [activeJobId, setActiveJobId] = useState<string | null>(null);

  const { data: recs, isLoading, error } = useRecommendations();
  const refreshRecs = useRefreshRecommendations();
  const resolveRec = useResolveRecommendation();
  const { data: decisions } = useDecisionLog(20);
  const { data: jobStatus } = useAgentJob(activeJobId);

  // Clear the tracker shortly after a run completes so it doesn't linger.
  useEffect(() => {
    if (jobStatus?.status === "completed" || jobStatus?.status === "failed") {
      const t = setTimeout(() => setActiveJobId(null), 4000);
      return () => clearTimeout(t);
    }
  }, [jobStatus?.status]);

  const filtered =
    filter === "ALL"
      ? recs || []
      : (recs || []).filter((r) => r.action === filter);

  // Count per action
  const counts: Record<string, number> = { ALL: (recs || []).length };
  for (const r of recs || []) {
    counts[r.action] = (counts[r.action] || 0) + 1;
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
          <h1 className="text-xl font-display text-text-primary">Intel</h1>
          <button
            onClick={() =>
              refreshRecs.mutate(undefined, {
                onSuccess: (data) => {
                  setActiveJobId(data.job_id);
                  setToast("Agent pipeline queued");
                },
              })
            }
            disabled={refreshRecs.isPending || (jobStatus?.status === "running")}
            className="text-xs px-3 py-1.5 rounded-md bg-accent text-background font-semibold hover:bg-accent-hover transition-colors disabled:opacity-50"
          >
            {refreshRecs.isPending || jobStatus?.status === "running" ? (
              <Spinner className="h-3 w-3" />
            ) : (
              "Run Agents"
            )}
          </button>
        </div>
      </header>

      <main className="max-w-7xl mx-auto px-4 py-6 space-y-4">
        {/* Agent Pipeline Progress Tracker */}
        {activeJobId && jobStatus && (
          <AgentProgressTracker status={jobStatus} />
        )}

        {/* Filter cards */}
        <div className="grid grid-cols-3 sm:grid-cols-6 gap-2">
          {ACTION_FILTERS.map((f) => (
            <button
              key={f.key}
              onClick={() => setFilter(f.key)}
              className={cn(
                "px-3 py-2 rounded-lg text-xs font-semibold text-center border transition-colors",
                filter === f.key
                  ? cn(f.color, "border-current")
                  : "border-border text-text-muted hover:bg-surface-elevated"
              )}
            >
              {f.label}
              {counts[f.key] ? (
                <span className="block text-lg font-display mt-0.5">
                  {counts[f.key]}
                </span>
              ) : (
                <span className="block text-lg font-display mt-0.5 opacity-30">
                  0
                </span>
              )}
            </button>
          ))}
        </div>

        {/* Recommendations list */}
        {isLoading ? (
          <InlineLoader text="Loading recommendations..." />
        ) : error ? (
          <EmptyState title="Failed to load recommendations" />
        ) : filtered.length === 0 ? (
          <EmptyState
            title="No recommendations"
            description={
              filter === "ALL"
                ? "Hit Refresh to generate recommendations for your portfolio."
                : `No ${filter} recommendations right now.`
            }
          />
        ) : (
          <div className="space-y-3">
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

function DecisionRow({ entry }: { entry: DecisionLogEntry }) {
  const decisionStyle = DECISION_STYLES[entry.decision.toLowerCase()] || "bg-surface-elevated text-text-muted";

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
          {entry.price_at_decision !== null && entry.price_at_decision !== undefined && (
            <span className="ml-2 font-mono">{formatCurrency(entry.price_at_decision)}</span>
          )}
        </p>
      </div>
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
