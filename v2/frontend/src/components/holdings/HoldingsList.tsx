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

  // Compute market value for sorting (use current_price if available, else avg_cost)
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

  // Category counts for filter badges
  const counts: Record<string, number> = { All: positions.length };
  for (const p of positions) {
    counts[p.category] = (counts[p.category] || 0) + 1;
  }

  return (
    <div className="space-y-3">
      {/* Category filter */}
      <div className="flex gap-1.5 overflow-x-auto pb-1">
        {CATEGORIES.map((cat) => {
          const count = counts[cat] || 0;
          if (cat !== "All" && count === 0) return null;
          return (
            <button
              key={cat}
              onClick={() => setFilter(cat)}
              className={cn(
                "px-3 py-1 text-xs rounded-md transition-colors whitespace-nowrap",
                filter === cat
                  ? "bg-accent text-background font-semibold"
                  : "text-text-muted hover:text-text-primary hover:bg-surface-elevated"
              )}
            >
              {cat} {count > 0 && <span className="opacity-60">{count}</span>}
            </button>
          );
        })}
      </div>

      {/* Holdings list */}
      <div className="space-y-1.5">
        {sorted.map((h) => (
          <Link
            key={h.ticker}
            href={`/dashboard/position/${h.ticker}`}
            className="card-glass px-4 py-3 flex items-center justify-between hover:bg-surface-elevated/50 transition-colors cursor-pointer block"
          >
            <div className="flex-1 min-w-0">
              <div className="flex items-center gap-2">
                <span className="font-mono font-semibold text-text-primary">
                  {h.ticker}
                </span>
                <span className="text-xs px-1.5 py-0.5 rounded bg-surface-elevated text-text-muted">
                  {h.category}
                </span>
                {h.lt_eligible && (
                  <span className="text-[10px] px-1 py-0.5 rounded bg-accent/10 text-accent">
                    LT
                  </span>
                )}
              </div>
              <p className="text-xs text-text-secondary truncate">{h.name}</p>
            </div>

            <div className="text-right ml-4">
              <p className="font-mono text-sm text-text-primary">
                {formatCurrency(h.marketValue)}
              </p>
              <p className={cn("text-xs font-mono", pnlClass(h.pnl))}>
                {formatCurrency(h.pnl)} ({formatPercent(h.pnlPct)})
              </p>
            </div>
          </Link>
        ))}
      </div>

      <p className="text-xs text-text-muted text-center pt-2">
        {sorted.length} position{sorted.length !== 1 ? "s" : ""}
        {filter !== "All" ? ` in ${filter}` : ""}
      </p>
    </div>
  );
}
