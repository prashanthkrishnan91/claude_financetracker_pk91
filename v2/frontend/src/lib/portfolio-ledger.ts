/**
 * Pure Portfolio Ledger helpers — Stage 4F.
 * No React, no Supabase, no external deps — safe for tests.
 *
 * Merges existing portfolio positions + Intel v3 cards into the Living Thesis
 * Ledger display format. Deterministic; never invents intelligence or scores.
 * Missing data renders Coming-Later / unavailable — not fabricated.
 */

import type { Position, IntelV3HeldCard, IntelV3Snapshot, DecisionMemoryLog } from "./api";

// ── Ledger holding ────────────────────────────────────────────────────────────

export interface LedgerHolding {
  ticker: string;
  name: string;
  category: string;
  /** Computed market value (shares × current_price) or undefined when no price. */
  marketValue: number | undefined;
  /** Portfolio weight 0–100, only set when total > 0. */
  portfolioWeight: number | undefined;
  /** Intel action from the certified snapshot, or "NO_INTEL". */
  intelAction: string;
  /** Intel conviction or undefined when no intel. */
  conviction: string | undefined;
  /** Evidence band label or undefined when no intel. */
  evidenceBand: string | undefined;
  /** Plain-English thesis state from Intel card, or undefined. */
  thesisState: string | undefined;
  /** Plain-English why/evidence text from Intel card, or undefined. */
  evidenceText: string | undefined;
  /** Risk level from Intel card, or undefined. */
  riskLevel: string | undefined;
  /** ISO timestamp of the Intel card's last update. */
  intelUpdatedAt: string | undefined;
  /** Whether Intel data is available for this holding. */
  hasIntel: boolean;
  /** True when evidence is THIN or the Intel card is absent. */
  isStaleOrThin: boolean;
  /** Long-term eligible status from the position. */
  ltEligible: boolean;
  /** Full Intel card for drawer details. */
  intelCard: IntelV3HeldCard | undefined;
}

/** All sections needed for the ledger. */
export interface LedgerData {
  holdings: LedgerHolding[];
  concentrationTop5: LedgerHolding[];
  categoryExposure: CategoryExposureRow[];
  thesisHealth: ThesisHealthSummary;
  sourceFreshness: SourceFreshnessSummary;
  hasIntelData: boolean;
  hasPositionData: boolean;
}

// ── Category exposure ─────────────────────────────────────────────────────────

export interface CategoryExposureRow {
  category: string;
  count: number;
  totalValue: number | undefined;
  pct: number | undefined;
  /** True when market value is unavailable for this category. */
  valueUnavailable: boolean;
}

// ── Thesis health ─────────────────────────────────────────────────────────────

export type ThesisHealthStatus = "strong" | "mixed" | "needs_attention" | "unavailable";

export interface ThesisHealthSummary {
  status: ThesisHealthStatus;
  statusLabel: string;
  detail: string;
  strongCount: number;
  partialCount: number;
  thinCount: number;
  highRiskCount: number;
  noIntelCount: number;
  totalCount: number;
}

// ── Source freshness ──────────────────────────────────────────────────────────

export type SourceFreshnessStatus = "fresh" | "stale" | "hard_stale" | "unavailable";

export interface SourceFreshnessSummary {
  overallStatus: SourceFreshnessStatus;
  overallLabel: string;
  detail: string;
  freshCount: number;
  staleCount: number;
  hardStaleCount: number;
  missingCount: number;
  snapshotGeneratedAt: string | undefined;
  evidenceFreshnessState: string | undefined;
}

// ── Drawer decision entry ─────────────────────────────────────────────────────

export interface DrawerDecisionEntry {
  id: string;
  date: string;
  status: string;
  source: string;
  actionCount: number;
}

export interface HoldingDrawerData {
  holding: LedgerHolding;
  lastThreeDecisions: DrawerDecisionEntry[];
  hasDecisionHistory: boolean;
  isThesisStale: boolean;
  staleWarning: string | undefined;
}

// ── Build helpers ─────────────────────────────────────────────────────────────

/** Derive market value from a position. Mirrors HoldingsList computation. */
function positionMarketValue(p: Position): number {
  const price = p.current_price ?? p.avg_cost;
  return p.shares * price;
}

/**
 * Merge positions + intel cards into LedgerHolding rows.
 * Matches by ticker (case-insensitive). Missing Intel = hasIntel false.
 */
export function buildLedgerHoldings(
  positions: Position[],
  intelCards: IntelV3HeldCard[],
): LedgerHolding[] {
  if (!positions || positions.length === 0) return [];

  const intelByTicker = new Map<string, IntelV3HeldCard>();
  for (const card of intelCards ?? []) {
    intelByTicker.set(card.ticker.toUpperCase(), card);
  }

  const rawValues = positions.map(p => positionMarketValue(p));
  const totalValue = rawValues.reduce((s, v) => s + v, 0);

  return positions.map((p, i) => {
    const card = intelByTicker.get(p.ticker.toUpperCase());
    const mv = rawValues[i];
    const weight = totalValue > 0 ? (mv / totalValue) * 100 : undefined;
    const isStaleOrThin =
      !card || card.evidence_band === "THIN" || card.evidence_band === undefined;

    return {
      ticker: p.ticker,
      name: p.name,
      category: p.category,
      marketValue: mv,
      portfolioWeight: weight,
      intelAction: card?.action ?? "NO_INTEL",
      conviction: card?.conviction,
      evidenceBand: card?.evidence_band,
      thesisState: card?.thesis_state,
      evidenceText: card?.evidence_text,
      riskLevel: card?.risk_level,
      intelUpdatedAt: card?.updated_at,
      hasIntel: !!card,
      isStaleOrThin,
      ltEligible: p.lt_eligible,
      intelCard: card,
    };
  });
}

/**
 * Top 5 holdings sorted by market value descending.
 * Positions with undefined value sort below valued ones.
 */
export function buildConcentrationTop5(holdings: LedgerHolding[]): LedgerHolding[] {
  return [...holdings]
    .sort((a, b) => {
      const av = a.marketValue ?? -1;
      const bv = b.marketValue ?? -1;
      return bv - av;
    })
    .slice(0, 5);
}

/**
 * Group holdings by category and compute value + pct.
 * Missing price data → valueUnavailable=true, no pct.
 */
export function buildCategoryExposure(holdings: LedgerHolding[]): CategoryExposureRow[] {
  const map = new Map<string, { count: number; total: number; anyUnavailable: boolean }>();

  for (const h of holdings) {
    const cat = h.category || "Other";
    const existing = map.get(cat) ?? { count: 0, total: 0, anyUnavailable: false };
    map.set(cat, {
      count: existing.count + 1,
      total: existing.total + (h.marketValue ?? 0),
      anyUnavailable: existing.anyUnavailable || h.marketValue === undefined,
    });
  }

  const total = Array.from(map.values()).reduce((s, v) => s + v.total, 0);

  return Array.from(map.entries())
    .map(([category, { count, total: catTotal, anyUnavailable }]) => ({
      category,
      count,
      totalValue: anyUnavailable ? undefined : catTotal,
      pct: anyUnavailable || total === 0 ? undefined : (catTotal / total) * 100,
      valueUnavailable: anyUnavailable,
    }))
    .sort((a, b) => (b.pct ?? -1) - (a.pct ?? -1));
}

/**
 * Deterministic thesis-health summary from existing Intel action / evidence /
 * risk fields. No LLM, no fabricated score.
 */
export function buildThesisHealthSummary(holdings: LedgerHolding[]): ThesisHealthSummary {
  let strongCount = 0;
  let partialCount = 0;
  let thinCount = 0;
  let highRiskCount = 0;
  let noIntelCount = 0;

  for (const h of holdings) {
    if (!h.hasIntel) {
      noIntelCount++;
      continue;
    }
    const band = h.evidenceBand;
    if (band === "STRONG") strongCount++;
    else if (band === "PARTIAL") partialCount++;
    else thinCount++;

    if (h.riskLevel === "HIGH") highRiskCount++;
  }

  const total = holdings.length;
  const withIntel = total - noIntelCount;

  let status: ThesisHealthStatus;
  let statusLabel: string;
  let detail: string;

  if (total === 0) {
    status = "unavailable";
    statusLabel = "No holdings";
    detail = "No positions found.";
  } else if (withIntel === 0) {
    status = "unavailable";
    statusLabel = "Intel not yet run";
    detail = "Run Intel to generate thesis status for your holdings.";
  } else if (highRiskCount > 0 || thinCount > withIntel / 2) {
    status = "needs_attention";
    statusLabel = "Needs attention";
    detail = buildHealthDetail(strongCount, partialCount, thinCount, highRiskCount, noIntelCount);
  } else if (strongCount >= withIntel / 2) {
    status = "strong";
    statusLabel = "Strong signal";
    detail = buildHealthDetail(strongCount, partialCount, thinCount, highRiskCount, noIntelCount);
  } else {
    status = "mixed";
    statusLabel = "Mixed signals";
    detail = buildHealthDetail(strongCount, partialCount, thinCount, highRiskCount, noIntelCount);
  }

  return { status, statusLabel, detail, strongCount, partialCount, thinCount, highRiskCount, noIntelCount, totalCount: total };
}

function buildHealthDetail(
  strongCount: number,
  partialCount: number,
  thinCount: number,
  highRiskCount: number,
  noIntelCount: number,
): string {
  const parts: string[] = [];
  if (strongCount > 0) parts.push(`${strongCount} strong`);
  if (partialCount > 0) parts.push(`${partialCount} partial`);
  if (thinCount > 0) parts.push(`${thinCount} thin`);
  if (highRiskCount > 0) parts.push(`${highRiskCount} high-risk`);
  if (noIntelCount > 0) parts.push(`${noIntelCount} without Intel`);
  return parts.join(" · ") || "No detail available";
}

/**
 * Source freshness summary from Intel snapshot source_freshness fields.
 * Returns unavailable state when snapshot is absent.
 */
export function buildSourceFreshnessSummary(
  intelSnapshot: IntelV3Snapshot | null | undefined,
): SourceFreshnessSummary {
  if (!intelSnapshot) {
    return {
      overallStatus: "unavailable",
      overallLabel: "No Intel snapshot",
      detail: "Run Intel to populate source freshness data.",
      freshCount: 0,
      staleCount: 0,
      hardStaleCount: 0,
      missingCount: 0,
      snapshotGeneratedAt: undefined,
      evidenceFreshnessState: undefined,
    };
  }

  const diag = intelSnapshot.diagnostics;
  const sf = diag?.source_freshness;
  const stale = diag?.stale_source_count ?? 0;
  const hardStale = diag?.hard_stale_source_count ?? 0;
  const missing = diag?.missing_source_count ?? 0;

  let freshCount = 0;
  let staleCount = 0;
  let hardStaleCount = 0;
  let missingCount = 0;

  if (sf) {
    for (const rec of Object.values(sf) as import("./api").IntelV3SourceFreshness[]) {
      if (rec.state === "FRESH") freshCount++;
      else if (rec.state === "STALE") staleCount++;
      else if (rec.state === "HARD_STALE") hardStaleCount++;
      else if (rec.state === "MISSING") missingCount++;
    }
  } else {
    staleCount = stale;
    hardStaleCount = hardStale;
    missingCount = missing;
  }

  const totalNonFresh = staleCount + hardStaleCount + missingCount;
  let overallStatus: SourceFreshnessStatus;
  let overallLabel: string;
  let detail: string;

  if (hardStaleCount > 0 || missingCount > 1) {
    overallStatus = "hard_stale";
    overallLabel = "Sources need refresh";
    detail = `${hardStaleCount} critical · ${staleCount} stale · ${missingCount} missing`;
  } else if (staleCount > 0 || missingCount > 0) {
    overallStatus = "stale";
    overallLabel = "Some sources stale";
    detail = `${staleCount} stale · ${missingCount} missing — run Intel to refresh`;
  } else if (freshCount > 0 || totalNonFresh === 0) {
    overallStatus = "fresh";
    overallLabel = "Sources current";
    detail = freshCount > 0 ? `${freshCount} source${freshCount !== 1 ? "s" : ""} verified current` : "Source freshness verified";
  } else {
    overallStatus = "unavailable";
    overallLabel = "Freshness unavailable";
    detail = "Freshness data not available in this snapshot.";
  }

  return {
    overallStatus,
    overallLabel,
    detail,
    freshCount,
    staleCount,
    hardStaleCount,
    missingCount,
    snapshotGeneratedAt: intelSnapshot.generated_at,
    evidenceFreshnessState: intelSnapshot.evidence_freshness_state,
  };
}

/**
 * Build holding drawer data from a holding + decision logs.
 * Last 3 decision logs, stale-thesis warning if applicable.
 */
export function buildHoldingDrawerData(
  holding: LedgerHolding,
  decisionLogs: DecisionMemoryLog[],
): HoldingDrawerData {
  const logsForHolding = (decisionLogs ?? [])
    .filter(log =>
      (log.actual_decisions ?? []).some(
        d => d.ticker?.toUpperCase() === holding.ticker.toUpperCase()
      )
    )
    .slice(0, 3)
    .map(log => ({
      id: log.id,
      date: log.created_at ?? "",
      status: log.status ?? "DRAFT",
      source: log.source ?? "unknown",
      actionCount: (log.actual_decisions ?? []).length,
    }));

  const isThesisStale = holding.isStaleOrThin && holding.hasIntel;
  const staleWarning = isThesisStale
    ? "Evidence is thin — thesis confidence is lower than holdings with strong signals."
    : undefined;

  return {
    holding,
    lastThreeDecisions: logsForHolding,
    hasDecisionHistory: logsForHolding.length > 0,
    isThesisStale,
    staleWarning,
  };
}

/** Build the full ledger data object. */
export function buildLedgerData(
  positions: Position[],
  intelSnapshot: IntelV3Snapshot | null | undefined,
  decisionLogs: DecisionMemoryLog[],
): LedgerData {
  const allCards = [
    ...(intelSnapshot?.best_buys ?? []),
    ...(intelSnapshot?.trim_sell_desk ?? []),
    ...(intelSnapshot?.current_holdings ?? []),
  ];

  // Deduplicate by ticker (snapshot may include a card in multiple sections)
  const uniqueCards = new Map<string, IntelV3HeldCard>();
  for (const card of allCards) {
    uniqueCards.set(card.ticker.toUpperCase(), card);
  }

  const holdings = buildLedgerHoldings(positions, Array.from(uniqueCards.values()));
  const concentrationTop5 = buildConcentrationTop5(holdings);
  const categoryExposure = buildCategoryExposure(holdings);
  const thesisHealth = buildThesisHealthSummary(holdings);
  const sourceFreshness = buildSourceFreshnessSummary(intelSnapshot);

  return {
    holdings,
    concentrationTop5,
    categoryExposure,
    thesisHealth,
    sourceFreshness,
    hasIntelData: !!intelSnapshot,
    hasPositionData: holdings.length > 0,
  };
}

// ── Plain-English formatters ──────────────────────────────────────────────────

/** Intel action → plain-English chip label (never shows raw keys). */
export function actionToLabel(action: string): string {
  switch (action?.toUpperCase()) {
    case "BUY":  return "Buy";
    case "HOLD": return "Hold";
    case "TRIM": return "Trim";
    case "SELL": return "Sell";
    case "NO_INTEL": return "—";
    default: return "—";
  }
}

/** Evidence band → plain-English freshness cue. */
export function evidenceBandToFreshnessCue(band: string | undefined): string {
  switch (band) {
    case "STRONG":  return "Strong evidence";
    case "PARTIAL": return "Partial evidence";
    case "THIN":    return "Thin evidence";
    default:        return "No evidence";
  }
}

/** Source freshness state → plain-English label. */
export function sourceFreshnessStateToLabel(state: string | undefined): string {
  switch (state) {
    case "certified_current":     return "Current";
    case "rebuilt_and_published": return "Refreshed";
    case "republish_pending":     return "Refresh pending";
    case "certification_blocked": return "Blocked";
    case "no_snapshot_exists":    return "None yet";
    default:                      return "—";
  }
}

/** Format a relative age label from an ISO timestamp. Returns "—" when absent. */
export function formatRelativeAge(iso: string | undefined): string {
  if (!iso) return "—";
  try {
    const diffMs = Date.now() - new Date(iso).getTime();
    if (Number.isNaN(diffMs)) return "—";
    const h = Math.floor(diffMs / 3_600_000);
    if (h < 1) return "< 1h ago";
    if (h < 24) return `${h}h ago`;
    const d = Math.floor(h / 24);
    return `${d}d ago`;
  } catch {
    return "—";
  }
}

/** Action → CSS token classes for color. Mirrors IntelV3PrimitivesData. */
export function actionChipStyle(action: string): string {
  switch (action?.toUpperCase()) {
    case "BUY":  return "bg-action-buy/10 text-action-buy border-action-buy/20";
    case "TRIM": return "bg-action-trim/10 text-action-trim border-action-trim/20";
    case "SELL": return "bg-action-sell/10 text-action-sell border-action-sell/20";
    case "HOLD": return "bg-action-hold/10 text-action-hold border-action-hold/20";
    default:     return "bg-surface-elevated text-text-muted border-border";
  }
}
