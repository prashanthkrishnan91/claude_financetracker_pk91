"use client";

/**
 * IntelV3Card — Premium held-position card for the v3 Intel cockpit.
 *
 * Renders ONLY from IntelV3HeldCard (v3 snapshot). Never reads legacy InsightCardData.
 * Actions rendered: BUY / HOLD / TRIM / SELL only.
 * No posture labels. No raw metric keys. No price targets. No allocation math.
 */

import { cn } from "@/lib/utils";
import type { IntelV3HeldCard, IntelV3Action, IntelV3Conviction } from "@/lib/api";

interface IntelV3CardProps {
  card: IntelV3HeldCard;
  onSelect: (card: IntelV3HeldCard) => void;
}

// LOCKED: v3 visible action set. Do not add posture labels or radar labels here.
const ACTION_STYLES: Record<IntelV3Action, { bg: string; text: string; border: string; dot: string }> = {
  BUY:  { bg: "bg-green-500/10",  text: "text-green-400",  border: "border-green-500/30",  dot: "bg-green-400" },
  HOLD: { bg: "bg-blue-500/10",   text: "text-blue-400",   border: "border-blue-500/30",   dot: "bg-blue-400"  },
  TRIM: { bg: "bg-amber-500/10",  text: "text-amber-400",  border: "border-amber-500/30",  dot: "bg-amber-400" },
  SELL: { bg: "bg-red-500/10",    text: "text-red-400",    border: "border-red-500/30",    dot: "bg-red-400"   },
};

const CONVICTION_DOTS: Record<IntelV3Conviction, number> = {
  LOW: 1, MEDIUM: 2, HIGH: 3,
};

function ConvictionDots({ conviction, dotColor }: { conviction: IntelV3Conviction; dotColor: string }) {
  const filled = CONVICTION_DOTS[conviction];
  return (
    <span className="flex items-center gap-0.5" title={`Conviction: ${conviction}`}>
      {[1, 2, 3].map((n) => (
        <span
          key={n}
          className={cn(
            "w-1.5 h-1.5 rounded-full",
            n <= filled ? dotColor : "bg-surface-elevated"
          )}
        />
      ))}
    </span>
  );
}

function EvidencePill({ band }: { band: string }) {
  const styles: Record<string, string> = {
    STRONG:  "bg-emerald-500/10 text-emerald-400 border-emerald-500/20",
    PARTIAL: "bg-sky-500/10 text-sky-400 border-sky-500/20",
    THIN:    "bg-zinc-500/10 text-zinc-400 border-zinc-500/20",
  };
  const labels: Record<string, string> = {
    STRONG: "Strong evidence",
    PARTIAL: "Partial data",
    THIN: "Thin data",
  };
  return (
    <span className={cn(
      "text-[10px] px-1.5 py-0.5 rounded border font-medium tracking-wide uppercase",
      styles[band] ?? "bg-zinc-500/10 text-zinc-400 border-zinc-500/20"
    )}>
      {labels[band] ?? band}
    </span>
  );
}

export function IntelV3Card({ card, onSelect }: IntelV3CardProps) {
  const actionStyle = ACTION_STYLES[card.action] ?? ACTION_STYLES.HOLD;

  return (
    <button
      onClick={() => onSelect(card)}
      className={cn(
        "w-full text-left rounded-xl border p-4 transition-all duration-150",
        "bg-surface hover:bg-surface-elevated",
        "border-border hover:border-border-hover",
        "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/60"
      )}
      aria-label={`${card.ticker} — ${card.action}`}
    >
      {/* Header row */}
      <div className="flex items-start justify-between gap-2 mb-3">
        <div className="flex items-center gap-2 min-w-0">
          <span className="text-sm font-bold text-text-primary font-mono tracking-wide">
            {card.ticker}
          </span>
          <span className="text-xs text-text-muted truncate max-w-[120px]">
            {card.name}
          </span>
        </div>
        <div className="flex items-center gap-1.5 shrink-0">
          {/* Action badge — v3 locked set only */}
          <span className={cn(
            "text-xs font-bold px-2 py-0.5 rounded border tracking-wider uppercase",
            actionStyle.bg, actionStyle.text, actionStyle.border
          )}>
            {card.action}
          </span>
          <ConvictionDots conviction={card.conviction} dotColor={actionStyle.dot} />
        </div>
      </div>

      {/* Why text — one sentence, plain English */}
      <p className="text-xs text-text-secondary leading-relaxed mb-3 line-clamp-2">
        {card.why_text}
      </p>

      {/* Footer chips */}
      <div className="flex items-center gap-1.5 flex-wrap">
        <EvidencePill band={card.evidence_band} />
        <span className="text-[10px] px-1.5 py-0.5 rounded border border-border text-text-muted uppercase tracking-wide">
          {card.portfolio_fit}
        </span>
        {card.risk_level !== "LOW" && card.risk_level !== "UNKNOWN" && (
          <span className={cn(
            "text-[10px] px-1.5 py-0.5 rounded border font-medium uppercase tracking-wide",
            card.risk_level === "HIGH"
              ? "bg-red-500/10 text-red-400 border-red-500/20"
              : "bg-amber-500/10 text-amber-400 border-amber-500/20"
          )}>
            {card.risk_level} risk
          </span>
        )}
      </div>
    </button>
  );
}
