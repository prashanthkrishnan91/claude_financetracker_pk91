"use client";

/**
 * IntelV3Cockpit — Premium held-position intelligence cockpit.
 *
 * Stage 3.3 — all-or-nothing certified intelligence run contract.
 *
 * Green state (Certified Current) is only shown when:
 *   - snapshot_source === "worker_certified"
 *   - certified_holding_count === total_holding_count
 *   - No pending refresh is in progress
 *
 * Run Intel v3 button enqueues a background worker run — it does NOT
 * immediately show green. The cockpit polls GET /intel/v3/snapshot every
 * 15 seconds after a click until the snapshot becomes worker_certified.
 *
 * LOCKED filter contract: ALL / BUY / HOLD / TRIM / SELL only.
 * No posture labels. No radar labels in held cards. No raw metric keys.
 */

import { useState, useEffect, useRef, useCallback } from "react";
import { cn } from "@/lib/utils";
import { useIntelV3Snapshot, useRunIntelV3 } from "@/lib/hooks";
import { buildBannerState } from "@/lib/intel-v3-banner";
import { IntelV3Card } from "./IntelV3Card";
import { IntelV3Drawer } from "./IntelV3Drawer";
import { Spinner } from "@/components/ui/Spinner";
import type {
  IntelV3HeldCard,
  IntelV3Action,
  IntelV3Snapshot,
  IntelV3RunResult,
} from "@/lib/api";

// LOCKED: Intel v3 visible filter contract is ALL/BUY/HOLD/TRIM/SELL only.
const INTEL_V3_FILTERS = [
  { key: "ALL",  label: "All",  color: "bg-surface-elevated text-text-primary" },
  { key: "BUY",  label: "Buy",  color: "bg-green-500/10 text-green-400 border-green-500/30" },
  { key: "HOLD", label: "Hold", color: "bg-blue-500/10 text-blue-400 border-blue-500/30" },
  { key: "TRIM", label: "Trim", color: "bg-amber-500/10 text-amber-400 border-amber-500/30" },
  { key: "SELL", label: "Sell", color: "bg-red-500/10 text-red-400 border-red-500/30" },
] as const;

type FilterKey = "ALL" | IntelV3Action;

// Polling interval while a refresh is in progress (15 seconds)
const REFRESH_POLL_INTERVAL_MS = 15_000;
// Stop polling after this many minutes if worker hasn't completed
const REFRESH_POLL_TIMEOUT_MS = 5 * 60 * 1000;


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

function ProvenanceRow({ snapshot }: { snapshot: IntelV3Snapshot }) {
  const cert = snapshot.certification_summary;
  const certCount = snapshot.certified_holding_count ?? cert?.certified_holding_count;
  const totalCount = snapshot.total_holding_count ?? cert?.total_holding_count;
  const latestRunAt = cert?.latest_agent_run_at
    ? new Date(cert.latest_agent_run_at).toLocaleString()
    : null;
  const runIds = cert?.agent_run_ids_used ?? [];
  const failedTickers = snapshot.failed_tickers_in_certification ?? [];

  return (
    <div className="mt-2 space-y-0.5 text-[11px] text-text-muted">
      {certCount !== undefined && totalCount !== undefined && (
        <p>Coverage: {certCount}/{totalCount} certified.</p>
      )}
      {latestRunAt && <p>Latest analyst run: {latestRunAt}.</p>}
      {runIds.length > 0 && (
        <p>Agent run IDs: {runIds.slice(0, 2).join(", ")}{runIds.length > 2 ? ` +${runIds.length - 2} more` : ""}.</p>
      )}
      <p>
        Snapshot source:{" "}
        <span className="font-medium">
          {snapshot.snapshot_source === "worker_certified"
            ? "worker certified"
            : snapshot.snapshot_source === "certification_failed"
            ? "certification failed"
            : snapshot.snapshot_source ?? "unknown"}
        </span>
        .
      </p>
      <p>Agents ran for this click: No — background worker handles analysis.</p>
      <p>This click used LLMs: No — background worker handles analysis.</p>
      {failedTickers.length > 0 && (
        <p className="text-red-400">
          Failed tickers: {failedTickers.slice(0, 5).join(", ")}
          {failedTickers.length > 5 ? ` +${failedTickers.length - 5} more` : ""}.
        </p>
      )}
    </div>
  );
}

function SnapshotBanner({
  snapshot,
  isRefreshing,
  lastRunResult,
}: {
  snapshot: IntelV3Snapshot | null;
  isRefreshing: boolean;
  lastRunResult?: IntelV3RunResult | null;
}) {
  const bannerState = buildBannerState(snapshot, isRefreshing, lastRunResult);

  const toneClass = {
    green: "bg-green-500/10 border-green-500/30 text-green-400",
    amber: "bg-amber-500/10 border-amber-500/30 text-amber-400",
    red:   "bg-red-500/10 border-red-500/30 text-red-400",
    grey:  "bg-surface border-border text-text-muted",
  }[bannerState.tone];

  return (
    <div className={cn("rounded-lg border px-3 py-2 text-xs", toneClass)}>
      <div className="flex items-center justify-between gap-2 flex-wrap">
        <span className="font-semibold">{bannerState.headline}</span>
        {isRefreshing && (
          <span className="flex items-center gap-1 text-text-muted">
            <Spinner className="h-3 w-3" /> checking every 15s
          </span>
        )}
      </div>
      {bannerState.detail && (
        <p className="mt-1 text-[11px] opacity-80">{bannerState.detail}</p>
      )}
      {bannerState.showProvenance && snapshot && (
        <ProvenanceRow snapshot={snapshot} />
      )}
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
        <p className="text-sm">Run Intel v3 to start a background analyst run for all holdings.</p>
        <p className="text-xs text-text-muted">
          The background worker will run LLM analysis and publish a certified snapshot automatically.
        </p>
      </div>
      <button
        onClick={onRun}
        disabled={isRunning}
        className="px-4 py-2 rounded-lg bg-accent text-background text-sm font-semibold hover:bg-accent-hover transition-colors disabled:opacity-50 flex items-center gap-2"
      >
        {isRunning ? <Spinner className="h-4 w-4" /> : null}
        {isRunning ? "Enqueueing refresh…" : "Run Intel v3"}
      </button>
    </div>
  );
}


// ── Main cockpit ──────────────────────────────────────────────────────────────

export function IntelV3Cockpit() {
  const [filter, setFilter] = useState<FilterKey>("ALL");
  const [selectedCard, setSelectedCard] = useState<IntelV3HeldCard | null>(null);
  const [isRefreshing, setIsRefreshing] = useState(false);
  const [lastRunResult, setLastRunResult] = useState<IntelV3RunResult | null>(null);

  const refreshStartedAt = useRef<number | null>(null);
  const pollTimerRef = useRef<NodeJS.Timeout | null>(null);

  const { data: snapshot, isLoading, refetch: refetchSnapshot } = useIntelV3Snapshot();
  const runMutation = useRunIntelV3();

  // Poll the snapshot every 15s while a refresh is in progress.
  // Stop when:
  //   a) snapshot_source === "worker_certified" and certified_holding_count === total_holding_count
  //   b) snapshot_source === "certification_failed" (worker completed but failed)
  //   c) Timeout exceeded (5 minutes)
  const stopPolling = useCallback(() => {
    if (pollTimerRef.current) {
      clearInterval(pollTimerRef.current);
      pollTimerRef.current = null;
    }
    setIsRefreshing(false);
    refreshStartedAt.current = null;
  }, []);

  const startPolling = useCallback(() => {
    if (pollTimerRef.current) clearInterval(pollTimerRef.current);
    refreshStartedAt.current = Date.now();
    setIsRefreshing(true);

    pollTimerRef.current = setInterval(async () => {
      // Check timeout
      const elapsed = Date.now() - (refreshStartedAt.current ?? Date.now());
      if (elapsed > REFRESH_POLL_TIMEOUT_MS) {
        stopPolling();
        return;
      }

      const result = await refetchSnapshot();
      const snap = result.data;

      if (!snap) return;

      const isCertified =
        snap.snapshot_source === "worker_certified" &&
        typeof snap.certified_holding_count === "number" &&
        typeof snap.total_holding_count === "number" &&
        snap.total_holding_count > 0 &&
        snap.certified_holding_count === snap.total_holding_count;

      const isCertificationFailed = snap.snapshot_source === "certification_failed";

      if (isCertified || isCertificationFailed) {
        stopPolling();
      }
    }, REFRESH_POLL_INTERVAL_MS);
  }, [refetchSnapshot, stopPolling]);

  // Clean up timer on unmount
  useEffect(() => {
    return () => {
      if (pollTimerRef.current) clearInterval(pollTimerRef.current);
    };
  }, []);

  // When the snapshot becomes certified while polling, stop polling
  useEffect(() => {
    if (!isRefreshing || !snapshot) return;
    const isCertified =
      snapshot.snapshot_source === "worker_certified" &&
      typeof snapshot.certified_holding_count === "number" &&
      typeof snapshot.total_holding_count === "number" &&
      snapshot.total_holding_count > 0 &&
      snapshot.certified_holding_count === snapshot.total_holding_count;
    const isCertificationFailed = snapshot.snapshot_source === "certification_failed";
    if (isCertified || isCertificationFailed) {
      stopPolling();
    }
  }, [snapshot, isRefreshing, stopPolling]);

  const handleRun = () => {
    runMutation.mutate(undefined, {
      onSuccess: (result) => {
        setLastRunResult(result);
        // Start polling — worker will certify in background
        startPolling();
      },
      onError: () => {
        setIsRefreshing(false);
      },
    });
  };

  const isButtonDisabled = runMutation.isPending;

  const allCards = snapshot?.current_holdings ?? [];
  const filteredCards = filter === "ALL"
    ? allCards
    : allCards.filter((c) => c.action === filter);

  const counts: Record<string, number> = {
    ALL: allCards.length,
    ...(snapshot?.action_counts ?? {}),
  };

  if (isLoading) {
    return (
      <div className="flex justify-center py-16">
        <Spinner className="h-6 w-6" />
      </div>
    );
  }

  if (!snapshot && !isLoading && !isRefreshing) {
    return <EmptySnapshotState onRun={handleRun} isRunning={runMutation.isPending} />;
  }

  if (!snapshot && isRefreshing) {
    return (
      <div className="space-y-5">
        <SnapshotBanner
          snapshot={null}
          isRefreshing={isRefreshing}
          lastRunResult={lastRunResult}
        />
        <div className="flex items-center justify-center py-12 gap-3 text-text-muted">
          <Spinner className="h-5 w-5" />
          <span className="text-sm">Analyst worker is running. Results will appear here automatically.</span>
        </div>
      </div>
    );
  }

  if (!snapshot) return null;

  return (
    <div className="space-y-5">

      {/* Snapshot banner + run button row */}
      <div className="flex items-start gap-3 flex-wrap">
        <div className="flex-1">
          <SnapshotBanner
            snapshot={snapshot}
            isRefreshing={isRefreshing}
            lastRunResult={lastRunResult}
          />
        </div>
        <button
          onClick={handleRun}
          disabled={isButtonDisabled}
          className="shrink-0 px-3 py-1.5 rounded-md bg-accent text-background text-xs font-semibold hover:bg-accent-hover transition-colors disabled:opacity-50 flex items-center gap-1.5"
        >
          {isButtonDisabled ? <Spinner className="h-3 w-3" /> : null}
          {isButtonDisabled ? "Enqueueing…" : "Run Intel v3"}
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
