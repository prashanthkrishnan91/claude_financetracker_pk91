"use client";

import { cn, formatCurrency, formatPercent } from "@/lib/utils";
import type { InsightCardData, IntelRead } from "@/lib/api";
import { normalizeVisibleIntelAction } from "@/lib/visibleIntelActions";

const ACTION_STYLES: Record<string, { bg: string; text: string; border: string }> = {
  BUY: { bg: "bg-green-500/10", text: "text-green-400", border: "border-green-500/30" },
  SELL: { bg: "bg-red-500/10", text: "text-red-400", border: "border-red-500/30" },
  TRIM: { bg: "bg-yellow-500/10", text: "text-yellow-400", border: "border-yellow-500/30" },
  HOLD: { bg: "bg-blue-500/10", text: "text-blue-400", border: "border-blue-500/30" },
};


const CONVICTION_STYLES: Record<string, string> = {
  HIGH: "bg-green-500/15 text-green-300 border border-green-500/30",
  MEDIUM: "bg-yellow-500/15 text-yellow-300 border border-yellow-500/30",
  LOW: "bg-surface-elevated text-text-muted border border-border/60",
};

const CONVICTION_LABELS: Record<"HIGH" | "MEDIUM" | "LOW", string> = {
  HIGH: "High confidence",
  MEDIUM: "Moderate confidence",
  LOW: "Evidence limited",
};

export function formatCategoryLine(category?: string | null, sector?: string | null): string {
  const c = (category || "").trim();
  const s = (sector || "").trim();
  if (!c && !s) return "";
  if (!c) return s;
  if (!s) return c;
  return c.toLowerCase() === s.toLowerCase() ? c : `${c} · ${s}`;
}

export function convictionBadgeLabel(level: "HIGH" | "MEDIUM" | "LOW"): string {
  return CONVICTION_LABELS[level];
}

const STRUCTURED_SCHEMAS = new Set(["human_v2", "compact_v1"]);

function isStructuredSchema(card: InsightCardData): boolean {
  return STRUCTURED_SCHEMAS.has(card.reasoning_schema_version ?? "");
}

function mainThesis(card: InsightCardData): string {
  // For structured schemas (human_v2 / compact_v1), the memo sections
  // (primary_driver / risk_flag / action_reason) own all visible reasoning.
  // Suppress the legacy text block to prevent old indicator language from
  // appearing through the summary/thesis fallback chain.
  if (isStructuredSchema(card) && (card.primary_driver || card.risk_flag || card.action_reason)) {
    return "";
  }
  return (
    card.plain_language_explanation
    || card.thesis
    || card.reasoning_summary
    || card.summary
    || card.investment_thesis
    || card.detail
    || ""
  );
}




function reasoningSourceLabel(source?: string | null): string | null {
  if (!source) return null;
  if (source === "fresh_llm") return "Live analysis";
  if (source === "cache") return "Cached";
  if (source === "stale_db") return "Historical";
  if (source === "fallback") return "Fallback";
  if (source === "no_analyst_data") return "No analyst data";
  return null;
}

export function AgentInsightCard({ card, onClick }: { card: InsightCardData; onClick?: () => void }) {
  const action = normalizeVisibleIntelAction(card.analyst_action || card.action);
  const styles = ACTION_STYLES[action] || ACTION_STYLES.HOLD;
  const convictionLevel = _resolveConvictionLevel(card);
  const structuredSchema = isStructuredSchema(card);

  const whyThisMatters = card.primary_driver || (card.analyst_drivers || card.key_drivers || [])[0] || null;
  const whatCouldGoWrong = card.risk_flag || (card.analyst_risks || card.main_risks || [])[0] || null;
  const whatToDo = card.action_reason || null;

  return (
    <div
      onClick={onClick}
      className={cn(
        "card-glass p-3 md:p-3.5 space-y-2 border rounded-xl shadow-[inset_0_1px_0_rgba(255,255,255,0.03)]",
        styles.border,
        styles.bg,
        onClick && "cursor-pointer hover:brightness-110"
      )}
    >
      {/* Header */}
      <div className="flex items-start justify-between gap-3">
        <div>
          <div className="font-mono font-bold text-text-primary text-base md:text-lg leading-tight">{card.ticker}</div>
          <div className="text-[11px] text-text-muted">{formatCategoryLine(card.category, card.sector)}</div>
        </div>
        <div className="flex flex-col items-end gap-1 text-[10px]">
          <span className={cn("px-2 py-0.5 rounded border font-bold uppercase leading-tight", styles.bg, styles.text, styles.border)}>
            {action}
          </span>
          <span className={cn("px-2 py-0.5 rounded font-semibold uppercase", CONVICTION_STYLES[convictionLevel])}>
            {convictionBadgeLabel(convictionLevel)}
          </span>
          {card.current_price != null && (
            <span className="font-mono text-sm text-text-secondary normal-case">
              {formatCurrency(card.current_price)}
            </span>
          )}
        </div>
      </div>

      {/* Main thesis — suppressed for human_v2 cards where memo sections own the text */}
      {mainThesis(card) && (
        <p className="text-sm text-text-primary leading-snug">{mainThesis(card)}</p>
      )}

      {/* Compact 2x2 reasoning grid */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
        {whyThisMatters && (
          <MemoSection
            label="WHY"
            text={whyThisMatters}
            tone="positive"
          />
        )}
        {whatCouldGoWrong && (
          <MemoSection
            label="RISK"
            text={whatCouldGoWrong}
            tone="negative"
          />
        )}
        {whatToDo && (
          <MemoSection
            label="ACTION"
            text={whatToDo}
            tone="neutral"
          />
        )}
        {structuredSchema && card.differentiation && card.differentiation !== "—" && (
          <MemoSection
            label="ALT VIEW"
            text={card.differentiation}
            tone="neutral"
          />
        )}
      </div>


      {/* Why this view? — intel_read compact section */}
      {card.intel_read && <WhyThisView intelRead={card.intel_read} />}

      {/* Footer chips */}
      <div className="flex flex-wrap gap-1.5 pt-0.5">
        {card.pnl_pct != null && (
          <Chip
            label={`P&L ${formatPercent(card.pnl_pct)}`}
            tone={card.pnl_pct >= 0 ? "good" : "bad"}
          />
        )}
        {card.data_quality_label && (
          <Chip label={`Ticker data: ${card.data_quality_label}`} tone="neutral" />
        )}
        {(() => {
          const srcLabel = reasoningSourceLabel(card.reasoning_source) ?? (card.analysis_source === "live_llm" ? "Live analysis" : "Cached");
          const isFresh = card.reasoning_source === "fresh_llm" || (!card.reasoning_source && card.analysis_source === "live_llm");
          const isStale = card.reasoning_source === "stale_db";
          return (
            <Chip
              label={srcLabel}
              tone={isFresh ? "good" : isStale ? "bad" : "neutral"}
            />
          );
        })()}
      </div>
    </div>
  );
}

function MemoSection({
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
    <div className="rounded-md border border-border/50 bg-surface-elevated/35 px-2.5 py-1.5">
      <p className={cn("text-[10px] uppercase tracking-wide font-semibold mb-0.5", labelCls)}>
        {label}
      </p>
      <p className="text-xs text-text-secondary leading-[1.45]">{text}</p>
    </div>
  );
}

function Chip({ label, tone }: { label: string; tone: "good" | "bad" | "neutral" }) {
  const cls =
    tone === "good"
      ? "bg-green-500/10 text-green-400"
      : tone === "bad"
      ? "bg-red-500/10 text-red-400"
      : "bg-surface-elevated text-text-secondary";
  return <span className={cn("text-[10px] px-2 py-0.5 rounded-full", cls)}>{label}</span>;
}

function WhyThisView({ intelRead }: { intelRead: IntelRead }) {
  const hasTrusted = intelRead.trusted_signals.length > 0;
  const hasIncomplete = intelRead.incomplete_signals.length > 0;
  // posture_reason is the primary card-specific explanation of WHY this posture was assigned.
  // Fall back to bottom_line (insufficient-data specific), then summary.
  const displaySummary = intelRead.posture_reason || intelRead.bottom_line || intelRead.summary;

  return (
    <div className="rounded-md border border-border/30 bg-surface-elevated/20 px-2.5 py-2 space-y-1.5">
      <p className="text-[10px] uppercase tracking-wide font-semibold text-text-muted">
        Evidence check
      </p>
      <p className="text-xs text-text-secondary leading-snug">{displaySummary}</p>
      {hasTrusted && (
        <div className="flex flex-wrap gap-1 items-center">
          <span className="text-[10px] text-text-muted mr-0.5">Reliable:</span>
          {intelRead.trusted_signals.map((s) => (
            <span
              key={s}
              className="text-[10px] px-1.5 py-0.5 rounded border bg-green-500/10 text-green-400 border-green-500/20"
            >
              {s}
            </span>
          ))}
        </div>
      )}
      {hasIncomplete && (
        <div className="flex flex-wrap gap-1 items-center">
          <span className="text-[10px] text-text-muted mr-0.5">Missing:</span>
          {intelRead.incomplete_signals.map((s) => (
            <span
              key={s}
              className="text-[10px] px-1.5 py-0.5 rounded border bg-surface-elevated text-text-muted border-border/40"
            >
              {s}
            </span>
          ))}
        </div>
      )}
      <p className="text-[10px] text-text-muted leading-tight">{intelRead.caveat}</p>
    </div>
  );
}

function _resolveConvictionLevel(card: InsightCardData): "HIGH" | "MEDIUM" | "LOW" {
  const raw = (card.conviction_level || "").toUpperCase();
  if (raw === "HIGH" || raw === "MEDIUM" || raw === "LOW") return raw;
  // Derive from numeric score for legacy rows that pre-date this field
  const score = card.analyst_conviction ?? card.conviction ?? card.conviction_score ?? null;
  if (score === null) return "LOW";
  if (score >= 0.65) return "HIGH";
  if (score >= 0.35) return "MEDIUM";
  return "LOW";
}
