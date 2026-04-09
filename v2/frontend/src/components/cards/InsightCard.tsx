"use client";

import { cn, formatCurrency, formatPercent } from "@/lib/utils";
import type { InsightCardData } from "@/lib/api";

const ACTION_STYLES: Record<string, { bg: string; text: string; border: string }> = {
  BUY:    { bg: "bg-green-500/10", text: "text-green-400", border: "border-green-500/30" },
  SELL:   { bg: "bg-red-500/10", text: "text-red-400", border: "border-red-500/30" },
  TRIM:   { bg: "bg-yellow-500/10", text: "text-yellow-400", border: "border-yellow-500/30" },
  HOLD:   { bg: "bg-blue-500/10", text: "text-blue-400", border: "border-blue-500/30" },
  REVIEW: { bg: "bg-purple-500/10", text: "text-purple-400", border: "border-purple-500/30" },
};

export function InsightCard({
  card,
  onClick,
}: {
  card: InsightCardData;
  onClick?: () => void;
}) {
  const styles = ACTION_STYLES[card.action] || ACTION_STYLES.HOLD;

  return (
    <div
      onClick={onClick}
      className={cn(
        "card-glass p-4 space-y-3 border transition-colors",
        styles.border,
        styles.bg,
        onClick && "cursor-pointer hover:brightness-110 active:scale-[0.99]"
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
        <div className="flex items-center gap-2">
          {card.current_price && (
            <span className="font-mono text-sm text-text-secondary">
              {formatCurrency(card.current_price)}
            </span>
          )}
          {onClick && (
            <span className="text-text-muted">
              <ChevronRightIcon className="w-4 h-4" />
            </span>
          )}
        </div>
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

function ChevronRightIcon({ className }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2}>
      <path d="M9 18l6-6-6-6" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}
