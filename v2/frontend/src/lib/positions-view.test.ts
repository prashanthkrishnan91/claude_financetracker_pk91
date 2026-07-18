/**
 * Positions view helper tests.
 * Covers: totals math, missing-price degradation, weights/concentration,
 * allocation split, freshness derivation, intel mapping (available / no card /
 * no snapshot), empty positions, auth-error classification.
 */

import {
  computeTotals,
  cashFromSummary,
  categoryToAllocationKey,
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
} from "./positions-view";
import type { Position, PortfolioSummary, Snapshot, IntelV3Snapshot, IntelV3HeldCard } from "./api";

// ── Fixtures ──────────────────────────────────────────────────────────────────

function makePosition(overrides: Partial<Position> = {}): Position {
  return {
    id: "pos-1",
    ticker: "AAPL",
    name: "Apple Inc.",
    category: "Core",
    shares: 10,
    avg_cost: 100,
    current_price: 150,
    market_value: 1500,
    unrealised_pnl: 500,
    unrealised_pnl_pct: 50,
    lt_eligible: false,
    source: "manual",
    ...overrides,
  } as Position;
}

function makeIntelCard(overrides: Partial<IntelV3HeldCard> = {}): IntelV3HeldCard {
  return {
    ticker: "AAPL",
    name: "Apple Inc.",
    asset_type: "equity",
    action: "HOLD",
    conviction: "MEDIUM",
    evidence_band: "PARTIAL",
    portfolio_fit: "core",
    risk_level: "LOW",
    thesis_state: "intact",
    why_text: "why",
    risk_text: "risk",
    action_text: "action",
    what_would_change_view: "",
    fit_text: "",
    evidence_text: "",
    flags: [],
    source_snapshot_id: "snap-1",
    source_run_id: "run-1",
    updated_at: "2026-07-18T00:00:00Z",
    detail_drawer_payload: {} as IntelV3HeldCard["detail_drawer_payload"],
    ...overrides,
  } as IntelV3HeldCard;
}

function makeSnapshot(cards: IntelV3HeldCard[]): IntelV3Snapshot {
  return {
    schema_version: "v3",
    snapshot_id: "snap-1",
    run_id: "run-1",
    generated_at: "2026-07-18T00:00:00Z",
    is_stale: false,
    source_health: { status: "ok" },
    portfolio_command_center: {
      total_holdings: cards.length,
      buy_count: 0, hold_count: 0, trim_count: 0, sell_count: 0,
      high_conviction: 0, thin_evidence: 0,
      source_health: { status: "ok" },
    },
    action_counts: { BUY: 0, HOLD: 0, TRIM: 0, SELL: 0 },
    evidence_band_counts: { THIN: 0, PARTIAL: 0, STRONG: 0 },
    conviction_counts: { LOW: 0, MEDIUM: 0, HIGH: 0 },
    best_buys: [],
    trim_sell_desk: [],
    current_holdings: cards,
    opportunity_radar_preview: { status: "deferred" },
    what_changed: [],
    warnings: [],
    legacy_path_used: false,
  } as IntelV3Snapshot;
}

// ── Totals ────────────────────────────────────────────────────────────────────

describe("computeTotals", () => {
  it("sums market value, cost basis and P&L across fully priced positions", () => {
    const totals = computeTotals([
      makePosition({ ticker: "AAPL", shares: 10, avg_cost: 100, market_value: 1500 }),
      makePosition({ ticker: "VTI", shares: 5, avg_cost: 200, market_value: 900, category: "ETF" }),
    ]);
    expect(totals.totalMarketValue).toBe(2400);
    expect(totals.totalCostBasis).toBe(2000);
    expect(totals.pricedCostBasis).toBe(2000);
    expect(totals.totalUnrealizedPnl).toBe(400);
    expect(totals.totalUnrealizedPnlPct).toBeCloseTo(20);
    expect(totals.isDegraded).toBe(false);
    expect(totals.unpricedTickers).toEqual([]);
  });

  it("derives market value from shares × current_price when market_value is absent", () => {
    const totals = computeTotals([
      makePosition({ market_value: undefined, current_price: 120, shares: 10, avg_cost: 100 }),
    ]);
    expect(totals.totalMarketValue).toBe(1200);
    expect(totals.totalUnrealizedPnl).toBe(200);
  });

  it("degrades honestly when some positions have no price", () => {
    const totals = computeTotals([
      makePosition({ ticker: "AAPL", market_value: 1500, shares: 10, avg_cost: 100 }),
      makePosition({
        ticker: "MYST",
        market_value: undefined,
        current_price: undefined,
        shares: 4,
        avg_cost: 50,
      }),
    ]);
    expect(totals.isDegraded).toBe(true);
    expect(totals.unpricedCount).toBe(1);
    expect(totals.unpricedTickers).toEqual(["MYST"]);
    // Total market value covers only the priced subset.
    expect(totals.totalMarketValue).toBe(1500);
    // Cost basis still covers everything.
    expect(totals.totalCostBasis).toBe(1200);
    // P&L% uses the priced cost basis only — never mixes the unpriced lot in.
    expect(totals.pricedCostBasis).toBe(1000);
    expect(totals.totalUnrealizedPnlPct).toBeCloseTo(50);
  });

  it("returns null totals when nothing is priced", () => {
    const totals = computeTotals([
      makePosition({ market_value: undefined, current_price: undefined }),
    ]);
    expect(totals.totalMarketValue).toBeNull();
    expect(totals.totalUnrealizedPnl).toBeNull();
    expect(totals.totalUnrealizedPnlPct).toBeNull();
    expect(totals.isDegraded).toBe(true);
  });

  it("handles empty positions", () => {
    const totals = computeTotals([]);
    expect(totals.totalMarketValue).toBeNull();
    expect(totals.totalCostBasis).toBe(0);
    expect(totals.pricedCount).toBe(0);
    expect(totals.isDegraded).toBe(false);
  });
});

describe("cashFromSummary", () => {
  it("returns cash when the summary exists", () => {
    expect(cashFromSummary({ cash_balance: 1234.5 } as PortfolioSummary)).toBe(1234.5);
  });
  it("returns null when the summary is absent", () => {
    expect(cashFromSummary(null)).toBeNull();
    expect(cashFromSummary(undefined)).toBeNull();
  });
});

// ── Allocation ────────────────────────────────────────────────────────────────

describe("allocation split", () => {
  it("maps categories to buckets like the backend summary", () => {
    expect(categoryToAllocationKey("Core")).toBe("equities");
    expect(categoryToAllocationKey("Other")).toBe("equities");
    expect(categoryToAllocationKey("IPO")).toBe("equities");
    expect(categoryToAllocationKey("SELL")).toBe("equities");
    expect(categoryToAllocationKey("ETF")).toBe("etf");
    expect(categoryToAllocationKey("Crypto")).toBe("crypto");
    expect(categoryToAllocationKey(undefined)).toBe("equities");
  });

  it("computes percentages over priced positions only", () => {
    const slices = computeAllocationSplit([
      makePosition({ ticker: "AAPL", category: "Core", market_value: 600 }),
      makePosition({ ticker: "VTI", category: "ETF", market_value: 300 }),
      makePosition({ ticker: "BTC-USD", category: "Crypto", market_value: 100 }),
      makePosition({ ticker: "MYST", category: "Core", market_value: undefined, current_price: undefined }),
    ]);
    expect(slices.map(s => s.key)).toEqual(["equities", "etf", "crypto"]);
    expect(slices[0].pct).toBeCloseTo(60);
    expect(slices[1].pct).toBeCloseTo(30);
    expect(slices[2].pct).toBeCloseTo(10);
  });

  it("returns empty when nothing is priced (never invents an allocation)", () => {
    expect(
      computeAllocationSplit([
        makePosition({ market_value: undefined, current_price: undefined }),
      ])
    ).toEqual([]);
    expect(computeAllocationSplit([])).toEqual([]);
  });
});

// ── Weights & concentration ───────────────────────────────────────────────────

describe("weights and concentration", () => {
  it("computes weights over priced positions and omits unpriced tickers", () => {
    const weights = computeWeights([
      makePosition({ ticker: "AAPL", market_value: 750 }),
      makePosition({ ticker: "VTI", market_value: 250 }),
      makePosition({ ticker: "MYST", market_value: undefined, current_price: undefined }),
    ]);
    expect(weights.get("AAPL")).toBeCloseTo(75);
    expect(weights.get("VTI")).toBeCloseTo(25);
    expect(weights.has("MYST")).toBe(false);
  });

  it("finds the top concentration", () => {
    const top = computeTopConcentration([
      makePosition({ ticker: "AAPL", market_value: 750 }),
      makePosition({ ticker: "VTI", market_value: 250 }),
    ]);
    expect(top).toEqual({ ticker: "AAPL", weightPct: 75 });
  });

  it("returns null concentration when nothing is priced", () => {
    expect(
      computeTopConcentration([
        makePosition({ market_value: undefined, current_price: undefined }),
      ])
    ).toBeNull();
    expect(computeTopConcentration([])).toBeNull();
  });
});

// ── Freshness ─────────────────────────────────────────────────────────────────

describe("deriveFreshness", () => {
  const snapshots = [
    { snapshot_at: "2026-07-16T00:00:00Z" },
    { snapshot_at: "2026-07-18T00:00:00Z" },
    { snapshot_at: "2026-07-17T00:00:00Z" },
  ] as Snapshot[];

  it("finds the newest snapshot time regardless of order", () => {
    const info = deriveFreshness(snapshots, []);
    expect(info.latestSnapshotAt).toBe("2026-07-18T00:00:00Z");
    expect(info.hasSnapshots).toBe(true);
  });

  it("reports no snapshots when the list is empty or absent", () => {
    expect(deriveFreshness([], []).hasSnapshots).toBe(false);
    expect(deriveFreshness(undefined, undefined).latestSnapshotAt).toBeNull();
  });

  it("flags stale-price tickers", () => {
    const info = deriveFreshness(snapshots, [
      makePosition({ ticker: "AAPL", market_value: 100 }),
      makePosition({ ticker: "MYST", market_value: undefined, current_price: undefined }),
    ]);
    expect(info.hasStalePrices).toBe(true);
    expect(info.staleTickers).toEqual(["MYST"]);
  });
});

describe("relativeAgeLabel", () => {
  const now = new Date("2026-07-18T12:00:00Z").getTime();
  it("formats minutes, hours and days", () => {
    expect(relativeAgeLabel("2026-07-18T11:59:40Z", now)).toBe("just now");
    expect(relativeAgeLabel("2026-07-18T11:30:00Z", now)).toBe("30m ago");
    expect(relativeAgeLabel("2026-07-18T09:00:00Z", now)).toBe("3h ago");
    expect(relativeAgeLabel("2026-07-15T12:00:00Z", now)).toBe("3d ago");
  });
  it("returns a dash for missing or invalid input", () => {
    expect(relativeAgeLabel(undefined, now)).toBe("—");
    expect(relativeAgeLabel(null, now)).toBe("—");
    expect(relativeAgeLabel("not-a-date", now)).toBe("—");
  });
});

// ── Intel mapping ─────────────────────────────────────────────────────────────

describe("intel mapping", () => {
  const now = new Date("2026-07-18T03:00:00Z").getTime();

  it("maps current_holdings cards by ticker (case-insensitive)", () => {
    const snapshot = makeSnapshot([makeIntelCard({ ticker: "AAPL", action: "BUY", evidence_band: "STRONG" })]);
    const map = buildIntelCardMap(snapshot);
    const intel = getHoldingIntel(map, "aapl", true, now);
    expect(intel.status).toBe("available");
    expect(intel.action).toBe("BUY");
    expect(intel.evidenceBand).toBe("STRONG");
    expect(intel.freshnessLabel).toBe("Updated 3h ago");
    expect(intel.card?.ticker).toBe("AAPL");
  });

  it("prefers current_holdings over other sections for the same ticker", () => {
    const snapshot = makeSnapshot([makeIntelCard({ ticker: "AAPL", action: "HOLD" })]);
    snapshot.best_buys = [makeIntelCard({ ticker: "AAPL", action: "BUY" })];
    const map = buildIntelCardMap(snapshot);
    expect(map.get("AAPL")?.action).toBe("HOLD");
  });

  it("falls back to other sections when current_holdings misses a ticker", () => {
    const snapshot = makeSnapshot([]);
    snapshot.trim_sell_desk = [makeIntelCard({ ticker: "TSLA", action: "TRIM" })];
    const map = buildIntelCardMap(snapshot);
    expect(getHoldingIntel(map, "TSLA", true, now).action).toBe("TRIM");
  });

  it("returns no_card with the No certified Intel label for uncovered held tickers", () => {
    const map = buildIntelCardMap(makeSnapshot([makeIntelCard({ ticker: "AAPL" })]));
    const intel = getHoldingIntel(map, "MSFT", true, now);
    expect(intel.status).toBe("no_card");
    expect(intel.action).toBeNull();
    expect(intel.freshnessLabel).toBe(NO_CERTIFIED_INTEL_LABEL);
  });

  it("returns no_snapshot when no snapshot exists", () => {
    const map = buildIntelCardMap(null);
    const intel = getHoldingIntel(map, "AAPL", false, now);
    expect(intel.status).toBe("no_snapshot");
    expect(intel.card).toBeNull();
    expect(intel.freshnessLabel).toBe(NO_CERTIFIED_INTEL_LABEL);
  });

  it("labels evidence bands in plain English", () => {
    expect(evidenceBandLabel("STRONG")).toBe("Strong evidence");
    expect(evidenceBandLabel("PARTIAL")).toBe("Partial evidence");
    expect(evidenceBandLabel("THIN")).toBe("Thin evidence");
    expect(evidenceBandLabel(null)).toBe("No evidence");
  });
});

// ── Error classification ──────────────────────────────────────────────────────

describe("isAuthError", () => {
  it("recognizes auth-flavored errors", () => {
    expect(isAuthError(new Error("API error: 401"))).toBe(true);
    expect(isAuthError(new Error("Not authenticated"))).toBe(true);
    expect(isAuthError(new Error("Could not validate credentials"))).toBe(true);
  });
  it("rejects other errors and non-errors", () => {
    expect(isAuthError(new Error("API error: 500"))).toBe(false);
    expect(isAuthError("401")).toBe(false);
    expect(isAuthError(null)).toBe(false);
  });
});
