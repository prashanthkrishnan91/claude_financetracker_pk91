/**
 * Pure helpers for the primary Positions view.
 * No React, no Supabase, no network — deterministic and safe for node tests.
 *
 * Data-truth rules:
 * - Missing prices are reported missing, never fabricated.
 * - Totals over partially priced portfolios are explicitly flagged degraded.
 * - Intel states are explicit: available / no card / no snapshot.
 */

import type {
  Position,
  PortfolioSummary,
  Snapshot,
  IntelV3Snapshot,
  IntelV3HeldCard,
  IntelV3Action,
  IntelV3EvidenceBand,
} from "./api";

// ── Totals ────────────────────────────────────────────────────────────────────

export interface PositionsTotals {
  /** Sum of market values across priced positions. Null when nothing is priced. */
  totalMarketValue: number | null;
  /** Sum of shares × avg_cost across ALL positions (does not need live prices). */
  totalCostBasis: number;
  /** Cost basis of only the priced subset — the honest denominator for P&L %. */
  pricedCostBasis: number;
  /** Unrealized G/L in dollars over the priced subset. Null when nothing is priced. */
  totalUnrealizedPnl: number | null;
  /** Unrealized G/L percent over the priced subset. Null when not computable. */
  totalUnrealizedPnlPct: number | null;
  pricedCount: number;
  unpricedCount: number;
  unpricedTickers: string[];
  /** True when at least one position is missing a trusted current price. */
  isDegraded: boolean;
}

function positionMarketValue(p: Position): number | null {
  if (typeof p.market_value === "number" && isFinite(p.market_value)) {
    return p.market_value;
  }
  if (
    typeof p.current_price === "number" &&
    isFinite(p.current_price) &&
    p.current_price > 0 &&
    p.shares > 0
  ) {
    return p.shares * p.current_price;
  }
  return null;
}

export function computeTotals(positions: Position[]): PositionsTotals {
  let totalMarketValue = 0;
  let totalCostBasis = 0;
  let pricedCostBasis = 0;
  let pricedCount = 0;
  const unpricedTickers: string[] = [];

  for (const p of positions) {
    const costBasis = (p.shares || 0) * (p.avg_cost || 0);
    totalCostBasis += costBasis;
    const mv = positionMarketValue(p);
    if (mv === null) {
      unpricedTickers.push(p.ticker);
    } else {
      totalMarketValue += mv;
      pricedCostBasis += costBasis;
      pricedCount++;
    }
  }

  const hasPriced = pricedCount > 0;
  const pnl = hasPriced ? totalMarketValue - pricedCostBasis : null;
  const pnlPct =
    pnl !== null && pricedCostBasis > 0 ? (pnl / pricedCostBasis) * 100 : null;

  return {
    totalMarketValue: hasPriced ? totalMarketValue : null,
    totalCostBasis,
    pricedCostBasis,
    totalUnrealizedPnl: pnl,
    totalUnrealizedPnlPct: pnlPct,
    pricedCount,
    unpricedCount: unpricedTickers.length,
    unpricedTickers,
    isDegraded: unpricedTickers.length > 0,
  };
}

/** Cash balance from the portfolio summary, or null when the summary is absent. */
export function cashFromSummary(
  summary: PortfolioSummary | null | undefined
): number | null {
  if (!summary || typeof summary.cash_balance !== "number") return null;
  return summary.cash_balance;
}

// ── Allocation split ──────────────────────────────────────────────────────────

export type AllocationKey = "equities" | "etf" | "crypto";

export interface AllocationSlice {
  key: AllocationKey;
  label: string;
  value: number;
  pct: number;
}

/**
 * Category → bucket, mirroring the backend summary mapping:
 * Core/Other/IPO/SELL (and unknowns) → equities, ETF → etf, Crypto → crypto.
 */
export function categoryToAllocationKey(category: string | undefined): AllocationKey {
  const cat = (category || "").trim().toUpperCase();
  if (cat === "ETF" || cat === "ETFS") return "etf";
  if (cat === "CRYPTO") return "crypto";
  return "equities";
}

const ALLOCATION_LABELS: Record<AllocationKey, string> = {
  equities: "Equities",
  etf: "ETFs",
  crypto: "Crypto",
};

/**
 * Allocation split over priced positions only. Returns [] when no position
 * has a trusted market value (never invents an allocation).
 * Slices with zero value are omitted.
 */
export function computeAllocationSplit(positions: Position[]): AllocationSlice[] {
  const buckets: Record<AllocationKey, number> = { equities: 0, etf: 0, crypto: 0 };
  let total = 0;
  for (const p of positions) {
    const mv = positionMarketValue(p);
    if (mv === null) continue;
    buckets[categoryToAllocationKey(p.category)] += mv;
    total += mv;
  }
  if (total <= 0) return [];
  return (Object.keys(buckets) as AllocationKey[])
    .filter(k => buckets[k] > 0)
    .map(k => ({
      key: k,
      label: ALLOCATION_LABELS[k],
      value: buckets[k],
      pct: (buckets[k] / total) * 100,
    }))
    .sort((a, b) => b.value - a.value);
}

// ── Weights & concentration ───────────────────────────────────────────────────

/**
 * Portfolio weight (0–100) per ticker, computed only over priced positions.
 * Unpriced tickers are absent from the map — weight unknown, not zero.
 */
export function computeWeights(positions: Position[]): Map<string, number> {
  const weights = new Map<string, number>();
  let total = 0;
  const values: Array<[string, number]> = [];
  for (const p of positions) {
    const mv = positionMarketValue(p);
    if (mv === null) continue;
    values.push([p.ticker.toUpperCase(), mv]);
    total += mv;
  }
  if (total <= 0) return weights;
  for (const [ticker, mv] of values) {
    weights.set(ticker, (mv / total) * 100);
  }
  return weights;
}

export interface TopConcentration {
  ticker: string;
  weightPct: number;
}

/** Largest position weight, or null when no priced positions exist. */
export function computeTopConcentration(positions: Position[]): TopConcentration | null {
  const weights = computeWeights(positions);
  let top: TopConcentration | null = null;
  for (const [ticker, weightPct] of Array.from(weights.entries())) {
    if (!top || weightPct > top.weightPct) top = { ticker, weightPct };
  }
  return top;
}

// ── Freshness / degraded state ────────────────────────────────────────────────

export interface FreshnessInfo {
  /** ISO timestamp of the newest portfolio snapshot, or null when none exist. */
  latestSnapshotAt: string | null;
  hasSnapshots: boolean;
  /** Tickers whose live price is missing right now. */
  staleTickers: string[];
  hasStalePrices: boolean;
}

export function deriveFreshness(
  snapshots: Snapshot[] | null | undefined,
  positions: Position[] | null | undefined
): FreshnessInfo {
  let latest: string | null = null;
  for (const s of snapshots ?? []) {
    if (!s.snapshot_at) continue;
    if (latest === null || new Date(s.snapshot_at).getTime() > new Date(latest).getTime()) {
      latest = s.snapshot_at;
    }
  }
  const staleTickers = (positions ?? [])
    .filter(p => positionMarketValue(p) === null)
    .map(p => p.ticker);
  return {
    latestSnapshotAt: latest,
    hasSnapshots: (snapshots?.length ?? 0) > 0,
    staleTickers,
    hasStalePrices: staleTickers.length > 0,
  };
}

/** Relative age label with injectable clock for deterministic tests. */
export function relativeAgeLabel(
  iso: string | null | undefined,
  nowMs: number = Date.now()
): string {
  if (!iso) return "—";
  const t = new Date(iso).getTime();
  if (Number.isNaN(t)) return "—";
  const diffMs = nowMs - t;
  if (diffMs < 0) return "just now";
  const minutes = Math.floor(diffMs / 60_000);
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  return `${days}d ago`;
}

// ── Intel v3 mapping ──────────────────────────────────────────────────────────

export const NO_CERTIFIED_INTEL_LABEL = "No certified Intel";

export type HoldingIntelStatus = "available" | "no_card" | "no_snapshot";

export interface HoldingIntel {
  status: HoldingIntelStatus;
  action: IntelV3Action | null;
  evidenceBand: IntelV3EvidenceBand | null;
  /** e.g. "Updated 3h ago" when available; NO_CERTIFIED_INTEL_LABEL otherwise. */
  freshnessLabel: string;
  card: IntelV3HeldCard | null;
}

/**
 * Map Intel v3 held cards by uppercase ticker. current_holdings is the
 * canonical section; best_buys / trim_sell_desk fill gaps defensively but
 * never override a current_holdings card.
 */
export function buildIntelCardMap(
  snapshot: IntelV3Snapshot | null | undefined
): Map<string, IntelV3HeldCard> {
  const map = new Map<string, IntelV3HeldCard>();
  if (!snapshot) return map;
  for (const card of [
    ...(snapshot.best_buys ?? []),
    ...(snapshot.trim_sell_desk ?? []),
    ...(snapshot.current_holdings ?? []),
  ]) {
    map.set(card.ticker.toUpperCase(), card);
  }
  return map;
}

export function getHoldingIntel(
  cardMap: Map<string, IntelV3HeldCard>,
  ticker: string,
  hasSnapshot: boolean,
  nowMs: number = Date.now()
): HoldingIntel {
  if (!hasSnapshot) {
    return {
      status: "no_snapshot",
      action: null,
      evidenceBand: null,
      freshnessLabel: NO_CERTIFIED_INTEL_LABEL,
      card: null,
    };
  }
  const card = cardMap.get(ticker.toUpperCase());
  if (!card) {
    return {
      status: "no_card",
      action: null,
      evidenceBand: null,
      freshnessLabel: NO_CERTIFIED_INTEL_LABEL,
      card: null,
    };
  }
  return {
    status: "available",
    action: card.action,
    evidenceBand: card.evidence_band,
    freshnessLabel: `Updated ${relativeAgeLabel(card.updated_at, nowMs)}`,
    card,
  };
}

/** Evidence band → plain-English label (never raw enum). */
export function evidenceBandLabel(band: IntelV3EvidenceBand | null): string {
  switch (band) {
    case "STRONG":  return "Strong evidence";
    case "PARTIAL": return "Partial evidence";
    case "THIN":    return "Thin evidence";
    default:        return "No evidence";
  }
}

// ── Error classification ──────────────────────────────────────────────────────

/**
 * True when a query error looks like an auth failure (401 / expired session).
 * fetchApi throws Error(detail || "API error: <status>"), so we match on the
 * message text.
 */
export function isAuthError(error: unknown): boolean {
  if (!(error instanceof Error)) return false;
  return /401|unauthorized|not authenticated|invalid token|credential/i.test(
    error.message
  );
}
