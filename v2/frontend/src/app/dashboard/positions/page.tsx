"use client";

/**
 * Positions — primary holdings view.
 * Summary band (value, cost, G/L, cash, allocation, concentration, freshness),
 * holdings list with expandable detail + lazily loaded tax lots, and explicit
 * degraded states when prices/snapshots/Intel are missing. No polling; tax
 * lots are fetched only when a lot section is opened.
 */

import { useState } from "react";
import Link from "next/link";
import { cn, formatCurrency, formatPercent, formatNumber, pnlClass } from "@/lib/utils";
import {
  usePositions,
  usePortfolioSummary,
  useSnapshots,
  useIntelV3Snapshot,
} from "@/lib/hooks";
import { PageLoader, InlineLoader } from "@/components/ui/Spinner";
import { EmptyState } from "@/components/ui/EmptyState";
import {
  computeTotals,
  cashFromSummary,
  computeAllocationSplit,
  computeWeights,
  computeTopConcentration,
  deriveFreshness,
  relativeAgeLabel,
  buildIntelCardMap,
  getHoldingIntel,
  evidenceBandLabel,
  isAuthError,
  NO_CERTIFIED_INTEL_LABEL,
  type HoldingIntel,
} from "@/lib/positions-view";
import {
  useTaxLots,
  findTaxLotHolding,
  type TaxLotHolding,
  type TaxLotRow,
} from "@/lib/tax-lots";
import type { Position } from "@/lib/api";

// Literal class map so Tailwind's content scan keeps these component classes.
const ACTION_BADGE_CLASS: Record<string, string> = {
  BUY: "action-badge-buy",
  HOLD: "action-badge-hold",
  TRIM: "action-badge-trim",
  SELL: "action-badge-sell",
};

const FOCUS_RING =
  "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/60";

export default function PositionsPage() {
  const {
    data: positions,
    isLoading: posLoading,
    error: posError,
    refetch: refetchPositions,
  } = usePositions();
  const { data: summary } = usePortfolioSummary();
  const { data: snapshots } = useSnapshots();
  const { data: intelSnapshot, isLoading: intelLoading } = useIntelV3Snapshot();

  const [expandedTicker, setExpandedTicker] = useState<string | null>(null);

  const hasPositions = !!positions && positions.length > 0;
  const totals = hasPositions ? computeTotals(positions) : null;
  const cash = cashFromSummary(summary);
  const allocation = hasPositions ? computeAllocationSplit(positions) : [];
  const weights = hasPositions ? computeWeights(positions) : new Map<string, number>();
  const topConcentration = hasPositions ? computeTopConcentration(positions) : null;
  const freshness = deriveFreshness(snapshots, positions);
  const hasIntelSnapshot = !!intelSnapshot;
  const intelMap = buildIntelCardMap(intelSnapshot);

  const sorted = hasPositions
    ? [...positions].sort(
        (a, b) => (b.market_value ?? -1) - (a.market_value ?? -1)
      )
    : [];

  return (
    <>
      <header className="page-header">
        <div className="page-header-inner">
          <h1 className="text-xl font-display text-text-primary">Positions</h1>
          <div className="flex items-center gap-2">
            <Link
              href="/dashboard/import"
              className={cn("btn-secondary min-h-[40px] inline-flex items-center", FOCUS_RING)}
            >
              Import data
            </Link>
            <Link
              href="/settings"
              aria-label="Settings"
              className={cn(
                "inline-flex items-center justify-center min-h-[40px] min-w-[40px] rounded-md text-text-muted",
                "transition-colors duration-160 hover:text-text-primary hover:bg-surface-elevated",
                FOCUS_RING
              )}
            >
              <GearIcon className="w-[18px] h-[18px]" />
            </Link>
          </div>
        </div>
      </header>

      <main className="page-main">
        {posLoading && <PageLoader />}

        {!posLoading && posError && (
          isAuthError(posError) ? (
            <EmptyState
              title="Your session has expired"
              description="Sign in again to load your positions."
              action={
                <Link href="/login" className={cn("btn-primary min-h-[40px] inline-flex items-center", FOCUS_RING)}>
                  Go to login
                </Link>
              }
            />
          ) : (
            <EmptyState
              title="Failed to load positions"
              description={posError instanceof Error ? posError.message : "Something went wrong."}
              action={
                <button
                  type="button"
                  onClick={() => refetchPositions()}
                  className={cn("btn-secondary min-h-[40px]", FOCUS_RING)}
                >
                  Retry
                </button>
              }
            />
          )
        )}

        {!posLoading && !posError && !hasPositions && (
          <EmptyState
            title="No positions yet"
            description="Import a CSV or sync your brokerage to see your holdings here."
            action={
              <Link href="/dashboard/import" className={cn("btn-primary min-h-[40px] inline-flex items-center", FOCUS_RING)}>
                Import data
              </Link>
            }
          />
        )}

        {!posLoading && !posError && hasPositions && totals && (
          <>
            {/* ── Degraded / stale-price truth banner ─────────────────────── */}
            {totals.isDegraded && (
              <div className="risk-block" role="status">
                <p className="risk-block-title">Some prices unavailable</p>
                <p className="text-xs text-text-secondary mt-1 leading-relaxed">
                  {totals.unpricedCount} holding{totals.unpricedCount !== 1 ? "s" : ""} (
                  {totals.unpricedTickers.join(", ")}) {totals.unpricedCount !== 1 ? "have" : "has"} no
                  trusted live price right now. Value, G/L and weights below cover priced holdings
                  only — nothing is estimated.
                </p>
              </div>
            )}

            {/* ── Summary band ─────────────────────────────────────────────── */}
            <section aria-label="Portfolio summary" className="space-y-3">
              <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
                <SummaryTile
                  label="Portfolio value"
                  value={totals.totalMarketValue !== null ? formatCurrency(totals.totalMarketValue) : "Unavailable"}
                  sub={totals.isDegraded && totals.totalMarketValue !== null ? "Priced holdings only" : undefined}
                />
                <SummaryTile
                  label="Total cost basis"
                  value={formatCurrency(totals.totalCostBasis)}
                />
                <SummaryTile
                  label="Unrealized G/L"
                  value={totals.totalUnrealizedPnl !== null ? formatCurrency(totals.totalUnrealizedPnl) : "Unavailable"}
                  valueClass={totals.totalUnrealizedPnl !== null ? pnlClass(totals.totalUnrealizedPnl) : undefined}
                  sub={
                    totals.totalUnrealizedPnlPct !== null
                      ? formatPercent(totals.totalUnrealizedPnlPct)
                      : totals.totalUnrealizedPnl === null
                        ? "No trusted prices"
                        : undefined
                  }
                />
                <SummaryTile
                  label="Cash"
                  value={cash !== null ? formatCurrency(cash) : "Unavailable"}
                  sub={cash === null ? "Cash balance not reported yet" : undefined}
                />
              </div>

              <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
                {/* Allocation split */}
                <div className="data-card p-4">
                  <p className="metric-label mb-2">Allocation</p>
                  {allocation.length === 0 ? (
                    <p className="text-xs text-text-muted italic">
                      Unavailable — no priced holdings to split.
                    </p>
                  ) : (
                    <ul className="space-y-1.5">
                      {allocation.map(slice => (
                        <li key={slice.key} className="flex items-baseline justify-between gap-2">
                          <span className="text-xs text-text-secondary">{slice.label}</span>
                          <span className="data-value-xs text-text-primary">
                            {slice.pct.toFixed(1)}%
                          </span>
                        </li>
                      ))}
                    </ul>
                  )}
                </div>

                {/* Top concentration */}
                <div className="data-card p-4">
                  <p className="metric-label mb-2">Top concentration</p>
                  {topConcentration ? (
                    <div className="flex items-baseline justify-between gap-2">
                      <span className="ticker-symbol text-sm">{topConcentration.ticker}</span>
                      <span className="data-value-sm">{topConcentration.weightPct.toFixed(1)}% of portfolio</span>
                    </div>
                  ) : (
                    <p className="text-xs text-text-muted italic">
                      Unavailable — no priced holdings.
                    </p>
                  )}
                </div>

                {/* Data freshness */}
                <div className="data-card p-4">
                  <p className="metric-label mb-2">Data freshness</p>
                  {freshness.hasSnapshots ? (
                    <p className="text-xs text-text-secondary">
                      Last snapshot {relativeAgeLabel(freshness.latestSnapshotAt)}
                    </p>
                  ) : (
                    <p className="text-xs text-text-muted italic">No portfolio snapshots yet.</p>
                  )}
                  {freshness.hasStalePrices ? (
                    <p className="text-xs text-caution mt-1">
                      Stale prices: {freshness.staleTickers.join(", ")}
                    </p>
                  ) : (
                    <p className="text-xs text-text-muted mt-1">Live prices loaded for all holdings.</p>
                  )}
                  {!intelLoading && !hasIntelSnapshot && (
                    <p className="text-xs text-text-muted mt-1">
                      No certified Intel snapshot exists yet.
                    </p>
                  )}
                </div>
              </div>
            </section>

            {/* ── Holdings list ────────────────────────────────────────────── */}
            <section aria-label="Holdings" className="data-card overflow-hidden">
              <div className="px-4 pt-4 pb-2 flex items-baseline justify-between">
                <h2 className="section-header">
                  Holdings · {sorted.length} position{sorted.length !== 1 ? "s" : ""}
                </h2>
              </div>
              <div className="divide-y divide-border/50">
                {sorted.map(p => (
                  <PositionRow
                    key={p.ticker}
                    position={p}
                    weightPct={weights.get(p.ticker.toUpperCase())}
                    intel={getHoldingIntel(intelMap, p.ticker, hasIntelSnapshot)}
                    expanded={expandedTicker === p.ticker}
                    onToggle={() =>
                      setExpandedTicker(prev => (prev === p.ticker ? null : p.ticker))
                    }
                  />
                ))}
              </div>
            </section>
          </>
        )}
      </main>
    </>
  );
}

// ── Summary tile ──────────────────────────────────────────────────────────────

function SummaryTile({
  label,
  value,
  sub,
  valueClass,
}: {
  label: string;
  value: string;
  sub?: string;
  valueClass?: string;
}) {
  return (
    <div className="data-card p-4">
      <p className="metric-label mb-1">{label}</p>
      <p className={cn("data-value text-lg", valueClass)}>{value}</p>
      {sub && <p className="text-xs text-text-muted mt-0.5">{sub}</p>}
    </div>
  );
}

// ── Position row ──────────────────────────────────────────────────────────────

function PositionRow({
  position: p,
  weightPct,
  intel,
  expanded,
  onToggle,
}: {
  position: Position;
  weightPct: number | undefined;
  intel: HoldingIntel;
  expanded: boolean;
  onToggle: () => void;
}) {
  const detailId = `position-detail-${p.ticker}`;
  const hasPrice = typeof p.current_price === "number";
  const costBasis = (p.shares || 0) * (p.avg_cost || 0);

  return (
    <div>
      <button
        type="button"
        onClick={onToggle}
        aria-expanded={expanded}
        aria-controls={detailId}
        className={cn(
          "w-full text-left px-4 py-3 min-h-[56px] transition-colors duration-120 hover:bg-surface-hover/40",
          FOCUS_RING,
          expanded && "bg-surface-elevated/40"
        )}
      >
        {/* Core info — no horizontal scroll: mobile shows ticker/value/G-L/action */}
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0 flex-1">
            <div className="flex flex-wrap items-center gap-1.5">
              <span className="ticker-symbol text-sm">{p.ticker}</span>
              {intel.status === "available" && intel.action ? (
                <span className={ACTION_BADGE_CLASS[intel.action] ?? "action-badge"}>
                  {intel.action}
                </span>
              ) : (
                <span className="text-[10px] text-text-muted uppercase tracking-wide">
                  {NO_CERTIFIED_INTEL_LABEL}
                </span>
              )}
            </div>
            {p.name && (
              <p className="text-xs text-text-muted truncate mt-0.5">{p.name}</p>
            )}
            {/* Desktop-only extra columns */}
            <p className="hidden sm:block text-xs text-text-muted mt-1 font-mono tabular-nums">
              {formatNumber(p.shares, 4)} sh · avg {formatCurrency(p.avg_cost)} ·{" "}
              {hasPrice ? `now ${formatCurrency(p.current_price!)}` : "no live price"}
              {weightPct !== undefined && ` · ${weightPct.toFixed(1)}% wt`}
            </p>
          </div>

          <div className="text-right shrink-0">
            {typeof p.market_value === "number" ? (
              <p className="data-value-sm">{formatCurrency(p.market_value)}</p>
            ) : (
              <p className="text-xs text-text-muted italic">No price</p>
            )}
            {typeof p.unrealised_pnl === "number" ? (
              <p className={cn("text-xs font-mono tabular-nums", pnlClass(p.unrealised_pnl))}>
                {formatCurrency(p.unrealised_pnl)}
                {typeof p.unrealised_pnl_pct === "number" && ` (${formatPercent(p.unrealised_pnl_pct)})`}
              </p>
            ) : (
              <p className="text-xs text-text-muted">G/L unavailable</p>
            )}
          </div>
        </div>
      </button>

      {/* ── Expanded detail ───────────────────────────────────────────────── */}
      <div id={detailId} hidden={!expanded}>
        {expanded && (
          <div className="px-4 pb-4 pt-1 space-y-4 bg-surface-elevated/20 border-t border-border-subtle/60">
            <dl className="grid grid-cols-2 sm:grid-cols-4 gap-x-4 gap-y-2 pt-3">
              <DetailItem label="Asset type" value={p.category || "—"} />
              <DetailItem label="Shares" value={formatNumber(p.shares, 4)} />
              <DetailItem label="Avg cost" value={formatCurrency(p.avg_cost)} />
              <DetailItem
                label="Current price"
                value={hasPrice ? formatCurrency(p.current_price!) : "Unavailable"}
              />
              <DetailItem label="Cost basis" value={formatCurrency(costBasis)} />
              <DetailItem
                label="Market value"
                value={typeof p.market_value === "number" ? formatCurrency(p.market_value) : "Unavailable"}
              />
              <DetailItem
                label="Portfolio weight"
                value={weightPct !== undefined ? `${weightPct.toFixed(1)}%` : "Unavailable"}
              />
              <DetailItem
                label="Unrealized G/L"
                value={
                  typeof p.unrealised_pnl === "number"
                    ? `${formatCurrency(p.unrealised_pnl)}${typeof p.unrealised_pnl_pct === "number" ? ` (${formatPercent(p.unrealised_pnl_pct)})` : ""}`
                    : "Unavailable"
                }
              />
            </dl>

            {/* Intel detail */}
            <div className="info-block">
              <p className="metric-label mb-1.5">Intel</p>
              {intel.status === "available" && intel.card ? (
                <div className="space-y-1">
                  <p className="text-xs text-text-secondary">
                    Action: <span className="font-semibold">{intel.action}</span> ·{" "}
                    {evidenceBandLabel(intel.evidenceBand)} · {intel.freshnessLabel}
                  </p>
                  {intel.card.why_text && (
                    <p className="text-xs text-text-muted leading-relaxed">{intel.card.why_text}</p>
                  )}
                </div>
              ) : (
                <p className="text-xs text-text-muted">
                  {intel.status === "no_snapshot"
                    ? "No certified Intel — no Intel snapshot exists yet."
                    : "No certified Intel for this holding in the latest snapshot."}
                </p>
              )}
            </div>

            <div className="flex flex-wrap items-center gap-2">
              <Link
                href={`/dashboard/position/${encodeURIComponent(p.ticker)}`}
                className={cn("btn-secondary min-h-[40px] inline-flex items-center", FOCUS_RING)}
              >
                Full history &amp; chart
              </Link>
            </div>

            {/* Tax lots — lazily loaded on open */}
            <TaxLotsSection ticker={p.ticker} />
          </div>
        )}
      </div>
    </div>
  );
}

function DetailItem({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt className="metric-label">{label}</dt>
      <dd className="data-value-xs text-text-primary mt-0.5">{value}</dd>
    </div>
  );
}

// ── Tax lots ──────────────────────────────────────────────────────────────────

const RECONCILIATION_LABELS: Record<string, string> = {
  reconciled: "Reconciled with your position",
  quantity_mismatch: "Share count does not reconcile",
  basis_mismatch: "Cost basis does not reconcile",
  blocked_unsupported_events: "Blocked by unsupported transaction events",
  blocked_share_ledger_oversold: "Blocked — transaction ledger oversold",
  no_transaction_history: "No transaction history",
};

function TaxLotsSection({ ticker }: { ticker: string }) {
  const [open, setOpen] = useState(false);
  const { data, isLoading, error, refetch } = useTaxLots(open);
  const sectionId = `tax-lots-${ticker}`;

  const holding: TaxLotHolding | null = findTaxLotHolding(data, ticker);

  return (
    <div>
      <button
        type="button"
        onClick={() => setOpen(v => !v)}
        aria-expanded={open}
        aria-controls={sectionId}
        className={cn("btn-ghost min-h-[40px] inline-flex items-center gap-1.5", FOCUS_RING)}
      >
        <span aria-hidden="true" className="text-[10px]">{open ? "▾" : "▸"}</span>
        Tax lots
      </button>

      <div id={sectionId} hidden={!open} aria-live="polite">
        {open && (
          <div className="mt-2 space-y-3">
            {isLoading && <InlineLoader text="Loading tax lots…" />}

            {!isLoading && error && (
              <div className="info-block">
                <p className="text-xs text-text-secondary">
                  Failed to load tax lots
                  {error instanceof Error ? ` — ${error.message}` : "."}
                </p>
                <button
                  type="button"
                  onClick={() => refetch()}
                  className={cn("btn-secondary min-h-[40px] mt-2", FOCUS_RING)}
                >
                  Retry
                </button>
              </div>
            )}

            {!isLoading && !error && data && !holding && (
              <p className="text-xs text-text-muted italic">
                No tax-lot data available for {ticker}.
              </p>
            )}

            {!isLoading && !error && data && holding && (
              <>
                <p className="text-xs text-text-secondary">
                  Reconciliation:{" "}
                  {RECONCILIATION_LABELS[holding.reconciliation.status] ??
                    holding.reconciliation.status.replace(/_/g, " ")}
                </p>

                {holding.authoritative && holding.lots ? (
                  holding.lots.length === 0 ? (
                    <p className="text-xs text-text-muted italic">No open lots.</p>
                  ) : (
                    <ul className="space-y-2">
                      {holding.lots.map((lot, i) => (
                        <TaxLotCard key={`${lot.acquired_date}-${i}`} lot={lot} />
                      ))}
                    </ul>
                  )
                ) : (
                  <div className="info-block">
                    <p className="text-xs text-text-secondary leading-relaxed">
                      {holding.message ??
                        "Tax lots are not shown because this holding's transactions do not reconcile with the certified position."}
                    </p>
                  </div>
                )}

                <p className="text-[11px] text-text-muted leading-relaxed">
                  {data.disclaimer} {data.jurisdiction_note}
                </p>
              </>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

function TaxLotCard({ lot }: { lot: TaxLotRow }) {
  const isLongTerm = lot.estimated_holding_classification === "long_term";
  return (
    <li className="data-card-elevated p-3">
      <dl className="grid grid-cols-2 sm:grid-cols-3 gap-x-4 gap-y-2">
        <DetailItem label="Acquired" value={lot.acquired_date} />
        <DetailItem label="Remaining shares" value={formatNumber(lot.remaining_shares, 4)} />
        <DetailItem label="Cost basis" value={formatCurrency(lot.cost_basis)} />
        <DetailItem
          label="Current value"
          value={lot.current_value !== null ? formatCurrency(lot.current_value) : "Unavailable"}
        />
        <DetailItem
          label="Unrealized G/L"
          value={
            lot.unrealized_gain !== null
              ? `${formatCurrency(lot.unrealized_gain)}${lot.unrealized_gain_pct !== null ? ` (${formatPercent(lot.unrealized_gain_pct)})` : ""}`
              : "Unavailable"
          }
        />
        <DetailItem
          label="Holding period"
          value={
            isLongTerm
              ? "Long-term (estimated)"
              : `Short-term — long-term on ${lot.estimated_long_term_start_date} (${lot.days_until_long_term}d)`
          }
        />
      </dl>
    </li>
  );
}

// ── Icons ─────────────────────────────────────────────────────────────────────

function GearIcon({ className }: { className?: string }) {
  return (
    <svg
      className={className}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.75}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d="M12.22 2h-.44a2 2 0 0 0-2 2v.18a2 2 0 0 1-1 1.73l-.43.25a2 2 0 0 1-2 0l-.15-.08a2 2 0 0 0-2.73.73l-.22.38a2 2 0 0 0 .73 2.73l.15.1a2 2 0 0 1 1 1.72v.51a2 2 0 0 1-1 1.74l-.15.09a2 2 0 0 0-.73 2.73l.22.38a2 2 0 0 0 2.73.73l.15-.08a2 2 0 0 1 2 0l.43.25a2 2 0 0 1 1 1.73V20a2 2 0 0 0 2 2h.44a2 2 0 0 0 2-2v-.18a2 2 0 0 1 1-1.73l.43-.25a2 2 0 0 1 2 0l.15.08a2 2 0 0 0 2.73-.73l.22-.39a2 2 0 0 0-.73-2.73l-.15-.08a2 2 0 0 1-1-1.74v-.5a2 2 0 0 1 1-1.74l.15-.09a2 2 0 0 0 .73-2.73l-.22-.38a2 2 0 0 0-2.73-.73l-.15.08a2 2 0 0 1-2 0l-.43-.25a2 2 0 0 1-1-1.73V4a2 2 0 0 0-2-2z" />
      <circle cx="12" cy="12" r="3" />
    </svg>
  );
}
