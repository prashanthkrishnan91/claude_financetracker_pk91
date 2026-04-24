"use client";

import { cn, formatCurrency, formatPercent } from "@/lib/utils";
import type { InsightCardData } from "@/lib/api";

const ACTION_STYLES: Record<string, { bg: string; text: string; border: string }> = {
  BUY: { bg: "bg-green-500/10", text: "text-green-400", border: "border-green-500/30" },
  SELL: { bg: "bg-red-500/10", text: "text-red-400", border: "border-red-500/30" },
  TRIM: { bg: "bg-yellow-500/10", text: "text-yellow-400", border: "border-yellow-500/30" },
  HOLD: { bg: "bg-blue-500/10", text: "text-blue-400", border: "border-blue-500/30" },
  REVIEW: { bg: "bg-purple-500/10", text: "text-purple-400", border: "border-purple-500/30" },
};

const CONVICTION_STYLES: Record<string, string> = {
  HIGH: "bg-green-500/15 text-green-300 border border-green-500/30",
  MEDIUM: "bg-yellow-500/15 text-yellow-300 border border-yellow-500/30",
  LOW: "bg-surface-elevated text-text-muted border border-border/60",
};

function normalizeAction(action?: string | null): string {
  const raw = (action || "").toUpperCase();
  if (raw === "REDUCE") return "TRIM";
  if (["BUY", "HOLD", "TRIM", "SELL", "REVIEW"].includes(raw)) return raw;
  return "HOLD";
}

function mainThesis(card: InsightCardData): string {
  return (
    card.plain_language_explanation
    || card.thesis
    || card.reasoning_summary
    || card.summary
    || card.investment_thesis
    || card.detail
    || `${card.ticker} remains on watch pending stronger confirmation.`
  );
}

export function AgentInsightCard({ card, onClick }: { card: InsightCardData; onClick?: () => void }) {
  const action = normalizeAction(card.analyst_action || card.action);
  const styles = ACTION_STYLES[action] || ACTION_STYLES.HOLD;
  const convictionLevel = (card.conviction_level || "LOW").toUpperCase() as "HIGH" | "MEDIUM" | "LOW";

  const whyThisMatters = card.primary_driver || (card.analyst_drivers || card.key_drivers || [])[0] || null;
  const whatCouldGoWrong = card.risk_flag || (card.analyst_risks || card.main_risks || [])[0] || null;
  const whatToDo = card.action_reason || _defaultActionReason(action, card.ticker);

  return (
    <div
      onClick={onClick}
      className={cn(
        "card-glass p-4 space-y-3 border",
        styles.border,
        styles.bg,
        onClick && "cursor-pointer hover:brightness-110"
      )}
    >
      {/* Header */}
      <div className="flex items-start justify-between gap-3">
        <div>
          <div className="font-mono font-bold text-text-primary text-base">{card.ticker}</div>
          <div className="text-xs text-text-muted">{card.category}{card.sector ? ` · ${card.sector}` : ""}</div>
        </div>
        <div className="flex flex-col items-end gap-1 text-[10px]">
          <span className={cn("px-2 py-0.5 rounded border font-bold uppercase", styles.bg, styles.text, styles.border)}>
            {action}
          </span>
          <span className={cn("px-2 py-0.5 rounded font-semibold uppercase", CONVICTION_STYLES[convictionLevel])}>
            {convictionLevel} conviction
          </span>
          {card.current_price != null && (
            <span className="font-mono text-sm text-text-secondary normal-case">
              {formatCurrency(card.current_price)}
            </span>
          )}
        </div>
      </div>

      {/* Main thesis */}
      <p className="text-sm text-text-primary leading-relaxed">{mainThesis(card)}</p>

      {/* Hedge-fund memo sections */}
      <div className="space-y-2 pt-1">
        {whyThisMatters && (
          <MemoSection
            label="Why this matters"
            text={whyThisMatters}
            tone="positive"
          />
        )}
        {whatCouldGoWrong && (
          <MemoSection
            label="What could go wrong"
            text={whatCouldGoWrong}
            tone="negative"
          />
        )}
        <MemoSection
          label="What to do now"
          text={whatToDo}
          tone="neutral"
        />
      </div>

      {/* Footer chips */}
      <div className="flex flex-wrap gap-2 pt-1">
        {card.pnl_pct != null && (
          <Chip
            label={`P&L ${formatPercent(card.pnl_pct)}`}
            tone={card.pnl_pct >= 0 ? "good" : "bad"}
          />
        )}
        {card.data_quality_label && (
          <Chip label={`Data: ${card.data_quality_label}`} tone="neutral" />
        )}
        <Chip
          label={card.analysis_source === "live_llm" ? "Live analysis" : "Cached"}
          tone={card.analysis_source === "live_llm" ? "good" : "neutral"}
        />
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
    <div className="rounded-md bg-surface-elevated/40 px-3 py-2">
      <p className={cn("text-[10px] uppercase tracking-wide font-semibold mb-0.5", labelCls)}>
        {label}
      </p>
      <p className="text-xs text-text-secondary leading-relaxed">{text}</p>
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

function _defaultActionReason(action: string, ticker: string): string {
  if (action === "BUY") return `Adding to ${ticker} now keeps you sized in proportion to conviction — consider buying in stages rather than all at once.`;
  if (action === "HOLD") return `No new information justifies changing the position. Reassess after the next earnings report or a meaningful price move.`;
  if (action === "TRIM") return `Reducing exposure gradually limits downside while keeping you in the position if the thesis recovers.`;
  if (action === "SELL") return `Exit if the original investment thesis is broken or the tax impact of holding is acceptable.`;
  return `Monitor ${ticker} and reassess when new data arrives.`;
}
