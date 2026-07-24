"use client";

/**
 * IntelV3HoldingsPanel — Section B of the unified Advisor view.
 *
 * PRESENTATION-ONLY replacement for the retired cockpit component. The page
 * owns the single Intel v3 snapshot query and the single run mutation (the
 * readiness panel renders the only run control); this panel receives the
 * shared snapshot query state via props and renders:
 *   - Investment Committee summary (counts line) + Data Health access
 *   - Evidence-quality summary block
 *   - ALL / BUY / HOLD / TRIM / SELL filter chips (LOCKED contract)
 *   - Holding card grid (IntelV3Card) — rationale-backed cards only, with an
 *     honest exclusion note for holdings whose action has no explanation
 *   - "What changed" strip when the snapshot reports changes
 *   - IntelV3Drawer detail drawer + DataHealthDrawer
 *
 * Invariants:
 *   - No snapshot/run hooks, no mutations, no intervals or polling here
 *   - No run button, no run-state band — the readiness panel owns run state
 *   - Filter contract: ALL / BUY / HOLD / TRIM / SELL only
 *   - Headings are h3 under the page's "Holding actions" h2 section
 */

import { useState } from "react";
import { cn } from "@/lib/utils";
import { buildPortfolioEvidenceSummary } from "@/lib/intel-v3-explanation";
import { partitionRenderableCards } from "@/lib/visibleIntelActions";
import { IntelV3Card } from "@/components/cards/IntelV3Card";
import { IntelV3Drawer } from "@/components/cards/IntelV3Drawer";
import { DataHealthDrawer } from "@/components/cards/DataHealthDrawer";
import { Spinner } from "@/components/ui/Spinner";
import type {
  IntelV3HeldCard,
  IntelV3Action,
  IntelV3Snapshot,
  IntelV3RunTrustAxisCoverage,
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

// ── Investment Committee summary ──────────────────────────────────────────────

function PortfolioOverview({
  snapshot,
  onDataHealth,
}: {
  snapshot: IntelV3Snapshot;
  onDataHealth: () => void;
}) {
  const cc = snapshot.portfolio_command_center;

  const items = [
    { key: "BUY",  label: "Buy",  value: cc.buy_count,  class: "text-action-buy" },
    { key: "HOLD", label: "Hold", value: cc.hold_count, class: "text-action-hold" },
    { key: "TRIM", label: "Trim", value: cc.trim_count, class: "text-action-trim" },
    { key: "SELL", label: "Sell", value: cc.sell_count, class: "text-action-sell" },
  ];

  return (
    <div className="rounded-xl border border-border bg-surface px-4 py-4">
      <div className="flex items-center justify-between mb-3 gap-3 flex-wrap">
        <h3 className="font-display text-base text-text-primary">Investment Committee</h3>
        <div className="flex items-center gap-3">
          <span className="text-[11px] text-text-muted">
            {cc.total_holdings} position{cc.total_holdings !== 1 ? "s" : ""}
          </span>
          <button
            type="button"
            onClick={onDataHealth}
            className="text-[10px] text-text-muted hover:text-text-primary transition-colors motion-reduce:transition-none focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/60 rounded"
          >
            Data Health
          </button>
        </div>
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
      <h3 className="text-[10px] font-semibold uppercase tracking-widest text-text-muted mb-2">
        What changed since last run
      </h3>
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

/** Honest "X/Y contributing" label from the trust contract's authoritative
 * axis coverage — replaces the old boolean-only "contributing" / "not yet
 * usable" chip text with real counts when the contract is present. */
function axisCoverageLabel(
  counts: IntelV3RunTrustAxisCoverage | undefined,
  laneLabel: string,
): { label: string; usable: boolean } {
  if (!counts || counts.expected_count === 0) {
    return { label: `${laneLabel} not applicable`, usable: false };
  }
  if (counts.succeeded_count === 0) {
    return { label: `${laneLabel} not yet usable`, usable: false };
  }
  if (counts.succeeded_count === counts.expected_count) {
    return { label: `${laneLabel} contributing (${counts.succeeded_count}/${counts.expected_count})`, usable: true };
  }
  return {
    label: `${laneLabel} contributing for ${counts.succeeded_count}/${counts.expected_count}`,
    usable: true,
  };
}

function EvidenceSummaryBand({
  cards,
  axisCoverage,
  trustUnknown,
}: {
  cards: IntelV3HeldCard[];
  /** run_trust_contract_v1.axis_coverage — authoritative counts derived
   * from durable specialist outputs/tasks, not card-level heuristics.
   * Absent on legacy/non-distributed snapshots (falls back to per-card
   * evidence_explanation counts). */
  axisCoverage?: Record<string, IntelV3RunTrustAxisCoverage>;
  /** True when run_trust_contract_v1.overall_status === "unknown" (the
   * fail-closed read/reverification-failure overlay). axis_coverage is an
   * empty placeholder in this case, NOT a verified "zero axes applied"
   * fact — technical/sentiment/company-data chips must say so honestly
   * instead of reading as "not applicable" or reusing stale per-card
   * readiness values as if they were reverified. */
  trustUnknown?: boolean;
}) {
  const hasAnyExplanation = cards.some(
    (c) => c.detail_drawer_payload?.evidence_explanation != null
  );

  if (!hasAnyExplanation && !axisCoverage) return null;

  const summary = buildPortfolioEvidenceSummary(cards);
  const technical = trustUnknown
    ? { label: "Price signals could not be verified", usable: false }
    : axisCoverage
    ? axisCoverageLabel(axisCoverage["technical"], "Price signals")
    : { label: summary.technicalUsableCount > 0 ? "Price signals contributing" : "Price signals not yet usable",
        usable: summary.technicalUsableCount > 0 };
  const sentiment = trustUnknown
    ? { label: "News & sentiment could not be verified", usable: false }
    : axisCoverage
    ? axisCoverageLabel(axisCoverage["sentiment"], "News & sentiment")
    : { label: summary.sentimentUsableCount > 0 ? "News & sentiment contributing" : "News & sentiment not yet usable",
        usable: summary.sentimentUsableCount > 0 };
  const technicalUsable = technical.usable;
  const sentimentUsable = sentiment.usable;

  const { fundamentalsUsableCount, cardsWithExplanation } = summary;
  const companyDataLabel = trustUnknown
    ? "Company data could not be verified"
    : fundamentalsUsableCount === 0
      ? "Some company data missing or blocked"
      : fundamentalsUsableCount === cardsWithExplanation
      ? "Company data available"
      : `Company data usable for ${fundamentalsUsableCount}/${cardsWithExplanation}`;
  const companyDataStyle = trustUnknown
    ? "border-border bg-surface-elevated text-text-muted"
    : fundamentalsUsableCount === 0
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
        {summary.unknownCount > 0 && (
          <div className="flex items-baseline gap-1">
            <span className="text-sm font-bold text-text-muted">{summary.unknownCount}</span>
            <span className="text-xs text-text-muted">trust unknown</span>
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
            {technical.label}
          </span>
          <span className={cn(
            "text-[11px] px-2 py-0.5 rounded border font-medium",
            sentimentUsable
              ? "border-action-buy/30 bg-action-buy/10 text-action-buy"
              : "border-border bg-surface-elevated text-text-muted"
          )}>
            {sentiment.label}
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

// ── Excluded holdings note ────────────────────────────────────────────────────
// Honest exclusion: cards without any rationale are never shown as
// recommendation cards, and never silently dropped either.

function ExcludedHoldingsNote({ tickers }: { tickers: string[] }) {
  if (tickers.length === 0) return null;
  return (
    <div className="rounded-lg border border-border bg-surface px-4 py-3 space-y-1.5">
      <p className="text-xs text-text-secondary leading-snug">
        Not shown: {tickers.length} holding{tickers.length === 1 ? "" : "s"} — no
        explanation was available for their current action.
      </p>
      <details className="text-[11px] text-text-muted">
        <summary className="cursor-pointer select-none">Technical detail</summary>
        <p className="mt-1 font-mono tabular-nums break-words">
          Excluded tickers: {tickers.join(", ")}
        </p>
      </details>
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

// ── Main panel ────────────────────────────────────────────────────────────────

export interface IntelV3HoldingsPanelProps {
  /** Latest Intel v3 snapshot from the page's single shared snapshot query. */
  snapshot: IntelV3Snapshot | null;
  /** True while the shared snapshot query is loading. */
  isLoading: boolean;
  /** True when the query settled without a snapshot (none exists yet, or the read failed). */
  noSnapshot: boolean;
}

export function IntelV3HoldingsPanel({
  snapshot,
  isLoading,
  noSnapshot,
}: IntelV3HoldingsPanelProps) {
  const [filter, setFilter] = useState<FilterKey>("ALL");
  const [selectedCard, setSelectedCard] = useState<IntelV3HeldCard | null>(null);
  const [dataHealthOpen, setDataHealthOpen] = useState(false);

  // ── Loading ────────────────────────────────────────────────────────────────

  if (isLoading) {
    return (
      <div className="flex justify-center py-20">
        <Spinner className="h-6 w-6" />
      </div>
    );
  }

  // ── No snapshot ────────────────────────────────────────────────────────────
  // No run control here — the Readiness panel owns the single run button.

  if (!snapshot) {
    return (
      <div className="flex flex-col items-center justify-center py-16 gap-2">
        <p className="font-display text-base text-text-primary">Investment Committee</p>
        <p className="text-sm text-text-muted max-w-xs text-center">
          {noSnapshot
            ? "No committee view exists yet. Start an Intel refresh from the Readiness panel to generate one."
            : "The committee view is not available right now."}
        </p>
      </div>
    );
  }

  // ── Main committee view ────────────────────────────────────────────────────

  const allCards = snapshot.current_holdings ?? [];
  const { renderable, excludedTickers } = partitionRenderableCards(allCards);
  const filteredCards =
    filter === "ALL" ? renderable : renderable.filter((c) => c.action === filter);

  // Counts reflect what is actually renderable — never cards hidden for
  // having no rationale (those are declared in the exclusion note instead).
  const counts: Record<string, number> = {
    ALL: renderable.length,
    BUY: 0,
    HOLD: 0,
    TRIM: 0,
    SELL: 0,
  };
  for (const card of renderable) counts[card.action] += 1;

  const filterLabel =
    filter === "ALL"
      ? `All holdings (${renderable.length})`
      : `${filter.charAt(0) + filter.slice(1).toLowerCase()} (${filteredCards.length})`;

  return (
    <div className="space-y-5">

      {/* Investment Committee summary + Data Health access */}
      <PortfolioOverview snapshot={snapshot} onDataHealth={() => setDataHealthOpen(true)} />

      {/* Evidence quality summary (when governance data is present) */}
      <EvidenceSummaryBand
        cards={allCards}
        axisCoverage={snapshot.run_trust_contract?.axis_coverage}
        trustUnknown={snapshot.run_trust_contract?.overall_status === "unknown"}
      />

      {/* Action filter rail — LOCKED: ALL/BUY/HOLD/TRIM/SELL only */}
      <FilterRail filter={filter} setFilter={setFilter} counts={counts} />

      {/* Card grid — rationale-backed cards only */}
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

      {/* Honest exclusion note for holdings with no available explanation */}
      <ExcludedHoldingsNote tickers={excludedTickers} />

      {/* What Changed strip */}
      <WhatChangedStrip items={snapshot.what_changed} />

      {/* Detail drawer */}
      <IntelV3Drawer card={selectedCard} onClose={() => setSelectedCard(null)} />

      {/* Data Health drawer — mounted only when open so hooks don't run while closed */}
      {dataHealthOpen && <DataHealthDrawer open={dataHealthOpen} onClose={() => setDataHealthOpen(false)} />}
    </div>
  );
}
