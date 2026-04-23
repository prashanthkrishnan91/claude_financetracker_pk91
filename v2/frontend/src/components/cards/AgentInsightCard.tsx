"use client";

import { cn, formatCurrency, formatPercent } from "@/lib/utils";
import type { InsightCardData } from "@/lib/api";

/**
 * AgentInsightCard — enriched recommendation card that surfaces the
 * multi-agent reasoning (thesis, sentiment, technical, conviction, allocation).
 *
 * Renders a compact summary of *why* this ticker is being recommended,
 * pulling from the `investment_thesis` produced by the Portfolio Manager node.
 */

const ACTION_STYLES: Record<string, { bg: string; text: string; border: string; ring: string }> = {
  BUY:    { bg: "bg-green-500/10",  text: "text-green-400",  border: "border-green-500/30",  ring: "ring-green-500/30" },
  SELL:   { bg: "bg-red-500/10",    text: "text-red-400",    border: "border-red-500/30",    ring: "ring-red-500/30" },
  TRIM:   { bg: "bg-yellow-500/10", text: "text-yellow-400", border: "border-yellow-500/30", ring: "ring-yellow-500/30" },
  HOLD:   { bg: "bg-blue-500/10",   text: "text-blue-400",   border: "border-blue-500/30",   ring: "ring-blue-500/30" },
  REVIEW: { bg: "bg-purple-500/10", text: "text-purple-400", border: "border-purple-500/30", ring: "ring-purple-500/30" },
};

// Phase 6 — map the analyst's raw action back for the badge copy, since the
// legacy suggested_action enum folds REDUCE → TRIM and INSUFFICIENT_DATA → HOLD.
const ANALYST_ACTION_TAG: Record<string, { label: string; cls: string }> = {
  BUY:                { label: "BUY",         cls: "bg-green-500/10 text-green-400 border-green-500/30" },
  HOLD:               { label: "HOLD",        cls: "bg-blue-500/10 text-blue-400 border-blue-500/30" },
  REDUCE:             { label: "REDUCE",      cls: "bg-yellow-500/10 text-yellow-400 border-yellow-500/30" },
  INSUFFICIENT_DATA:  { label: "NO SIGNAL",   cls: "bg-surface-elevated text-text-muted border-border" },
};

const QUALITY_STYLES: Record<string, { label: string; cls: string }> = {
  HIGH:   { label: "HIGH",   cls: "bg-green-500/10 text-green-400 border-green-500/30" },
  MEDIUM: { label: "MEDIUM", cls: "bg-yellow-500/10 text-yellow-400 border-yellow-500/30" },
  LOW:    { label: "LOW",    cls: "bg-red-500/10 text-red-400 border-red-500/30" },
};

function sentimentBadge(
  score?: number | null,
  label?: string | null
): { label: string; cls: string } {
  const normalized = (label || "").toLowerCase();
  if (normalized === "unavailable") {
    return { label: "Unavailable", cls: "text-text-muted" };
  }
  if (normalized === "positive") return { label: "Positive", cls: "text-green-400" };
  if (normalized === "negative") return { label: "Negative", cls: "text-red-400" };
  if (normalized === "mixed") return { label: "Mixed", cls: "text-yellow-400" };
  if (score === undefined || score === null) return { label: "Unavailable", cls: "text-text-muted" };
  if (score >= 0.3) return { label: `bullish ${score.toFixed(2)}`, cls: "text-green-400" };
  if (score <= -0.3) return { label: `bearish ${score.toFixed(2)}`, cls: "text-red-400" };
  return { label: `neutral ${score.toFixed(2)}`, cls: "text-text-secondary" };
}

function technicalBadge(signal?: string | null): { cls: string } {
  const s = (signal || "").toUpperCase();
  if (s === "BUY") return { cls: "text-green-400 border-green-500/30" };
  if (s === "SELL") return { cls: "text-red-400 border-red-500/30" };
  if (s === "HOLD") return { cls: "text-blue-400 border-blue-500/30" };
  return { cls: "text-text-muted border-border" };
}

export function AgentInsightCard({
  card,
  onClick,
}: {
  card: InsightCardData;
  onClick?: () => void;
}) {
  const styles = ACTION_STYLES[card.action] || ACTION_STYLES.HOLD;
  const thesis = card.investment_thesis || card.detail;
  const sent = sentimentBadge(card.sentiment_score, card.sentiment_label);
  const tech = technicalBadge(card.technical_signal);
  const conviction = card.conviction_score;

  return (
    <div
      onClick={onClick}
      className={cn(
        "card-glass p-4 space-y-3 border transition-all",
        styles.border,
        styles.bg,
        onClick && "cursor-pointer hover:brightness-110 active:scale-[0.99]"
      )}
    >
      {/* Header row */}
      <div className="flex items-start justify-between gap-3">
        <div className="flex items-center gap-2 flex-wrap">
          <span
            className={cn(
              "px-2 py-0.5 rounded text-xs font-bold uppercase",
              styles.bg,
              styles.text
            )}
          >
            {card.action}
          </span>
          {/* Phase 6 — surface the raw analyst verdict when it diverges
              from the mapped suggested_action (e.g. REDUCE → TRIM,
              INSUFFICIENT_DATA → HOLD). Keeps the eye on the signal
              origin without confusing the action badge. */}
          {card.analyst_action && card.analyst_action !== card.action && (
            <span
              className={cn(
                "px-2 py-0.5 rounded text-[10px] font-semibold uppercase border",
                ANALYST_ACTION_TAG[card.analyst_action]?.cls ||
                "bg-surface-elevated text-text-muted border-border"
              )}
            >
              Analyst: {ANALYST_ACTION_TAG[card.analyst_action]?.label ||
                        card.analyst_action}
            </span>
          )}
          <span className="font-mono font-bold text-text-primary">{card.ticker}</span>
          <span className="text-xs text-text-muted">{card.category}</span>
          {card.data_quality_label && QUALITY_STYLES[card.data_quality_label] && (
            <span
              title={
                card.reason_tags && card.reason_tags.length > 0
                  ? `Data quality: ${card.data_quality_label} · ${card.reason_tags.join(", ")}`
                  : `Data quality: ${card.data_quality_label}`
              }
              className={cn(
                "px-1.5 py-0.5 rounded text-[10px] font-semibold uppercase border",
                QUALITY_STYLES[card.data_quality_label].cls
              )}
            >
              Data {QUALITY_STYLES[card.data_quality_label].label}
            </span>
          )}
        </div>
        <div className="flex items-center gap-2 shrink-0">
          {card.current_price !== undefined && card.current_price !== null && (
            <span className="font-mono text-sm text-text-secondary">
              {formatCurrency(card.current_price)}
            </span>
          )}
          {onClick && <ChevronRightIcon className="w-4 h-4 text-text-muted" />}
        </div>
      </div>

      {/* Investment thesis — the "why" */}
      {thesis && (
        <p className="text-sm text-text-primary leading-relaxed">{thesis}</p>
      )}

      {/* Phase 6 — analyst drivers & risks. Rendered WHEN the analyst
          returned a structured verdict so the card stops rehashing the
          same thesis prose for every ticker. */}
      {(card.analyst_drivers?.length || card.analyst_risks?.length) ? (
        <div className="rounded-md bg-surface-elevated/60 border border-border px-3 py-2 space-y-2">
          {card.analyst_drivers && card.analyst_drivers.length > 0 && (
            <div className="space-y-0.5">
              <span className="text-[10px] uppercase tracking-wide text-text-muted font-semibold">
                Drivers
              </span>
              <ul className="space-y-0.5">
                {card.analyst_drivers.slice(0, 3).map((d, i) => (
                  <li
                    key={i}
                    className="flex items-start gap-1.5 text-xs text-text-secondary"
                  >
                    <span className="mt-0.5 shrink-0 text-green-400">•</span>
                    <span>{d}</span>
                  </li>
                ))}
              </ul>
            </div>
          )}
          {card.analyst_risks && card.analyst_risks.length > 0 && (
            <div className="space-y-0.5">
              <span className="text-[10px] uppercase tracking-wide text-text-muted font-semibold">
                Risks
              </span>
              <ul className="space-y-0.5">
                {card.analyst_risks.slice(0, 2).map((r, i) => (
                  <li
                    key={i}
                    className="flex items-start gap-1.5 text-xs text-text-secondary"
                  >
                    <span className="mt-0.5 shrink-0 text-red-400">•</span>
                    <span>{r}</span>
                  </li>
                ))}
              </ul>
            </div>
          )}
          {card.analyst_confidence !== null &&
            card.analyst_confidence !== undefined && (
              <div className="flex items-center gap-1.5 pt-1">
                <span className="text-[10px] uppercase tracking-wide text-text-muted">
                  Analyst confidence
                </span>
                <span className="text-xs font-mono font-semibold text-text-secondary">
                  {(card.analyst_confidence * 100).toFixed(0)}%
                </span>
                {card.analyst_used_fallback && (
                  <span className="text-[10px] px-1.5 py-0.5 rounded bg-surface-elevated text-text-muted border border-border uppercase">
                    Fallback
                  </span>
                )}
              </div>
            )}
        </div>
      ) : null}

      {/* What Changed */}
      {card.what_changed && (
        <div className="rounded-md bg-surface-elevated border border-border px-3 py-2 space-y-1">
          <span className="text-[10px] uppercase tracking-wide text-text-muted font-semibold">What Changed</span>
          <ul className="space-y-0.5">
            {card.what_changed.split("\n").filter(Boolean).map((line, i) => (
              <li key={i} className="flex items-start gap-1.5 text-xs text-text-secondary">
                <span className="mt-0.5 shrink-0 text-text-muted">•</span>
                <span>{line}</span>
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* Analyst score strip */}
      <div className="flex flex-wrap items-center gap-2 pt-1">
        <span className="text-[10px] uppercase tracking-wide text-text-muted">Sentiment</span>
        <span className={cn("text-xs font-semibold", sent.cls)}>{sent.label}</span>
        <span className="text-[10px] uppercase tracking-wide text-text-muted ml-2">Tech</span>
        <span
          className={cn(
            "text-[10px] px-2 py-0.5 rounded-full border font-semibold uppercase",
            tech.cls
          )}
        >
          {card.technical_signal || "—"}
        </span>
        {conviction !== undefined && conviction !== null && (
          <>
            <span className="text-[10px] uppercase tracking-wide text-text-muted ml-2">Conviction</span>
            <ConvictionBar value={conviction} />
          </>
        )}
      </div>

      {/* Footer: allocation + pnl */}
      <div className="flex flex-wrap items-center gap-2">
        {card.pnl_pct !== null && card.pnl_pct !== undefined && (
          <span
            className={cn(
              "text-[10px] px-2 py-0.5 rounded-full font-semibold",
              card.pnl_pct >= 0
                ? "bg-green-500/10 text-green-400"
                : "bg-red-500/10 text-red-400"
            )}
          >
            P&L {formatPercent(card.pnl_pct)}
          </span>
        )}
        {card.tax_note && (
          <span className="text-[10px] px-2 py-0.5 rounded-full bg-surface-elevated text-text-muted">
            {card.tax_note}
          </span>
        )}
      </div>
    </div>
  );
}

function ConvictionBar({ value }: { value: number }) {
  // -1..+1 → width 0..100, centred at 50
  const pct = Math.max(-1, Math.min(1, value));
  const width = Math.round(Math.abs(pct) * 50);
  const isPositive = pct >= 0;
  return (
    <div className="flex items-center gap-1.5 min-w-[120px]">
      <div className="relative flex-1 h-1.5 bg-surface-elevated rounded-full overflow-hidden">
        <div className="absolute top-0 bottom-0 w-px bg-border left-1/2" />
        <div
          className={cn(
            "absolute top-0 bottom-0 rounded-full",
            isPositive ? "bg-green-400" : "bg-red-400"
          )}
          style={{
            left: isPositive ? "50%" : `${50 - width}%`,
            width: `${width}%`,
          }}
        />
      </div>
      <span className="text-[10px] font-mono text-text-secondary w-10 text-right">
        {pct >= 0 ? "+" : ""}
        {pct.toFixed(2)}
      </span>
    </div>
  );
}

function ChevronRightIcon({ className }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2}>
      <path d="M9 18l6-6-6-6" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}
