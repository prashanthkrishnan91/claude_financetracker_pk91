"use client";

/**
 * IntelV3Cockpit — Investment Committee workspace.
 *
 * Stage 4C redesign. Calm, boutique Intel surface for held-position intelligence.
 *
 * Layout:
 *   - Committee identity band (certification status + Run Intel button)
 *   - Portfolio overview (plain-English summary from portfolio_command_center)
 *   - Action filter rail: ALL / BUY / HOLD / TRIM / SELL (LOCKED contract)
 *   - Action card grid
 *   - What Changed strip (if snapshot.what_changed exists)
 *   - Opportunity Radar (Coming-Later chrome only)
 *   - Detail drawer (IntelV3Drawer) for selected card
 *
 * Invariants:
 *   - Intel v3 authority and route behavior unchanged
 *   - NEXT_PUBLIC_INTEL_V3_VISIBLE_SNAPSHOT_ENABLED split preserved
 *   - No legacy v2 blending
 *   - Filter contract: ALL / BUY / HOLD / TRIM / SELL only
 *   - Run Intel button behavior identical to pre-Stage-4C
 *   - Uses action-* design tokens; no raw Tailwind color classes
 */

import { useState, useEffect, useRef, useCallback } from "react";
import { cn } from "@/lib/utils";
import { useIntelV3Snapshot, useRunIntelV3 } from "@/lib/hooks";
import { buildBannerState, buildStatusPillState, onDemandDrainNote } from "@/lib/intel-v3-banner";
import { buildPortfolioEvidenceSummary } from "@/lib/intel-v3-explanation";
import { IntelV3Card } from "./IntelV3Card";
import { IntelV3Drawer } from "./IntelV3Drawer";
import { DataHealthDrawer } from "./DataHealthDrawer";
import { ComingLaterPanel } from "./IntelV3Primitives";
import { Spinner } from "@/components/ui/Spinner";
import type {
  IntelV3HeldCard,
  IntelV3Action,
  IntelV3Snapshot,
  IntelV3RunResult,
} from "@/lib/api";

// LOCKED: Intel v3 visible filter contract — ALL / BUY / HOLD / TRIM / SELL only.
// Never expand or rename. Radar/posture labels must not appear here.
const INTEL_V3_FILTERS: Array<{
  key: "ALL" | IntelV3Action;
  label: string;
  activeClass: string;
}> = [
  { key: "ALL",  label: "All",  activeClass: "bg-surface-elevated text-text-primary border-border-strong" },
  { key: "BUY",  label: "Buy",  activeClass: "bg-action-buy/10 text-action-buy border-action-buy/30" },
  { key: "HOLD", label: "Hold", activeClass: "bg-action-hold/10 text-action-hold border-action-hold/30" },
  { key: "TRIM", label: "Trim", activeClass: "bg-action-trim/10 text-action-trim border-action-trim/30" },
  { key: "SELL", label: "Sell", activeClass: "bg-action-sell/10 text-action-sell border-action-sell/30" },
];

type FilterKey = "ALL" | IntelV3Action;

const REFRESH_POLL_INTERVAL_MS = 15_000;
const REFRESH_POLL_TIMEOUT_MS = 5 * 60 * 1000;

// ── Status pill ───────────────────────────────────────────────────────────────

function CommitteeStatusBand({
  snapshot,
  isRefreshing,
  lastRunResult,
  onRun,
  isRunDisabled,
  onDataHealth,
}: {
  snapshot: IntelV3Snapshot | null;
  isRefreshing: boolean;
  lastRunResult?: IntelV3RunResult | null;
  onRun: () => void;
  isRunDisabled: boolean;
  onDataHealth?: () => void;
}) {
  const [diagOpen, setDiagOpen] = useState(false);
  const pillState = buildStatusPillState(snapshot, isRefreshing, lastRunResult);
  const bannerState = buildBannerState(snapshot, isRefreshing, lastRunResult);
  const drainNote = onDemandDrainNote(lastRunResult);

  const pillClass: Record<string, string> = {
    green: "bg-action-buy/10 text-action-buy border-action-buy/30",
    amber: "bg-action-trim/10 text-action-trim border-action-trim/30",
    red:   "bg-action-sell/10 text-action-sell border-action-sell/30",
    grey:  "bg-surface-elevated text-text-muted border-border",
  };

  const certCount = snapshot?.certified_holding_count ?? snapshot?.certification_summary?.certified_holding_count;
  const totalCount = snapshot?.total_holding_count ?? snapshot?.certification_summary?.total_holding_count;
  const failedTickers = snapshot?.failed_tickers_in_certification ?? [];
  const latestRunAt = snapshot?.certification_summary?.latest_agent_run_at
    ? new Date(snapshot.certification_summary.latest_agent_run_at).toLocaleString()
    : null;

  return (
    <div className="flex items-start gap-3 flex-wrap">
      {/* Status area */}
      <div className="flex-1 rounded-lg border border-border bg-surface px-3 py-2.5 space-y-1.5">
        <div className="flex items-center gap-2 flex-wrap">
          <span className="text-[10px] font-semibold uppercase tracking-widest text-text-muted">
            Intelligence status
          </span>
          <span
            className={cn(
              "rounded-full border px-2 py-0.5 text-[11px] font-semibold",
              pillClass[pillState.tone] ?? pillClass.grey
            )}
          >
            {pillState.pill}
          </span>
          {isRefreshing && <Spinner className="h-3 w-3 text-text-muted" />}
        </div>
        <p className="text-[11px] text-text-secondary">{pillState.line}</p>
        {drainNote && (
          <p className="text-[11px] text-text-muted opacity-80">{drainNote}</p>
        )}
        <div className="flex items-center gap-3">
          <button
            onClick={() => setDiagOpen((v) => !v)}
            className="text-[10px] text-text-muted hover:text-text-primary transition-colors motion-reduce:transition-none"
            aria-expanded={diagOpen}
          >
            {diagOpen ? "▲ Hide diagnostics" : "▼ Diagnostics"}
          </button>
          {onDataHealth && (
            <button
              onClick={onDataHealth}
              className="text-[10px] text-text-muted hover:text-text-primary transition-colors motion-reduce:transition-none"
            >
              Data Health
            </button>
          )}
        </div>
        {diagOpen && (
          <div className="mt-1 pt-1.5 border-t border-border space-y-0.5 text-[11px] text-text-muted">
            <p className="font-medium text-text-secondary">{bannerState.headline}</p>
            {bannerState.detail && <p className="opacity-80">{bannerState.detail}</p>}
            {certCount !== undefined && totalCount !== undefined && (
              <p>Coverage: {certCount}/{totalCount} certified.</p>
            )}
            {latestRunAt && <p>Latest analyst run: {latestRunAt}.</p>}
            <p>
              Source:{" "}
              <span className="font-medium">
                {snapshot?.snapshot_source === "worker_certified"
                  ? "worker certified"
                  : snapshot?.snapshot_source === "certification_failed"
                  ? "certification failed"
                  : snapshot?.snapshot_source ?? "none"}
              </span>
            </p>
            {failedTickers.length > 0 && (
              <p className="text-action-sell">
                Failed: {failedTickers.slice(0, 5).join(", ")}
                {failedTickers.length > 5 ? ` +${failedTickers.length - 5} more` : ""}
              </p>
            )}
          </div>
        )}
      </div>

      {/* Run Intel button — behavior identical to pre-4C */}
      <button
        onClick={onRun}
        disabled={isRunDisabled}
        className={cn(
          "shrink-0 px-3 py-1.5 rounded-md bg-accent text-background text-xs font-semibold",
          "hover:bg-accent-hover transition-colors motion-reduce:transition-none",
          "disabled:opacity-50 flex items-center gap-1.5"
        )}
      >
        {isRunDisabled ? <Spinner className="h-3 w-3" /> : null}
        {isRunDisabled ? "Updating…" : "Run Intel"}
      </button>
    </div>
  );
}

// ── Portfolio overview ────────────────────────────────────────────────────────

function PortfolioOverview({ snapshot }: { snapshot: IntelV3Snapshot }) {
  const cc = snapshot.portfolio_command_center;

  const items = [
    { key: "BUY",  label: "Buy",  value: cc.buy_count,  class: "text-action-buy" },
    { key: "HOLD", label: "Hold", value: cc.hold_count, class: "text-action-hold" },
    { key: "TRIM", label: "Trim", value: cc.trim_count, class: "text-action-trim" },
    { key: "SELL", label: "Sell", value: cc.sell_count, class: "text-action-sell" },
  ];

  return (
    <div className="rounded-xl border border-border bg-surface px-4 py-4">
      <div className="flex items-center justify-between mb-3">
        <h2 className="font-display text-base text-text-primary">Investment Committee</h2>
        <span className="text-[11px] text-text-muted">
          {cc.total_holdings} position{cc.total_holdings !== 1 ? "s" : ""}
        </span>
      </div>
      <div className="flex items-center gap-4 flex-wrap">
        {items.map((item) => (
          <div key={item.key} className="flex items-baseline gap-1">
            <span className={cn("text-xl font-bold font-display", item.class)}>
              {item.value}
            </span>
            <span className="text-xs text-text-muted">{item.label}</span>
          </div>
        ))}
        {cc.high_conviction > 0 && (
          <div className="flex items-baseline gap-1 ml-auto">
            <span className="text-sm font-semibold text-text-secondary">{cc.high_conviction}</span>
            <span className="text-xs text-text-muted">high conviction</span>
          </div>
        )}
        {cc.thin_evidence > 0 && (
          <div className="flex items-baseline gap-1">
            <span className="text-sm font-semibold text-text-muted">{cc.thin_evidence}</span>
            <span className="text-xs text-text-muted">thin data</span>
          </div>
        )}
      </div>
    </div>
  );
}

// ── Action filter rail ────────────────────────────────────────────────────────

function FilterRail({
  filter,
  setFilter,
  counts,
}: {
  filter: FilterKey;
  setFilter: (f: FilterKey) => void;
  counts: Record<string, number>;
}) {
  return (
    <div className="flex items-center gap-1.5 flex-wrap" role="group" aria-label="Filter by action">
      {INTEL_V3_FILTERS.map((f) => {
        const isActive = filter === f.key;
        const count = counts[f.key];
        return (
          <button
            key={f.key}
            onClick={() => setFilter(f.key)}
            aria-pressed={isActive}
            className={cn(
              "px-3 py-1 rounded-full border text-xs font-semibold transition-colors motion-reduce:transition-none",
              "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/60",
              isActive
                ? f.activeClass
                : "border-border text-text-muted bg-transparent hover:text-text-primary hover:bg-surface-elevated"
            )}
          >
            {f.label}
            {count !== undefined && count > 0 && (
              <span className="ml-1.5 opacity-70">{count}</span>
            )}
          </button>
        );
      })}
    </div>
  );
}

// ── What Changed strip ────────────────────────────────────────────────────────

function WhatChangedStrip({ items }: { items: string[] }) {
  if (items.length === 0) return null;
  return (
    <div className="rounded-lg border border-border bg-surface px-4 py-3">
      <p className="text-[10px] font-semibold uppercase tracking-widest text-text-muted mb-2">
        What changed since last run
      </p>
      <ul className="space-y-1">
        {items.map((item, i) => (
          <li key={i} className="text-xs text-text-secondary flex items-start gap-1.5">
            <span className="shrink-0 mt-0.5 text-accent" aria-hidden="true">→</span>
            <span>{item}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

// ── Evidence Summary Band ─────────────────────────────────────────────────────

function EvidenceSummaryBand({ cards }: { cards: IntelV3HeldCard[] }) {
  const hasAnyExplanation = cards.some(
    (c) => c.detail_drawer_payload?.evidence_explanation != null
  );

  if (!hasAnyExplanation) return null;

  const summary = buildPortfolioEvidenceSummary(cards);
  const technicalUsable = summary.technicalUsableCount > 0;
  const sentimentUsable = summary.sentimentUsableCount > 0;

  const { fundamentalsUsableCount, cardsWithExplanation } = summary;
  const companyDataLabel =
    fundamentalsUsableCount === 0
      ? "Some company data missing or blocked"
      : fundamentalsUsableCount === cardsWithExplanation
      ? "Company data available"
      : `Company data usable for ${fundamentalsUsableCount}/${cardsWithExplanation}`;
  const companyDataStyle =
    fundamentalsUsableCount === 0
      ? "border-action-trim/30 bg-action-trim/10 text-action-trim"
      : "border-action-buy/30 bg-action-buy/10 text-action-buy";

  return (
    <div className="rounded-xl border border-border bg-surface px-4 py-4 space-y-3">
      <div className="flex items-center gap-2">
        <h3 className="text-[10px] font-semibold uppercase tracking-widest text-text-muted">
          Evidence quality
        </h3>
      </div>

      {/* Support tier counts */}
      <div className="flex items-center gap-4 flex-wrap">
        {summary.safeCount > 0 && (
          <div className="flex items-baseline gap-1">
            <span className="text-sm font-bold text-action-buy">{summary.safeCount}</span>
            <span className="text-xs text-text-muted">better supported</span>
          </div>
        )}
        {summary.limitedCount > 0 && (
          <div className="flex items-baseline gap-1">
            <span className="text-sm font-bold text-text-secondary">{summary.limitedCount}</span>
            <span className="text-xs text-text-muted">evidence limited</span>
          </div>
        )}
        {summary.blockedCount > 0 && (
          <div className="flex items-baseline gap-1">
            <span className="text-sm font-bold text-action-trim">{summary.blockedCount}</span>
            <span className="text-xs text-text-muted">data issues</span>
          </div>
        )}
        {summary.convictionCappedCount > 0 && (
          <div className="flex items-baseline gap-1 ml-auto">
            <span className="text-sm font-semibold text-text-muted">{summary.convictionCappedCount}</span>
            <span className="text-xs text-text-muted">conviction capped</span>
          </div>
        )}
      </div>

      {/* Evidence lane availability */}
      <div className="pt-2 border-t border-border space-y-1.5">
        <p className="text-[10px] font-semibold uppercase tracking-widest text-text-muted">
          Evidence sources
        </p>
        <div className="flex flex-wrap gap-2">
          <span className={cn("text-[11px] px-2 py-0.5 rounded border font-medium", companyDataStyle)}>
            {companyDataLabel}
          </span>
          <span className={cn(
            "text-[11px] px-2 py-0.5 rounded border font-medium",
            technicalUsable
              ? "border-action-buy/30 bg-action-buy/10 text-action-buy"
              : "border-border bg-surface-elevated text-text-muted"
          )}>
            {technicalUsable ? "Price signals contributing" : "Price signals not yet usable"}
          </span>
          <span className={cn(
            "text-[11px] px-2 py-0.5 rounded border font-medium",
            sentimentUsable
              ? "border-action-buy/30 bg-action-buy/10 text-action-buy"
              : "border-border bg-surface-elevated text-text-muted"
          )}>
            {sentimentUsable ? "News & sentiment contributing" : "News & sentiment not yet usable"}
          </span>
        </div>
        {(!technicalUsable || !sentimentUsable) && (
          <p className="text-[10px] text-text-muted leading-snug max-w-md">
            The engine is conservative when supporting signals are thin. Recommendations
            still reflect company fundamentals — missing signals cause confidence caps,
            not false confidence.
          </p>
        )}
      </div>
    </div>
  );
}

// ── Empty states ──────────────────────────────────────────────────────────────

function EmptySnapshotState({
  onRun,
  isRunning,
}: {
  onRun: () => void;
  isRunning: boolean;
}) {
  return (
    <div className="flex flex-col items-center justify-center py-20 gap-5">
      <div className="text-center space-y-2">
        <p className="font-display text-base text-text-primary">Investment Committee</p>
        <p className="text-sm text-text-muted max-w-xs">
          Run Intel to generate your first committee view for all held positions.
        </p>
      </div>
      <button
        onClick={onRun}
        disabled={isRunning}
        className={cn(
          "px-4 py-2 rounded-lg bg-accent text-background text-sm font-semibold",
          "hover:bg-accent-hover transition-colors motion-reduce:transition-none",
          "disabled:opacity-50 flex items-center gap-2"
        )}
      >
        {isRunning ? <Spinner className="h-4 w-4" /> : null}
        {isRunning ? "Starting…" : "Run Intel"}
      </button>
    </div>
  );
}

// ── Card grid ────────────────────────────────────────────────────────────────

function CardGrid({
  cards,
  onSelect,
  emptyLabel,
}: {
  cards: IntelV3HeldCard[];
  onSelect: (card: IntelV3HeldCard) => void;
  emptyLabel: string;
}) {
  if (cards.length === 0) {
    return <p className="text-sm text-text-muted py-6">{emptyLabel}</p>;
  }
  return (
    <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
      {cards.map((card) => (
        <IntelV3Card key={card.ticker} card={card} onSelect={onSelect} />
      ))}
    </div>
  );
}


// ── Main cockpit ──────────────────────────────────────────────────────────────

export function IntelV3Cockpit() {
  const [filter, setFilter] = useState<FilterKey>("ALL");
  const [selectedCard, setSelectedCard] = useState<IntelV3HeldCard | null>(null);
  const [isRefreshing, setIsRefreshing] = useState(false);
  const [lastRunResult, setLastRunResult] = useState<IntelV3RunResult | null>(null);
  const [dataHealthOpen, setDataHealthOpen] = useState(false);

  const refreshStartedAt = useRef<number | null>(null);
  const pollTimerRef = useRef<NodeJS.Timeout | null>(null);

  const { data: snapshot, isLoading, refetch: refetchSnapshot } = useIntelV3Snapshot();
  const runMutation = useRunIntelV3();

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
      const isNewerThanClick =
        refreshStartedAt.current !== null &&
        new Date(snap.generated_at).getTime() > refreshStartedAt.current;

      if ((isCertified || isCertificationFailed) && isNewerThanClick) {
        stopPolling();
      }
    }, REFRESH_POLL_INTERVAL_MS);
  }, [refetchSnapshot, stopPolling]);

  useEffect(() => {
    return () => {
      if (pollTimerRef.current) clearInterval(pollTimerRef.current);
    };
  }, []);

  // Stop polling when snapshot becomes certified after the click
  useEffect(() => {
    if (!isRefreshing || !snapshot) return;
    const isCertified =
      snapshot.snapshot_source === "worker_certified" &&
      typeof snapshot.certified_holding_count === "number" &&
      typeof snapshot.total_holding_count === "number" &&
      snapshot.total_holding_count > 0 &&
      snapshot.certified_holding_count === snapshot.total_holding_count;
    const isCertificationFailed = snapshot.snapshot_source === "certification_failed";
    const isNewerThanClick =
      refreshStartedAt.current !== null &&
      new Date(snapshot.generated_at).getTime() > refreshStartedAt.current;
    if ((isCertified || isCertificationFailed) && isNewerThanClick) {
      stopPolling();
    }
  }, [snapshot, isRefreshing, stopPolling]);

  const handleRun = () => {
    runMutation.mutate(undefined, {
      onSuccess: (result) => {
        setLastRunResult(result);
        const isNoOpRun =
          result.status === "analyst_evidence_current" ||
          result.status === "mapping_version_recertified" ||
          result.status === "mapping_version_recertification_failed" ||
          (result.queued_ticker_count === 0 && result.existing_certified_snapshot === true);
        if (isNoOpRun) {
          refetchSnapshot();
          return;
        }
        startPolling();
      },
      onError: () => {
        setIsRefreshing(false);
      },
    });
  };

  const allCards = snapshot?.current_holdings ?? [];
  const filteredCards =
    filter === "ALL" ? allCards : allCards.filter((c) => c.action === filter);

  const counts: Record<string, number> = {
    ALL: allCards.length,
    ...(snapshot?.action_counts ?? {}),
  };

  // ── Loading ────────────────────────────────────────────────────────────────

  if (isLoading) {
    return (
      <div className="flex justify-center py-20">
        <Spinner className="h-6 w-6" />
      </div>
    );
  }

  // ── No snapshot, not refreshing ────────────────────────────────────────────

  if (!snapshot && !isLoading && !isRefreshing) {
    return <EmptySnapshotState onRun={handleRun} isRunning={runMutation.isPending} />;
  }

  // ── Refreshing, no snapshot yet ────────────────────────────────────────────

  if (!snapshot && isRefreshing) {
    return (
      <div className="space-y-5">
        <CommitteeStatusBand
          snapshot={null}
          isRefreshing={isRefreshing}
          lastRunResult={lastRunResult}
          onRun={handleRun}
          isRunDisabled={runMutation.isPending}
          onDataHealth={() => setDataHealthOpen(true)}
        />
        <div className="flex items-center justify-center py-16 gap-3 text-text-muted">
          <Spinner className="h-5 w-5" />
          <span className="text-sm">Portfolio intelligence is updating. Results appear here automatically.</span>
        </div>
      </div>
    );
  }

  if (!snapshot) return null;

  // ── Main committee view ────────────────────────────────────────────────────

  const filterLabel =
    filter === "ALL"
      ? `All holdings (${allCards.length})`
      : `${filter.charAt(0) + filter.slice(1).toLowerCase()} (${filteredCards.length})`;

  return (
    <div className="space-y-5">

      {/* Committee status band + Run Intel */}
      <CommitteeStatusBand
        snapshot={snapshot}
        isRefreshing={isRefreshing}
        lastRunResult={lastRunResult}
        onRun={handleRun}
        isRunDisabled={runMutation.isPending}
        onDataHealth={() => setDataHealthOpen(true)}
      />

      {/* Portfolio overview */}
      <PortfolioOverview snapshot={snapshot} />

      {/* Stage 7 — Evidence quality summary (when governance data is present) */}
      <EvidenceSummaryBand cards={allCards} />

      {/* Action filter rail — LOCKED: ALL/BUY/HOLD/TRIM/SELL only */}
      <FilterRail filter={filter} setFilter={setFilter} counts={counts} />

      {/* Card grid */}
      <div>
        <p className="text-[10px] font-semibold uppercase tracking-widest text-text-muted mb-3">
          {filterLabel}
        </p>
        <CardGrid
          cards={filteredCards}
          onSelect={setSelectedCard}
          emptyLabel={
            filter === "ALL"
              ? "No holdings found."
              : `No ${filter} positions right now.`
          }
        />
      </div>

      {/* What Changed strip */}
      <WhatChangedStrip items={snapshot.what_changed} />

      {/* Opportunity Radar — Coming-Later chrome only */}
      <div>
        <p className="text-[10px] font-semibold uppercase tracking-widest text-text-muted mb-3">
          Opportunity Radar
        </p>
        <ComingLaterPanel
          title="Radar candidates"
          caption="Launches after held-position intelligence is stable. Radar rows will appear here in a future stage."
        />
      </div>

      {/* Detail drawer */}
      <IntelV3Drawer card={selectedCard} onClose={() => setSelectedCard(null)} />

      {/* Data Health drawer — mounted only when open so hooks don't run while closed */}
      {dataHealthOpen && <DataHealthDrawer open={dataHealthOpen} onClose={() => setDataHealthOpen(false)} />}
    </div>
  );
}
