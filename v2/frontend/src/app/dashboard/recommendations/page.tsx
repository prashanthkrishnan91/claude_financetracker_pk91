"use client";

import { cn } from "@/lib/utils";
import { useRecommendationsPanel } from "@/lib/hooks";
import { InlineLoader } from "@/components/ui/Spinner";
import { EmptyState } from "@/components/ui/EmptyState";
import {
  renderableRecommendations,
  secondaryEngineReason,
  actionBadgeStyle,
} from "@/lib/recommendations-panel";
import type { RecommendationPanelItem } from "@/lib/api";

// ── Recommendations — deterministic Intel v3 panel ────────────────────────────
//
// Every visible call carries a mandatory one-line rationale. Items without one
// are filtered out client-side (hard rule, enforced in lib/recommendations-panel).

export default function RecommendationsPage() {
  const { data: panel, isLoading, error } = useRecommendationsPanel();

  const noSnapshot = panel?.snapshot_meta?.status === "no_snapshot";
  const items = noSnapshot ? [] : renderableRecommendations(panel?.items);
  const excluded = panel?.excluded ?? [];

  return (
    <>
      {/* Top bar */}
      <header className="sticky top-0 z-50 bg-background/80 backdrop-blur-xl border-b border-border px-4 py-3">
        <div className="max-w-3xl mx-auto flex items-center justify-between">
          <div>
            <h1 className="text-xl font-display text-text-primary">Recommendations</h1>
            <p className="text-[10px] uppercase tracking-label text-text-muted opacity-50 leading-none mt-0.5">
              Deterministic Intel calls
            </p>
          </div>
          {!noSnapshot && panel?.snapshot_meta?.generated_at && (
            <span className="text-[10px] text-text-muted opacity-60">
              Snapshot {formatGeneratedAt(panel.snapshot_meta.generated_at)}
            </span>
          )}
        </div>
      </header>

      <main className="max-w-3xl mx-auto px-4 py-6 space-y-4">
        {isLoading && <InlineLoader text="Loading recommendations…" />}

        {!isLoading && !!error && (
          <EmptyState
            title="Could not load recommendations"
            description="Check your connection and try again."
          />
        )}

        {!isLoading && !error && noSnapshot && (
          <EmptyState
            title="No certified snapshot yet"
            description="No certified snapshot yet — recommendations appear after the next Intel run."
          />
        )}

        {!isLoading && !error && !noSnapshot && panel && items.length === 0 && (
          <EmptyState
            title="No recommendations to show"
            description={
              excluded.length > 0
                ? "Every holding was excluded from this run — see the list below for why."
                : "This snapshot produced no visible calls for your holdings."
            }
          />
        )}

        {!isLoading && !error && items.length > 0 && (
          <section className="space-y-3">
            {items.map(item => (
              <RecommendationCard key={item.ticker} item={item} />
            ))}
          </section>
        )}

        {/* Excluded holdings — honest, plain-English reasons */}
        {!isLoading && !error && !noSnapshot && excluded.length > 0 && (
          <section className="card-glass p-5">
            <p className="text-[10px] uppercase tracking-label text-text-muted opacity-60 mb-3">
              Not shown · {excluded.length}
            </p>
            <div className="space-y-2">
              {excluded.map(ex => (
                <div key={ex.ticker} className="flex items-baseline justify-between gap-3">
                  <span className="ticker-symbol text-xs shrink-0">{ex.ticker}</span>
                  <span className="text-[11px] text-text-muted text-right leading-snug">
                    {ex.reason}
                  </span>
                </div>
              ))}
            </div>
          </section>
        )}
      </main>
    </>
  );
}

// ── Recommendation card ───────────────────────────────────────────────────────

function RecommendationCard({ item }: { item: RecommendationPanelItem }) {
  const engineReason = secondaryEngineReason(item);

  return (
    <article className="card-glass p-5">
      <div className="flex items-start justify-between gap-3">
        {/* Ticker + name */}
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2 mb-0.5">
            <span
              className={cn(
                "text-[11px] px-2 py-0.5 rounded border font-semibold uppercase tracking-wide",
                actionBadgeStyle(item.action)
              )}
            >
              {actionLabel(item.action)}
            </span>
            <span className="ticker-symbol text-sm">{item.ticker}</span>
          </div>
          <p className="text-[11px] text-text-muted truncate leading-snug">{item.name}</p>
        </div>

        {/* Conviction + evidence chips */}
        <div className="flex flex-col items-end gap-1 shrink-0">
          <span className="text-[9px] px-1.5 py-0.5 rounded bg-surface-elevated text-text-secondary border border-border uppercase tracking-wide">
            {convictionLabel(item.conviction)}
          </span>
          <span className="text-[9px] px-1.5 py-0.5 rounded bg-surface-elevated text-text-muted border border-border uppercase tracking-wide">
            {evidenceLabel(item.evidence_band)}
          </span>
        </div>
      </div>

      {/* The one-line rationale — the reason this call is visible at all */}
      <p className="text-sm text-text-primary leading-relaxed mt-3">
        {item.rationale}
      </p>

      {/* Secondary engine reason, only when it adds something */}
      {engineReason && (
        <p className="text-[11px] text-text-muted mt-1.5 leading-snug">{engineReason}</p>
      )}
    </article>
  );
}

// ── Plain-English labels ──────────────────────────────────────────────────────

function actionLabel(action: string): string {
  switch (action?.toUpperCase()) {
    case "BUY":  return "Buy";
    case "HOLD": return "Hold";
    case "TRIM": return "Trim";
    case "SELL": return "Sell";
    default:     return "—";
  }
}

function convictionLabel(conviction: string): string {
  switch (conviction?.toUpperCase()) {
    case "HIGH":   return "High conviction";
    case "MEDIUM": return "Medium conviction";
    case "LOW":    return "Low conviction";
    default:       return "Conviction —";
  }
}

function evidenceLabel(band: string): string {
  switch (band?.toUpperCase()) {
    case "STRONG":  return "Strong evidence";
    case "PARTIAL": return "Partial evidence";
    case "THIN":    return "Thin evidence";
    default:        return "Evidence —";
  }
}

function formatGeneratedAt(iso: string): string {
  try {
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return "—";
    return d.toLocaleString(undefined, {
      month: "short",
      day: "numeric",
      hour: "numeric",
      minute: "2-digit",
    });
  } catch {
    return "—";
  }
}
