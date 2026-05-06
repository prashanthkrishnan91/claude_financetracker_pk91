"use client";

/**
 * IntelV3Cockpit — Premium held-position intelligence cockpit.
 *
 * Reads ONLY from v3 snapshot (IntelV3Snapshot). Never reads legacy recommendation cards.
 * When v3 flag is enabled:
 *   - Page load: reads latest snapshot (zero LLM calls).
 *   - Run Intel v3: builds decisions from existing signals, creates new snapshot.
 *   - Failed run: keeps last valid snapshot with failure banner.
 *   - No snapshot: shows clear empty state with "Run Intel v3" CTA.
 *
 * LOCKED filter contract: ALL / BUY / HOLD / TRIM / SELL only.
 * No posture labels. No radar labels in held cards. No raw metric keys.
 */

import { useState } from "react";
import { cn } from "@/lib/utils";
import { useIntelV3Snapshot, useRunIntelV3 } from "@/lib/hooks";
import { IntelV3Card } from "./IntelV3Card";
import { IntelV3Drawer } from "./IntelV3Drawer";
import { Spinner } from "@/components/ui/Spinner";
import type { IntelV3HeldCard, IntelV3Action, IntelV3Snapshot } from "@/lib/api";

// LOCKED: Intel v3 visible filter contract is ALL/BUY/HOLD/TRIM/SELL only.
// Radar labels (WATCH/AVOID) must never appear here.
// New visible buckets require a spec change.
const INTEL_V3_FILTERS = [
  { key: "ALL",  label: "All",  color: "bg-surface-elevated text-text-primary" },
  { key: "BUY",  label: "Buy",  color: "bg-green-500/10 text-green-400 border-green-500/30" },
  { key: "HOLD", label: "Hold", color: "bg-blue-500/10 text-blue-400 border-blue-500/30" },
  { key: "TRIM", label: "Trim", color: "bg-amber-500/10 text-amber-400 border-amber-500/30" },
  { key: "SELL", label: "Sell", color: "bg-red-500/10 text-red-400 border-red-500/30" },
] as const;

type FilterKey = "ALL" | IntelV3Action;


// ── Sub-components ────────────────────────────────────────────────────────────

function CommandCenter({ snapshot }: { snapshot: IntelV3Snapshot }) {
  const cc = snapshot.portfolio_command_center;
  const stats = [
    { label: "Holdings", value: cc.total_holdings },
    { label: "Buy",      value: cc.buy_count,  color: "text-green-400" },
    { label: "Hold",     value: cc.hold_count, color: "text-blue-400" },
    { label: "Trim",     value: cc.trim_count, color: "text-amber-400" },
    { label: "Sell",     value: cc.sell_count, color: "text-red-400" },
  ];
  return (
    <div className="flex items-center gap-4 flex-wrap">
      {stats.map((s) => (
        <div key={s.label} className="flex items-baseline gap-1">
          <span className={cn("text-lg font-bold", s.color ?? "text-text-primary")}>
            {s.value}
          </span>
          <span className="text-xs text-text-muted">{s.label}</span>
        </div>
      ))}
    </div>
  );
}

function SnapshotBanner({
  snapshot,
  isStale,
  warnings,
  runFailed,
}: {
  snapshot: IntelV3Snapshot;
  isStale: boolean;
  warnings: string[];
  runFailed?: boolean;
}) {
  const date = new Date(snapshot.generated_at).toLocaleString();
  return (
    <div className={cn(
      "rounded-lg border px-3 py-2 text-xs",
      runFailed
        ? "bg-red-500/10 border-red-500/30 text-red-400"
        : isStale || warnings.length > 0
        ? "bg-amber-500/10 border-amber-500/30 text-amber-400"
        : "bg-surface border-border text-text-muted"
    )}>
      <div className="flex items-center justify-between gap-2 flex-wrap">
        <span>
          {runFailed
            ? "Last run failed — showing previous snapshot."
            : isStale
            ? `Data may be stale — last updated ${date}.`
            : `Snapshot as of ${date}`}
        </span>
        {warnings.map((w, i) => (
          <span key={i} className="text-amber-400">{w}</span>
        ))}
      </div>
    </div>
  );
}

function SectionHeader({ title, count }: { title: string; count?: number }) {
  return (
    <div className="flex items-center gap-2 mb-3">
      <h2 className="text-sm font-bold text-text-primary">{title}</h2>
      {count !== undefined && (
        <span className="text-xs text-text-muted bg-surface-elevated rounded px-1.5 py-0.5">
          {count}
        </span>
      )}
    </div>
  );
}

function WhatChanged({ items }: { items: string[] }) {
  if (items.length === 0) return null;
  return (
    <div className="rounded-lg border border-border bg-surface p-3">
      <SectionHeader title="What changed since last run" />
      <ul className="space-y-1">
        {items.map((item, i) => (
          <li key={i} className="text-xs text-text-secondary flex items-start gap-1.5">
            <span className="mt-0.5 text-accent">→</span>
            <span>{item}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

function RadarDeferredPanel() {
  return (
    <div className="rounded-lg border border-dashed border-border bg-surface/50 px-4 py-5 text-center">
      <p className="text-xs font-medium text-text-muted">Opportunity Radar</p>
      <p className="text-[11px] text-text-muted mt-1 max-w-xs mx-auto">
        Launches after held-position intelligence is stable. Radar rows will appear here.
      </p>
    </div>
  );
}

function EmptySnapshotState({ onRun, isRunning }: { onRun: () => void; isRunning: boolean }) {
  return (
    <div className="flex flex-col items-center justify-center py-16 gap-4">
      <div className="text-text-muted text-center space-y-2">
        <p className="text-base font-medium text-text-primary">No Intel v3 snapshot yet</p>
        <p className="text-sm">Run Intel v3 to build your first decision snapshot.</p>
        <p className="text-xs text-text-muted">
          The analysis uses your existing holdings and signals — no AI calls on page load.
        </p>
      </div>
      <button
        onClick={onRun}
        disabled={isRunning}
        className="px-4 py-2 rounded-lg bg-accent text-background text-sm font-semibold hover:bg-accent-hover transition-colors disabled:opacity-50 flex items-center gap-2"
      >
        {isRunning ? <Spinner className="h-4 w-4" /> : null}
        {isRunning ? "Building Intel…" : "Run Intel v3"}
      </button>
    </div>
  );
}


// ── Main cockpit ──────────────────────────────────────────────────────────────

export function IntelV3Cockpit() {
  const [filter, setFilter] = useState<FilterKey>("ALL");
  const [selectedCard, setSelectedCard] = useState<IntelV3HeldCard | null>(null);
  const [runFailed, setRunFailed] = useState(false);

  const { data: snapshot, isLoading, error } = useIntelV3Snapshot();
  const runMutation = useRunIntelV3();

  const handleRun = () => {
    setRunFailed(false);
    runMutation.mutate(undefined, {
      onError: () => setRunFailed(true),
    });
  };

  // Filter cards — counts come from snapshot, not recomputed.
  const allCards = snapshot?.current_holdings ?? [];
  const filteredCards = filter === "ALL"
    ? allCards
    : allCards.filter((c) => c.action === filter);

  // Build counts from snapshot action_counts (same source as cards).
  const counts: Record<string, number> = {
    ALL: allCards.length,
    ...(snapshot?.action_counts ?? {}),
  };

  const isRunning = runMutation.isPending;

  if (isLoading) {
    return (
      <div className="flex justify-center py-16">
        <Spinner className="h-6 w-6" />
      </div>
    );
  }

  // No snapshot yet — show empty state.
  if (!snapshot && !isLoading) {
    return <EmptySnapshotState onRun={handleRun} isRunning={isRunning} />;
  }

  if (!snapshot) return null;

  return (
    <div className="space-y-5">

      {/* Snapshot banner + run button row */}
      <div className="flex items-start gap-3 flex-wrap">
        <div className="flex-1">
          <SnapshotBanner
            snapshot={snapshot}
            isStale={snapshot.is_stale}
            warnings={snapshot.warnings}
            runFailed={runFailed}
          />
        </div>
        <button
          onClick={handleRun}
          disabled={isRunning}
          className="shrink-0 px-3 py-1.5 rounded-md bg-accent text-background text-xs font-semibold hover:bg-accent-hover transition-colors disabled:opacity-50 flex items-center gap-1.5"
        >
          {isRunning ? <Spinner className="h-3 w-3" /> : null}
          {isRunning ? "Running…" : "Run Intel v3"}
        </button>
      </div>

      {/* Portfolio Command Center */}
      <div className="rounded-xl border border-border bg-surface p-4">
        <SectionHeader title="Portfolio Command Center" />
        <CommandCenter snapshot={snapshot} />
      </div>

      {/* Filter tabs — LOCKED: ALL/BUY/HOLD/TRIM/SELL only */}
      <div className="flex rounded-md border border-border overflow-hidden text-xs w-fit">
        {INTEL_V3_FILTERS.map((f) => (
          <button
            key={f.key}
            onClick={() => setFilter(f.key as FilterKey)}
            className={cn(
              "px-3 py-1.5 font-semibold transition-colors border-r border-border last:border-0",
              filter === f.key
                ? cn(f.color, "border-current")
                : "text-text-muted hover:text-text-primary bg-transparent"
            )}
          >
            {f.label}
            {counts[f.key] !== undefined && (
              <span className="ml-1 opacity-60">({counts[f.key]})</span>
            )}
          </button>
        ))}
      </div>

      {/* Best Buys section */}
      {(filter === "ALL" || filter === "BUY") && snapshot.best_buys.length > 0 && (
        <div>
          <SectionHeader title="Best Buys" count={snapshot.best_buys.length} />
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
            {snapshot.best_buys.map((card) => (
              <IntelV3Card key={card.ticker} card={card} onSelect={setSelectedCard} />
            ))}
          </div>
        </div>
      )}

      {/* Trim/Sell Desk */}
      {(filter === "ALL" || filter === "TRIM" || filter === "SELL") && snapshot.trim_sell_desk.length > 0 && (
        <div>
          <SectionHeader title="Trim / Sell Desk" count={snapshot.trim_sell_desk.length} />
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
            {snapshot.trim_sell_desk.map((card) => (
              <IntelV3Card key={card.ticker} card={card} onSelect={setSelectedCard} />
            ))}
          </div>
        </div>
      )}

      {/* Current Holdings (filtered) */}
      <div>
        <SectionHeader
          title={filter === "ALL" ? "Current Holdings" : `${filter} (${filteredCards.length})`}
          count={filter === "ALL" ? allCards.length : undefined}
        />
        {filteredCards.length === 0 ? (
          <p className="text-sm text-text-muted py-4">No holdings match this filter.</p>
        ) : (
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
            {filteredCards.map((card) => (
              <IntelV3Card key={card.ticker} card={card} onSelect={setSelectedCard} />
            ))}
          </div>
        )}
      </div>

      {/* What Changed */}
      {snapshot.what_changed.length > 0 && (
        <WhatChanged items={snapshot.what_changed} />
      )}

      {/* Opportunity Radar — deferred */}
      <div>
        <SectionHeader title="Opportunity Radar" />
        <RadarDeferredPanel />
      </div>

      {/* Detail drawer */}
      {selectedCard && (
        <IntelV3Drawer card={selectedCard} onClose={() => setSelectedCard(null)} />
      )}
    </div>
  );
}
