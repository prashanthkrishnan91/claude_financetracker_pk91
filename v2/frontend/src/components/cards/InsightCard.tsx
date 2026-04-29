"use client";

import { cn, formatCurrency, formatPercent } from "@/lib/utils";
import type { InsightCardData } from "@/lib/api";

const ACTION_STYLES: Record<string, { bg: string; text: string; border: string; badge: string }> = {
  BUY:    { bg: "bg-positive/5",       text: "text-positive",      border: "border-positive/20",      badge: "action-badge-buy" },
  SELL:   { bg: "bg-negative/5",       text: "text-negative",      border: "border-negative/20",      badge: "action-badge-sell" },
  TRIM:   { bg: "bg-caution/5",        text: "text-caution",       border: "border-caution/20",       badge: "action-badge-trim" },
  HOLD:   { bg: "bg-accent-blue/5",    text: "text-accent-blue",   border: "border-accent-blue/20",   badge: "action-badge-hold" },
  REVIEW: { bg: "bg-accent-purple/5",  text: "text-accent-purple", border: "border-accent-purple/20", badge: "action-badge-review" },
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
        "intel-card p-4 space-y-2.5 border",
        styles.border,
        styles.bg,
        onClick && "cursor-pointer hover:brightness-105 active:scale-[0.995] transition-transform"
      )}
    >
      {/* Header */}
      <div className="flex items-center justify-between gap-2">
        <div className="flex items-center gap-2 min-w-0">
          <span className={styles.badge}>
            {card.action}
          </span>
          <span className="ticker-symbol text-sm">
            {card.ticker}
          </span>
          <span className="text-[10px] text-text-muted">{card.category}</span>
        </div>
        <div className="flex items-center gap-2 shrink-0">
          {card.current_price && (
            <span className="data-value-xs">
              {formatCurrency(card.current_price)}
            </span>
          )}
          {onClick && (
            <ChevronRightIcon className="w-4 h-4 text-text-muted" />
          )}
        </div>
      </div>

      {/* Detail */}
      <p className="text-sm text-text-primary leading-snug">{card.detail}</p>

      {/* Rationale */}
      {card.rationale && (
        <p className="text-xs text-text-secondary leading-relaxed border-l border-border/70 pl-2">
          {card.rationale}
        </p>
      )}

      {/* Footer pills */}
      <div className="flex flex-wrap gap-1.5 pt-0.5">
        {card.tax_note && (
          <span className="badge-surface">
            {card.tax_note}
          </span>
        )}
        {card.drip_note && (
          <span className="badge-surface">
            {card.drip_note}
          </span>
        )}
        {card.pnl_pct !== null && card.pnl_pct !== undefined && (
          <span className={card.pnl_pct >= 0 ? "badge-positive" : "badge-negative"}>
            {card.pnl_pct >= 0 ? "+" : ""}{formatPercent(card.pnl_pct)}
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
