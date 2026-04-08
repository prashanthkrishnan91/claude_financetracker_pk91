"use client";

import { cn, formatCurrency, formatPercent } from "@/lib/utils";
import type { InsightCard as InsightCardType } from "@/lib/api";

const ACTION_STYLES: Record<string, { bg: string; text: string; border: string }> = {
  BUY:    { bg: "bg-green-500/10", text: "text-green-400", border: "border-green-500/30" },
  SELL:   { bg: "bg-red-500/10", text: "text-red-400", border: "border-red-500/30" },
  TRIM:   { bg: "bg-yellow-500/10", text: "text-yellow-400", border: "border-yellow-500/30" },
  HOLD:   { bg: "bg-blue-500/10", text: "text-blue-400", border: "border-blue-500/30" },
  REVIEW: { bg: "bg-purple-500/10", text: "text-purple-400", border: "border-purple-500/30" },
};

export function InsightCard({ card }: { card: InsightCardType }) {
  const styles = ACTION_STYLES[card.action] || ACTION_STYLES.HOLD;

  return (
    <div
      className={cn(
        "card-glass p-4 space-y-3 border",
        styles.border,
        styles.bg
      )}
    >
      {/* Header: Action badge + Ticker */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <span
            className={cn(
              "px-2 py-0.5 rounded text-xs font-bold uppercase",
              styles.bg,
              styles.text
            )}
          >
            {card.action}
          </span>
          <span className="font-mono font-semibold text-text-primary">
            {card.ticker}
          </span>
          <span className="text-xs text-text-muted">{card.category}</span>
        </div>
        {card.current_price && (
          <span className="font-mono text-sm text-text-secondary">
            {formatCurrency(card.current_price)}
          </span>
        )}
      </div>

      {/* Detail */}
      <p className="text-sm text-text-primary">{card.detail}</p>

      {/* Rationale — the one-liner reason */}
      {card.rationale && (
        <p className="text-xs text-text-secondary italic leading-relaxed">
          {card.rationale}
        </p>
      )}

      {/* Footer: Tax note + DRIP note */}
      <div className="flex flex-wrap gap-2">
        {card.tax_note && (
          <span className="text-[10px] px-2 py-0.5 rounded-full bg-surface-elevated text-text-muted">
            {card.tax_note}
          </span>
        )}
        {card.drip_note && (
          <span className="text-[10px] px-2 py-0.5 rounded-full bg-surface-elevated text-text-muted">
            {card.drip_note}
          </span>
        )}
        {card.pnl_pct !== null && card.pnl_pct !== undefined && (
          <span
            className={cn(
              "text-[10px] px-2 py-0.5 rounded-full",
              card.pnl_pct >= 0
                ? "bg-green-500/10 text-green-400"
                : "bg-red-500/10 text-red-400"
            )}
          >
            {formatPercent(card.pnl_pct)}
          </span>
        )}
      </div>
    </div>
  );
}
