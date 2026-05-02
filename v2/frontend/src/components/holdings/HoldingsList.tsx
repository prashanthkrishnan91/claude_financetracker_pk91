"use client";

import { useState } from "react";
import Link from "next/link";
import { cn, formatCurrency, formatPercent, pnlClass } from "@/lib/utils";
import { usePositions } from "@/lib/hooks";
import { InlineLoader } from "@/components/ui/Spinner";
import { EmptyState } from "@/components/ui/EmptyState";
import type { Position } from "@/lib/api";

const CATEGORIES = ["All", "Core", "ETF", "Crypto", "Other", "IPO", "SELL"] as const;

export function HoldingsList() {
  const [filter, setFilter] = useState<string>("All");
  const { data: positions, isLoading, error } = usePositions();

  if (isLoading) return <InlineLoader text="Loading holdings..." />;
  if (error) return <EmptyState title="Failed to load holdings" />;
  if (!positions || positions.length === 0) {
    return (
      <EmptyState
        title="No positions yet"
        description="Import a CSV or sync with Plaid to get started."
      />
    );
  }

  const enriched = positions.map((p) => {
    const price = p.current_price ?? p.avg_cost;
    const marketValue = p.shares * price;
    const costBasis = p.shares * p.avg_cost;
    const pnl = marketValue - costBasis;
    const pnlPct = costBasis > 0 ? (pnl / costBasis) * 100 : 0;
    return { ...p, marketValue, costBasis, pnl, pnlPct };
  });

  const filtered =
    filter === "All" ? enriched : enriched.filter((p) => p.category === filter);
  const sorted = [...filtered].sort((a, b) => b.marketValue - a.marketValue);

  const counts: Record<string, number> = { All: positions.length };
  for (const p of positions) {
    counts[p.category] = (counts[p.category] || 0) + 1;
  }

  return (
    <div className="space-y-3">
      {/* Category filter */}
      <div className="flex gap-1 overflow-x-auto pb-1 scrollbar-hide">
        {CATEGORIES.map((cat) => {
          const count = counts[cat] || 0;
          if (cat !== "All" && count === 0) return null;
          const active = filter === cat;
          return (
            <button
              key={cat}
              onClick={() => setFilter(cat)}
              className={cn(
                "px-3 py-1 text-[11px] rounded-md transition-colors whitespace-nowrap font-medium",
                active
                  ? "bg-accent text-background font-semibold"
                  : "text-text-muted hover:text-text-secondary bg-surface-elevated hover:bg-surface-hover border border-border/60"
              )}
            >
              {cat}
              {count > 0 && (
                <span className={cn("ml-1", active ? "opacity-70" : "opacity-40")}>
                  {count}
                </span>
              )}
            </button>
          );
        })}
      </div>

      {/* Holdings list */}
      <div className="data-card overflow-hidden divide-y divide-border/50">
        {sorted.map((h) => (
          <Link
            key={h.ticker}
            href={`/dashboard/position/${h.ticker}`}
            className="flex items-center justify-between px-4 py-3 hover:bg-surface-hover/50 transition-colors block"
          >
            <div className="flex-1 min-w-0">
              <div className="flex items-center gap-2">
                <span className="ticker-symbol text-sm">
                  {h.ticker}
                </span>
                <span className="badge-surface text-[9px]">
                  {h.category}
                </span>
                {h.lt_eligible && (
                  <span className="badge-accent text-[9px]">
                    LT
                  </span>
                )}
              </div>
              <p className="text-xs text-text-muted truncate mt-0.5">{h.name}</p>
            </div>

            <div className="text-right ml-4 shrink-0">
              <p className="data-value text-sm">
                {formatCurrency(h.marketValue)}
              </p>
              <p className={cn("text-xs font-mono tabular-nums", pnlClass(h.pnl))}>
                {h.pnl >= 0 ? "+" : ""}{formatCurrency(h.pnl)}{" "}
                <span className="opacity-70">({formatPercent(h.pnlPct)})</span>
              </p>
            </div>
          </Link>
        ))}
      </div>

      <p className="metric-label text-center pt-1">
        {sorted.length} position{sorted.length !== 1 ? "s" : ""}
        {filter !== "All" ? ` · ${filter}` : ""}
      </p>
    </div>
  );
}
