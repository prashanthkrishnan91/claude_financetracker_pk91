"use client";

import { useState, useEffect, useCallback } from "react";
import { cn, formatCurrency } from "@/lib/utils";
import { usePositions, useIntelV3Snapshot, useDecisionMemoryLogs } from "@/lib/hooks";
import { InlineLoader } from "@/components/ui/Spinner";
import { EmptyState } from "@/components/ui/EmptyState";
import { COMING_LATER_CANONICAL_CAPTION } from "@/components/cards/IntelV3PrimitivesData";
import {
  buildLedgerData,
  buildHoldingDrawerData,
  actionToLabel,
  actionChipStyle,
  evidenceBandToFreshnessCue,
  formatRelativeAge,
  type LedgerHolding,
  type LedgerData,
  type ThesisHealthStatus,
  type SourceFreshnessStatus,
} from "@/lib/portfolio-ledger";
import { formatUpdatedAtSafe } from "@/lib/intel-v3-evidence";
import type { DecisionMemoryLog } from "@/lib/api";

// ── Portfolio Living Thesis Ledger ────────────────────────────────────────────

export default function PortfolioPage() {
  const { data: positions, isLoading: posLoading, error: posError } = usePositions();
  const { data: intelSnapshot } = useIntelV3Snapshot();
  const { data: decisionLogs } = useDecisionMemoryLogs(25);

  const [drawerTicker, setDrawerTicker] = useState<string | null>(null);

  const ledger: LedgerData | null =
    positions && positions.length > 0
      ? buildLedgerData(positions, intelSnapshot, decisionLogs ?? [])
      : null;

  const openDrawer = useCallback((ticker: string) => setDrawerTicker(ticker), []);
  const closeDrawer = useCallback(() => setDrawerTicker(null), []);

  const selectedHolding = ledger?.holdings.find(
    h => h.ticker.toUpperCase() === drawerTicker?.toUpperCase()
  );

  return (
    <>
      {/* Top bar */}
      <header className="sticky top-0 z-50 bg-background/80 backdrop-blur-xl border-b border-border px-4 py-3">
        <div className="max-w-7xl mx-auto flex items-center justify-between">
          <div>
            <h1 className="text-xl font-display text-text-primary">Portfolio</h1>
            <p className="text-[10px] uppercase tracking-label text-text-muted opacity-50 leading-none mt-0.5">
              Living Thesis Ledger
            </p>
          </div>
          {intelSnapshot && (
            <span className="text-[10px] text-text-muted opacity-60">
              Intel {formatRelativeAge(intelSnapshot.generated_at)}
            </span>
          )}
        </div>
      </header>

      <main className="max-w-7xl mx-auto px-4 py-6 space-y-5">

        {posLoading && <InlineLoader text="Loading portfolio…" />}

        {posError && (
          <EmptyState title="Failed to load holdings" description="Check your connection and try again." />
        )}

        {!posLoading && !posError && !ledger && (
          <EmptyState
            title="No positions yet"
            description="Import a CSV or sync with Plaid to populate your ledger."
          />
        )}

        {ledger && (
          <>
            {/* ── Thesis Health + Source Freshness (2-col on desktop) ───────── */}
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
              <ThesisHealthPanel summary={ledger.thesisHealth} />
              <SourceFreshnessPanel summary={ledger.sourceFreshness} />
            </div>

            {/* ── Editorial Holdings Ledger ──────────────────────────────────── */}
            <section className="card-glass overflow-hidden">
              <div className="px-5 pt-5 pb-3">
                <p className="text-[10px] uppercase tracking-label text-text-muted opacity-60">
                  Holdings · {ledger.holdings.length} position{ledger.holdings.length !== 1 ? "s" : ""}
                </p>
              </div>
              <HoldingsLedger
                holdings={ledger.holdings}
                onSelect={openDrawer}
                selected={drawerTicker}
              />
            </section>

            {/* ── Concentration + Category (2-col on desktop) ───────────────── */}
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
              <ConcentrationPanel top5={ledger.concentrationTop5} />
              <CategoryExposurePanel rows={ledger.categoryExposure} hasIntel={ledger.hasIntelData} />
            </div>

            {/* ── Coming-Later capsules ─────────────────────────────────────── */}
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
              <ComingLaterCapsule title="Business Story" />
              <ComingLaterCapsule title="Company Strategy Primer" />
              <ComingLaterCapsule title="What Would Make This Thesis Wrong" />
              <ComingLaterCapsule title="Good Company vs Good Stock" />
            </div>
          </>
        )}
      </main>

      {/* ── Holding detail drawer ─────────────────────────────────────────── */}
      {selectedHolding && (
        <HoldingDrawer
          holding={selectedHolding}
          decisionLogs={decisionLogs ?? []}
          onClose={closeDrawer}
        />
      )}
    </>
  );
}

// ── Holdings Ledger ───────────────────────────────────────────────────────────

function HoldingsLedger({
  holdings,
  onSelect,
  selected,
}: {
  holdings: LedgerHolding[];
  onSelect: (ticker: string) => void;
  selected: string | null;
}) {
  const sorted = [...holdings].sort(
    (a, b) => (b.marketValue ?? 0) - (a.marketValue ?? 0)
  );

  return (
    <div className="divide-y divide-border/40">
      {sorted.map(h => (
        <HoldingRow
          key={h.ticker}
          holding={h}
          onSelect={onSelect}
          isSelected={selected === h.ticker}
        />
      ))}
    </div>
  );
}

function HoldingRow({
  holding: h,
  onSelect,
  isSelected,
}: {
  holding: LedgerHolding;
  onSelect: (ticker: string) => void;
  isSelected: boolean;
}) {
  const chipClass = actionChipStyle(h.intelAction);
  const actionLabel = actionToLabel(h.intelAction);
  const freshnessLabel = evidenceBandToFreshnessCue(h.evidenceBand);

  return (
    <button
      onClick={() => onSelect(h.ticker)}
      className={cn(
        "w-full text-left px-5 py-3.5 hover:bg-surface-elevated/40 transition-colors",
        isSelected && "bg-surface-elevated/60"
      )}
      aria-label={`View thesis for ${h.ticker}`}
    >
      {/* ── Mobile-first card layout ─────────────────────────────────── */}
      <div className="flex items-start justify-between gap-3">
        {/* Left: ticker, name, badges */}
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-1.5 mb-0.5">
            <span className="ticker-symbol text-sm">{h.ticker}</span>
            {h.intelAction !== "NO_INTEL" && (
              <span
                className={cn(
                  "text-[10px] px-1.5 py-0.5 rounded border font-semibold uppercase tracking-wide",
                  chipClass
                )}
              >
                {actionLabel}
              </span>
            )}
            {h.isStaleOrThin && h.hasIntel && (
              <span className="text-[9px] px-1 py-0.5 rounded bg-warning/10 text-warning border border-warning/20 uppercase tracking-wide">
                Thin
              </span>
            )}
            {!h.hasIntel && (
              <span className="text-[9px] px-1 py-0.5 rounded bg-surface-elevated text-text-muted border border-border uppercase tracking-wide">
                No Intel
              </span>
            )}
          </div>
          <p className="text-[11px] text-text-muted truncate leading-snug">{h.name}</p>
          {h.hasIntel && (
            <p className="text-[10px] text-text-muted opacity-60 mt-0.5 leading-snug">
              {freshnessLabel} · {formatRelativeAge(h.intelUpdatedAt)}
            </p>
          )}
        </div>

        {/* Right: value + weight */}
        <div className="text-right shrink-0">
          {h.marketValue !== undefined && (
            <p className="data-value text-sm">{formatCurrency(h.marketValue)}</p>
          )}
          {h.portfolioWeight !== undefined && (
            <p className="text-[10px] text-text-muted font-mono tabular-nums">
              {h.portfolioWeight.toFixed(1)}%
            </p>
          )}
        </div>
      </div>

      {/* Thesis state — one line when available */}
      {h.thesisState && (
        <p className="text-[11px] text-text-secondary mt-1.5 ml-0 leading-snug line-clamp-1">
          {h.thesisState}
        </p>
      )}
    </button>
  );
}

// ── Concentration Panel ───────────────────────────────────────────────────────

function ConcentrationPanel({ top5 }: { top5: LedgerHolding[] }) {
  const maxWeight = Math.max(...top5.map(h => h.portfolioWeight ?? 0), 1);

  return (
    <section className="card-glass p-5">
      <p className="text-[10px] uppercase tracking-label text-text-muted opacity-60 mb-4">
        Top Concentration
      </p>
      {top5.length === 0 ? (
        <p className="text-sm text-text-muted italic">No holdings data.</p>
      ) : (
        <div className="space-y-3">
          {top5.map(h => (
            <div key={h.ticker}>
              <div className="flex items-center justify-between mb-1">
                <span className="text-sm font-medium text-text-primary">{h.ticker}</span>
                <div className="flex items-center gap-2">
                  <span className="text-[10px] text-text-muted font-mono tabular-nums">
                    {h.portfolioWeight !== undefined
                      ? `${h.portfolioWeight.toFixed(1)}%`
                      : "—"}
                  </span>
                  {h.intelAction !== "NO_INTEL" && (
                    <span
                      className={cn(
                        "text-[9px] px-1 py-0.5 rounded border font-semibold uppercase",
                        actionChipStyle(h.intelAction)
                      )}
                    >
                      {actionToLabel(h.intelAction)}
                    </span>
                  )}
                </div>
              </div>
              <div className="h-1 bg-surface-elevated rounded-full overflow-hidden">
                <div
                  className="h-full rounded-full bg-accent/40 transition-all"
                  style={{ width: `${((h.portfolioWeight ?? 0) / maxWeight) * 100}%` }}
                />
              </div>
            </div>
          ))}
        </div>
      )}
    </section>
  );
}

// ── Category Exposure Panel ───────────────────────────────────────────────────

function CategoryExposurePanel({
  rows,
  hasIntel,
}: {
  rows: ReturnType<typeof import("@/lib/portfolio-ledger").buildCategoryExposure>;
  hasIntel: boolean;
}) {
  return (
    <section className="card-glass p-5">
      <p className="text-[10px] uppercase tracking-label text-text-muted opacity-60 mb-4">
        Category Exposure
      </p>
      {rows.length === 0 ? (
        <p className="text-sm text-text-muted italic">No category data.</p>
      ) : (
        <div className="space-y-2">
          {rows.map(row => (
            <div key={row.category} className="flex items-center justify-between">
              <span className="text-sm text-text-secondary">{row.category}</span>
              <div className="flex items-center gap-3">
                <span className="text-[10px] text-text-muted">{row.count} position{row.count !== 1 ? "s" : ""}</span>
                {row.pct !== undefined ? (
                  <span className="text-[11px] font-mono tabular-nums text-text-primary">
                    {row.pct.toFixed(1)}%
                  </span>
                ) : (
                  <span className="text-[10px] text-text-muted italic">—</span>
                )}
              </div>
            </div>
          ))}
        </div>
      )}
      {!hasIntel && (
        <p className="text-[10px] text-text-muted italic mt-3 pt-3 border-t border-border-subtle/40">
          Sector/theme breakdown — Coming later when Intel is run.
        </p>
      )}
    </section>
  );
}

// ── Thesis Health Panel ───────────────────────────────────────────────────────

const THESIS_HEALTH_STYLES: Record<ThesisHealthStatus, string> = {
  strong:          "bg-action-buy/10 text-action-buy border-action-buy/20",
  mixed:           "bg-action-hold/10 text-action-hold border-action-hold/20",
  needs_attention: "bg-action-trim/10 text-action-trim border-action-trim/20",
  unavailable:     "bg-surface-elevated text-text-muted border-border",
};

function ThesisHealthPanel({
  summary,
}: {
  summary: LedgerData["thesisHealth"];
}) {
  return (
    <section className="card-glass p-5">
      <p className="text-[10px] uppercase tracking-label text-text-muted opacity-60 mb-3">
        Thesis Health
      </p>
      <div className="flex items-start gap-3">
        <span
          className={cn(
            "text-[11px] px-2 py-1 rounded border font-medium shrink-0 mt-0.5",
            THESIS_HEALTH_STYLES[summary.status]
          )}
        >
          {summary.statusLabel}
        </span>
        <div>
          <p className="text-sm text-text-secondary leading-snug">{summary.detail}</p>
          {summary.totalCount > 0 && (
            <p className="text-[10px] text-text-muted mt-1.5">
              {summary.totalCount} holding{summary.totalCount !== 1 ? "s" : ""}
              {summary.noIntelCount > 0 && ` · ${summary.noIntelCount} without Intel`}
            </p>
          )}
        </div>
      </div>
    </section>
  );
}

// ── Source Freshness Panel ────────────────────────────────────────────────────

const FRESHNESS_STYLES: Record<SourceFreshnessStatus, string> = {
  fresh:       "bg-action-buy/10 text-action-buy border-action-buy/20",
  stale:       "bg-action-hold/10 text-action-hold border-action-hold/20",
  hard_stale:  "bg-action-sell/10 text-action-sell border-action-sell/20",
  unavailable: "bg-surface-elevated text-text-muted border-border",
};

function SourceFreshnessPanel({
  summary,
}: {
  summary: LedgerData["sourceFreshness"];
}) {
  return (
    <section className="card-glass p-5">
      <p className="text-[10px] uppercase tracking-label text-text-muted opacity-60 mb-3">
        Source Freshness
      </p>
      <div className="flex items-start gap-3">
        <span
          className={cn(
            "text-[11px] px-2 py-1 rounded border font-medium shrink-0 mt-0.5",
            FRESHNESS_STYLES[summary.overallStatus]
          )}
        >
          {summary.overallLabel}
        </span>
        <div>
          <p className="text-sm text-text-secondary leading-snug">{summary.detail}</p>
          {summary.snapshotGeneratedAt && (
            <p className="text-[10px] text-text-muted mt-1.5">
              Snapshot {formatRelativeAge(summary.snapshotGeneratedAt)}
            </p>
          )}
        </div>
      </div>
    </section>
  );
}

// ── Coming-Later capsule ──────────────────────────────────────────────────────

function ComingLaterCapsule({ title }: { title: string }) {
  return (
    <section className="card-glass p-5 opacity-50">
      <p className="text-[10px] uppercase tracking-label text-text-muted opacity-60 mb-2">
        {title}
      </p>
      <p className="text-sm text-text-muted coming-later italic leading-relaxed">
        {COMING_LATER_CANONICAL_CAPTION}
      </p>
    </section>
  );
}

// ── Holding Drawer ────────────────────────────────────────────────────────────

function HoldingDrawer({
  holding,
  decisionLogs,
  onClose,
}: {
  holding: LedgerHolding;
  decisionLogs: DecisionMemoryLog[];
  onClose: () => void;
}) {
  const drawerData = buildHoldingDrawerData(holding, decisionLogs);
  const chipClass = actionChipStyle(holding.intelAction);

  // Close on Esc
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [onClose]);

  return (
    <>
      {/* Backdrop */}
      <div
        className="fixed inset-0 z-40 bg-black/30 backdrop-blur-sm"
        onClick={onClose}
        aria-hidden="true"
      />

      {/* Drawer panel — mobile: bottom sheet; desktop: right-side drawer */}
      <aside
        role="dialog"
        aria-label={`${holding.ticker} thesis details`}
        aria-modal="true"
        className={cn(
          // Mobile: full-width bottom sheet
          "fixed inset-x-0 bottom-0 z-50",
          "max-h-[88dvh] rounded-t-2xl",
          "border-t border-border-subtle bg-surface",
          "overflow-y-auto overscroll-contain flex flex-col",
          "sheet-slide-up",
          // Desktop: right-side drawer
          "lg:inset-x-auto lg:right-0 lg:top-0 lg:bottom-0",
          "lg:w-full lg:max-w-md lg:max-h-none",
          "lg:rounded-none lg:border-t-0 lg:border-l",
          "lg:[animation:none]",
        )}
      >
        {/* Mobile drag handle */}
        <div className="lg:hidden flex justify-center pt-3 pb-1 shrink-0" aria-hidden="true">
          <div className="w-8 h-1 rounded-full bg-border-strong opacity-50" />
        </div>

        {/* Drawer header */}
        <div className="sticky top-0 bg-surface/95 backdrop-blur-sm border-b border-border-subtle px-5 py-4 flex items-start justify-between gap-3">
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2 mb-0.5">
              <h2 className="ticker-symbol text-lg">{holding.ticker}</h2>
              {holding.intelAction !== "NO_INTEL" && (
                <span
                  className={cn(
                    "text-[11px] px-1.5 py-0.5 rounded border font-semibold uppercase tracking-wide",
                    chipClass
                  )}
                >
                  {actionToLabel(holding.intelAction)}
                </span>
              )}
            </div>
            <p className="text-xs text-text-muted truncate">{holding.name}</p>
          </div>
          <button
            onClick={onClose}
            aria-label="Close thesis drawer"
            className="shrink-0 text-text-muted hover:text-text-primary transition-colors mt-0.5 p-1 rounded focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/60"
          >
            <CloseIcon className="w-4 h-4" />
          </button>
        </div>

        {/* Drawer content */}
        <div className="flex-1 px-5 py-5 space-y-5">

          {/* Stale warning */}
          {drawerData.staleWarning && (
            <div className="bg-warning/8 border border-warning/20 rounded-md px-4 py-3">
              <p className="text-[11px] text-warning leading-snug">{drawerData.staleWarning}</p>
            </div>
          )}

          {/* Position facts */}
          <div>
            <p className="text-[10px] uppercase tracking-label text-text-muted opacity-60 mb-2">
              Current Position
            </p>
            <div className="space-y-1.5">
              <DrawerRow label="Value" value={holding.marketValue !== undefined ? formatCurrency(holding.marketValue) : "—"} />
              <DrawerRow label="Weight" value={holding.portfolioWeight !== undefined ? `${holding.portfolioWeight.toFixed(1)}%` : "—"} />
              <DrawerRow label="Category" value={holding.category} />
              {holding.ltEligible && (
                <DrawerRow label="Long-term eligible" value="Yes" />
              )}
            </div>
          </div>

          {/* Intel action & evidence */}
          {holding.hasIntel && holding.intelCard && (
            <div>
              <p className="text-[10px] uppercase tracking-label text-text-muted opacity-60 mb-2">
                Intel Summary
              </p>
              <div className="space-y-1.5">
                <DrawerRow label="Action" value={actionToLabel(holding.intelAction)} />
                <DrawerRow label="Conviction" value={holding.conviction ?? "—"} />
                <DrawerRow label="Evidence" value={evidenceBandToFreshnessCue(holding.evidenceBand)} />
                {holding.riskLevel && holding.riskLevel !== "LOW" && (
                  <DrawerRow label="Risk" value={`${holding.riskLevel} risk`} />
                )}
                <DrawerRow label="Updated" value={formatRelativeAge(holding.intelUpdatedAt)} />
              </div>
              {holding.intelCard.why_text && (
                <div className="mt-3 p-3 bg-surface-elevated/50 rounded-md">
                  <p className="text-[10px] uppercase tracking-label text-text-muted opacity-50 mb-1">
                    Why
                  </p>
                  <p className="text-xs text-text-secondary leading-relaxed">
                    {holding.intelCard.why_text}
                  </p>
                </div>
              )}
              {holding.intelCard.risk_text && holding.riskLevel !== "LOW" && (
                <div className="mt-2 p-3 bg-surface-elevated/50 rounded-md">
                  <p className="text-[10px] uppercase tracking-label text-text-muted opacity-50 mb-1">
                    Risk note
                  </p>
                  <p className="text-xs text-text-secondary leading-relaxed">
                    {holding.intelCard.risk_text}
                  </p>
                </div>
              )}
            </div>
          )}

          {!holding.hasIntel && (
            <div>
              <p className="text-[10px] uppercase tracking-label text-text-muted opacity-60 mb-2">
                Intel Summary
              </p>
              <p className="text-sm text-text-muted italic">
                No Intel data for this holding — run Intel to generate a thesis.
              </p>
            </div>
          )}

          {/* Thesis state */}
          {holding.thesisState && (
            <div>
              <p className="text-[10px] uppercase tracking-label text-text-muted opacity-60 mb-2">
                Thesis Status
              </p>
              <p className="text-sm text-text-secondary leading-relaxed">{holding.thesisState}</p>
            </div>
          )}

          {/* Last 3 decisions */}
          <div>
            <p className="text-[10px] uppercase tracking-label text-text-muted opacity-60 mb-2">
              Recent Decisions
            </p>
            {!drawerData.hasDecisionHistory ? (
              <p className="text-sm text-text-muted italic">
                No decision history for this holding yet.
              </p>
            ) : (
              <div className="divide-y divide-border/40">
                {drawerData.lastThreeDecisions.map(d => (
                  <div key={d.id} className="py-2.5 flex items-start justify-between gap-2">
                    <div>
                      <p className="text-xs text-text-primary font-medium">
                        {formatUpdatedAtSafe(d.date)}
                      </p>
                      <p className="text-[10px] text-text-muted capitalize">{d.source}</p>
                    </div>
                    <span className="text-[10px] px-1.5 py-0.5 rounded bg-surface-elevated text-text-muted border border-border uppercase tracking-wide shrink-0">
                      {d.status.replace(/_/g, " ")}
                    </span>
                  </div>
                ))}
              </div>
            )}
          </div>

          {/* Coming-Later: thesis timeline/sparkline */}
          <div className="opacity-50 pt-2 border-t border-border-subtle/40">
            <p className="text-[10px] uppercase tracking-label text-text-muted opacity-60 mb-1">
              Thesis Timeline
            </p>
            <p className="text-xs text-text-muted coming-later italic">
              {COMING_LATER_CANONICAL_CAPTION}
            </p>
          </div>

        </div>
      </aside>
    </>
  );
}

// ── Sub-components ────────────────────────────────────────────────────────────

function DrawerRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-baseline justify-between gap-2">
      <span className="text-[11px] text-text-muted">{label}</span>
      <span className="text-[11px] text-text-primary font-medium text-right">{value}</span>
    </div>
  );
}

function CloseIcon({ className }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round">
      <path d="M18 6 6 18M6 6l12 12" />
    </svg>
  );
}
