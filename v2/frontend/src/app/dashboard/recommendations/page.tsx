"use client";

import { useState } from "react";
import { cn } from "@/lib/utils";
import { useRecommendations, useRefreshRecommendations } from "@/lib/hooks";
import { InsightCard } from "@/components/cards/InsightCard";
import { InlineLoader } from "@/components/ui/Spinner";
import { EmptyState } from "@/components/ui/EmptyState";
import { Spinner } from "@/components/ui/Spinner";
import type { InsightCardData } from "@/lib/api";

const ACTION_FILTERS = [
  { key: "ALL", label: "All", color: "bg-surface-elevated text-text-primary" },
  { key: "SELL", label: "Sell", color: "bg-red-500/10 text-red-400 border-red-500/30" },
  { key: "BUY", label: "Buy", color: "bg-green-500/10 text-green-400 border-green-500/30" },
  { key: "TRIM", label: "Trim", color: "bg-yellow-500/10 text-yellow-400 border-yellow-500/30" },
  { key: "REVIEW", label: "Review", color: "bg-purple-500/10 text-purple-400 border-purple-500/30" },
  { key: "HOLD", label: "Hold", color: "bg-blue-500/10 text-blue-400 border-blue-500/30" },
] as const;

export default function RecommendationsPage() {
  const [filter, setFilter] = useState("ALL");
  const { data: recs, isLoading, error } = useRecommendations();
  const refreshRecs = useRefreshRecommendations();

  const filtered =
    filter === "ALL"
      ? recs || []
      : (recs || []).filter((r) => r.action === filter);

  // Count per action
  const counts: Record<string, number> = { ALL: (recs || []).length };
  for (const r of recs || []) {
    counts[r.action] = (counts[r.action] || 0) + 1;
  }

  return (
    <>
      <header className="sticky top-0 z-50 bg-background/80 backdrop-blur-xl border-b border-border px-4 py-3">
        <div className="max-w-7xl mx-auto flex items-center justify-between">
          <h1 className="text-xl font-display text-text-primary">Intel</h1>
          <button
            onClick={() => refreshRecs.mutate()}
            disabled={refreshRecs.isPending}
            className="text-xs px-3 py-1.5 rounded-md bg-accent text-background font-semibold hover:bg-accent-hover transition-colors disabled:opacity-50"
          >
            {refreshRecs.isPending ? (
              <Spinner className="h-3 w-3" />
            ) : (
              "Refresh"
            )}
          </button>
        </div>
      </header>

      <main className="max-w-7xl mx-auto px-4 py-6 space-y-4">
        {/* Filter cards */}
        <div className="grid grid-cols-3 sm:grid-cols-6 gap-2">
          {ACTION_FILTERS.map((f) => (
            <button
              key={f.key}
              onClick={() => setFilter(f.key)}
              className={cn(
                "px-3 py-2 rounded-lg text-xs font-semibold text-center border transition-colors",
                filter === f.key
                  ? cn(f.color, "border-current")
                  : "border-border text-text-muted hover:bg-surface-elevated"
              )}
            >
              {f.label}
              {counts[f.key] ? (
                <span className="block text-lg font-display mt-0.5">
                  {counts[f.key]}
                </span>
              ) : (
                <span className="block text-lg font-display mt-0.5 opacity-30">
                  0
                </span>
              )}
            </button>
          ))}
        </div>

        {/* Recommendations list */}
        {isLoading ? (
          <InlineLoader text="Loading recommendations..." />
        ) : error ? (
          <EmptyState title="Failed to load recommendations" />
        ) : filtered.length === 0 ? (
          <EmptyState
            title="No recommendations"
            description={
              filter === "ALL"
                ? "Hit Refresh to generate recommendations for your portfolio."
                : `No ${filter} recommendations right now.`
            }
          />
        ) : (
          <div className="space-y-3">
            {filtered.map((card) => (
              <InsightCard key={card.id} card={card} />
            ))}
          </div>
        )}
      </main>
    </>
  );
}
