/**
 * Portfolio Ledger helper tests — Stage 4F.
 * Covers: empty holdings, top-5 ordering, category exposure, missing theme data,
 * Intel action/evidence mapping, drawer stale-warnings, coming-later capsules.
 */

import {
  buildLedgerHoldings,
  buildConcentrationTop5,
  buildCategoryExposure,
  buildThesisHealthSummary,
  buildSourceFreshnessSummary,
  buildHoldingDrawerData,
  buildLedgerData,
  actionToLabel,
  evidenceBandToFreshnessCue,
  actionChipStyle,
  formatRelativeAge,
} from "./portfolio-ledger";
import type { Position, IntelV3HeldCard, IntelV3Snapshot } from "./api";

// ── Test fixtures ─────────────────────────────────────────────────────────────

function makePosition(overrides: Partial<Position> = {}): Position {
  return {
    id: "pos-1",
    ticker: "AAPL",
    name: "Apple Inc.",
    category: "Core",
    shares: 10,
    avg_cost: 150,
    current_price: 170,
    lt_eligible: false,
    source: "manual",
    ...overrides,
  } as Position;
}

function makeIntelCard(overrides: Partial<IntelV3HeldCard> = {}): IntelV3HeldCard {
  return {
    ticker: "AAPL",
    name: "Apple Inc.",
    asset_type: "stock",
    action: "HOLD",
    conviction: "MEDIUM",
    evidence_band: "STRONG",
    portfolio_fit: "core",
    risk_level: "LOW",
    thesis_state: "On track",
    why_text: "Strong fundamentals.",
    risk_text: "Low near-term risk.",
    action_text: "Hold and monitor.",
    what_would_change_view: "If margins compress.",
    fit_text: "Fits as core holding.",
    evidence_text: "Strong analyst coverage.",
    flags: [],
    source_snapshot_id: "snap-001",
    source_run_id: "run-001",
    updated_at: new Date(Date.now() - 3_600_000).toISOString(),
    detail_drawer_payload: {
      rationale: "Strong fundamentals.",
      why_now: "Recent earnings beat.",
      why_not_now: "Premium valuation.",
      evidence_band: "STRONG",
      evidence_quality: "High",
      attractiveness: "Moderate",
      price_context: "",
      portfolio_fit_raw: "core",
      risk_band: "LOW",
      blockers: [],
    },
    ...overrides,
  } as IntelV3HeldCard;
}

// ── buildLedgerHoldings ───────────────────────────────────────────────────────

describe("buildLedgerHoldings", () => {
  it("returns empty array for empty positions", () => {
    expect(buildLedgerHoldings([], [])).toEqual([]);
  });

  it("returns holdings with hasIntel=false when no intel cards present", () => {
    const result = buildLedgerHoldings([makePosition()], []);
    expect(result).toHaveLength(1);
    expect(result[0].hasIntel).toBe(false);
    expect(result[0].intelAction).toBe("NO_INTEL");
  });

  it("merges intel card when ticker matches", () => {
    const result = buildLedgerHoldings([makePosition()], [makeIntelCard()]);
    expect(result[0].hasIntel).toBe(true);
    expect(result[0].intelAction).toBe("HOLD");
    expect(result[0].evidenceBand).toBe("STRONG");
  });

  it("is case-insensitive for ticker matching", () => {
    const pos = makePosition({ ticker: "aapl" });
    const card = makeIntelCard({ ticker: "AAPL" });
    const result = buildLedgerHoldings([pos], [card]);
    expect(result[0].hasIntel).toBe(true);
  });

  it("prefers backend market_value when present and positive", () => {
    const pos = makePosition({ shares: 10, current_price: 170, avg_cost: 150, market_value: 1800 });
    const result = buildLedgerHoldings([pos], []);
    expect(result[0].marketValue).toBe(1800);
  });

  it("uses shares × current_price when market_value absent", () => {
    const pos = makePosition({ shares: 10, current_price: 170, avg_cost: 150, market_value: undefined });
    const result = buildLedgerHoldings([pos], []);
    expect(result[0].marketValue).toBe(1700);
  });

  it("returns undefined marketValue when current_price absent and no market_value — no avg_cost fallback", () => {
    const pos = makePosition({ shares: 10, current_price: undefined, avg_cost: 150, market_value: undefined });
    const result = buildLedgerHoldings([pos], []);
    expect(result[0].marketValue).toBeUndefined();
  });

  it("returns undefined marketValue when current_price is 0 and no market_value", () => {
    const pos = makePosition({ shares: 10, current_price: 0, avg_cost: 150, market_value: undefined });
    const result = buildLedgerHoldings([pos], []);
    expect(result[0].marketValue).toBeUndefined();
  });

  it("does not use avg_cost as a current value proxy", () => {
    // avg_cost is cost basis, not current value — must never appear as marketValue
    const pos = makePosition({ shares: 5, current_price: undefined, avg_cost: 200, market_value: undefined });
    const result = buildLedgerHoldings([pos], []);
    expect(result[0].marketValue).toBeUndefined();
    // Specifically must not equal shares × avg_cost
    expect(result[0].marketValue).not.toBe(1000);
  });

  it("computes portfolio weight as pct of total from real current values only", () => {
    const p1 = makePosition({ ticker: "AAPL", shares: 10, current_price: 100, market_value: undefined });
    const p2 = makePosition({ ticker: "MSFT", shares: 10, current_price: 100, market_value: undefined });
    const result = buildLedgerHoldings([p1, p2], []);
    expect(result[0].portfolioWeight).toBeCloseTo(50, 1);
    expect(result[1].portfolioWeight).toBeCloseTo(50, 1);
  });

  it("returns undefined portfolioWeight when marketValue is unavailable", () => {
    const pos = makePosition({ shares: 10, current_price: undefined, market_value: undefined });
    const result = buildLedgerHoldings([pos], []);
    expect(result[0].portfolioWeight).toBeUndefined();
  });

  it("excludes unavailable-value holdings from weight denominator", () => {
    // p1 has real price; p2 has no price — p1 weight should be 100%, not inflated by p2
    const p1 = makePosition({ ticker: "AAPL", shares: 10, current_price: 200, market_value: undefined });
    const p2 = makePosition({ ticker: "NOPR", shares: 10, current_price: undefined, market_value: undefined });
    const result = buildLedgerHoldings([p1, p2], []);
    expect(result[0].portfolioWeight).toBeCloseTo(100, 1);
    expect(result[1].portfolioWeight).toBeUndefined();
  });

  it("marks isStaleOrThin=true when no intel card", () => {
    const result = buildLedgerHoldings([makePosition()], []);
    expect(result[0].isStaleOrThin).toBe(true);
  });

  it("marks isStaleOrThin=true for THIN evidence band", () => {
    const result = buildLedgerHoldings([makePosition()], [makeIntelCard({ evidence_band: "THIN" })]);
    expect(result[0].isStaleOrThin).toBe(true);
  });

  it("marks isStaleOrThin=false for STRONG evidence band", () => {
    const result = buildLedgerHoldings([makePosition()], [makeIntelCard({ evidence_band: "STRONG" })]);
    expect(result[0].isStaleOrThin).toBe(false);
  });
});

// ── buildConcentrationTop5 ────────────────────────────────────────────────────

describe("buildConcentrationTop5", () => {
  it("returns empty array for empty input", () => {
    expect(buildConcentrationTop5([])).toEqual([]);
  });

  it("returns all holdings when 5 or fewer", () => {
    const holdings = buildLedgerHoldings(
      [makePosition({ ticker: "A", shares: 1, current_price: 10 }),
       makePosition({ ticker: "B", shares: 1, current_price: 20 })],
      []
    );
    expect(buildConcentrationTop5(holdings)).toHaveLength(2);
  });

  it("returns top 5 by market value descending", () => {
    const positions = [100, 300, 50, 200, 150, 400, 75].map((price, i) =>
      makePosition({ ticker: `T${i}`, shares: 1, current_price: price })
    );
    const holdings = buildLedgerHoldings(positions, []);
    const top5 = buildConcentrationTop5(holdings);
    expect(top5).toHaveLength(5);
    expect(top5[0].marketValue).toBe(400);
    expect(top5[1].marketValue).toBe(300);
    expect(top5[4].marketValue).toBe(100);
  });

  it("does not mutate original holdings array", () => {
    const holdings = buildLedgerHoldings(
      [makePosition({ ticker: "A", shares: 1, current_price: 100 }),
       makePosition({ ticker: "B", shares: 1, current_price: 200 })],
      []
    );
    const originalOrder = holdings.map(h => h.ticker);
    buildConcentrationTop5(holdings);
    expect(holdings.map(h => h.ticker)).toEqual(originalOrder);
  });
});

// ── buildCategoryExposure ─────────────────────────────────────────────────────

describe("buildCategoryExposure", () => {
  it("returns empty array for empty holdings", () => {
    expect(buildCategoryExposure([])).toEqual([]);
  });

  it("groups holdings by category", () => {
    const positions = [
      makePosition({ ticker: "A", category: "Core", shares: 1, current_price: 100 }),
      makePosition({ ticker: "B", category: "ETF", shares: 1, current_price: 200 }),
      makePosition({ ticker: "C", category: "Core", shares: 1, current_price: 100 }),
    ];
    const holdings = buildLedgerHoldings(positions, []);
    const rows = buildCategoryExposure(holdings);
    const core = rows.find(r => r.category === "Core");
    expect(core?.count).toBe(2);
  });

  it("computes correct percentage per category", () => {
    const positions = [
      makePosition({ ticker: "A", category: "Core", shares: 1, current_price: 300 }),
      makePosition({ ticker: "B", category: "ETF", shares: 1, current_price: 100 }),
      makePosition({ ticker: "C", category: "ETF", shares: 1, current_price: 100 }),
    ];
    const holdings = buildLedgerHoldings(positions, []);
    const rows = buildCategoryExposure(holdings);
    const core = rows.find(r => r.category === "Core");
    const etf = rows.find(r => r.category === "ETF");
    expect(core?.pct).toBeCloseTo(60, 1);
    expect(etf?.pct).toBeCloseTo(40, 1);
  });

  it("sorts by pct descending", () => {
    const positions = [
      makePosition({ ticker: "A", category: "Small", shares: 1, current_price: 50 }),
      makePosition({ ticker: "B", category: "Large", shares: 1, current_price: 500 }),
    ];
    const holdings = buildLedgerHoldings(positions, []);
    const rows = buildCategoryExposure(holdings);
    expect(rows[0].category).toBe("Large");
  });

  it("renders missing theme data as unavailable — pct is undefined when no current price", () => {
    // Holding with no current_price and no market_value → marketValue undefined → pct undefined
    const holdings = buildLedgerHoldings(
      [makePosition({ ticker: "A", category: "Crypto", current_price: undefined, market_value: undefined })],
      []
    );
    const rows = buildCategoryExposure(holdings);
    expect(rows[0].pct).toBeUndefined();
    expect(rows[0].valueUnavailable).toBe(true);
  });
});

// ── buildThesisHealthSummary ──────────────────────────────────────────────────

describe("buildThesisHealthSummary", () => {
  it("returns unavailable for empty holdings", () => {
    const result = buildThesisHealthSummary([]);
    expect(result.status).toBe("unavailable");
  });

  it("returns unavailable when no intel data available", () => {
    const holdings = buildLedgerHoldings([makePosition()], []);
    const result = buildThesisHealthSummary(holdings);
    expect(result.status).toBe("unavailable");
    expect(result.noIntelCount).toBe(1);
  });

  it("returns strong when majority have STRONG evidence", () => {
    const positions = ["A","B","C"].map(t => makePosition({ ticker: t }));
    const cards = ["A","B","C"].map(t => makeIntelCard({ ticker: t, evidence_band: "STRONG" }));
    const holdings = buildLedgerHoldings(positions, cards);
    expect(buildThesisHealthSummary(holdings).status).toBe("strong");
  });

  it("returns needs_attention when high risk count > 0", () => {
    const positions = [makePosition()];
    const cards = [makeIntelCard({ evidence_band: "STRONG", risk_level: "HIGH" })];
    const holdings = buildLedgerHoldings(positions, cards);
    expect(buildThesisHealthSummary(holdings).status).toBe("needs_attention");
  });

  it("returns needs_attention when majority have THIN evidence", () => {
    const positions = ["A","B","C"].map(t => makePosition({ ticker: t }));
    const cards = ["A","B","C"].map(t => makeIntelCard({ ticker: t, evidence_band: "THIN" }));
    const holdings = buildLedgerHoldings(positions, cards);
    expect(buildThesisHealthSummary(holdings).status).toBe("needs_attention");
  });

  it("counts evidence bands correctly", () => {
    const positions = ["A","B","C"].map(t => makePosition({ ticker: t }));
    const cards = [
      makeIntelCard({ ticker: "A", evidence_band: "STRONG" }),
      makeIntelCard({ ticker: "B", evidence_band: "PARTIAL" }),
      makeIntelCard({ ticker: "C", evidence_band: "THIN" }),
    ];
    const holdings = buildLedgerHoldings(positions, cards);
    const result = buildThesisHealthSummary(holdings);
    expect(result.strongCount).toBe(1);
    expect(result.partialCount).toBe(1);
    expect(result.thinCount).toBe(1);
  });

  it("does not render fabricated scores — status is derived only from existing fields", () => {
    const holdings = buildLedgerHoldings([makePosition()], [makeIntelCard()]);
    const result = buildThesisHealthSummary(holdings);
    // No fabricated numeric "thesis score" — only deterministic bucket labels
    expect(typeof result.status).toBe("string");
    expect(typeof result.statusLabel).toBe("string");
  });
});

// ── buildSourceFreshnessSummary ───────────────────────────────────────────────

describe("buildSourceFreshnessSummary", () => {
  it("returns unavailable when snapshot is null", () => {
    const result = buildSourceFreshnessSummary(null);
    expect(result.overallStatus).toBe("unavailable");
  });

  it("returns unavailable when snapshot is undefined", () => {
    const result = buildSourceFreshnessSummary(undefined);
    expect(result.overallStatus).toBe("unavailable");
  });

  it("returns fresh when no stale/missing sources", () => {
    const snap = {
      generated_at: new Date().toISOString(),
      diagnostics: {
        source_freshness: {
          news: { state: "FRESH", is_critical: false, fresh_count: 5, stale_count: 0, hard_stale_count: 0, missing_count: 0, oldest_age_hours: 1, newest_age_hours: 0 },
        },
        stale_source_count: 0,
        hard_stale_source_count: 0,
        missing_source_count: 0,
      },
    } as unknown as IntelV3Snapshot;
    expect(buildSourceFreshnessSummary(snap).overallStatus).toBe("fresh");
  });

  it("returns stale when stale sources present", () => {
    const snap = {
      generated_at: new Date().toISOString(),
      diagnostics: {
        source_freshness: {
          news: { state: "STALE", is_critical: false, fresh_count: 0, stale_count: 1, hard_stale_count: 0, missing_count: 0, oldest_age_hours: 30, newest_age_hours: 25 },
        },
        stale_source_count: 1,
        hard_stale_source_count: 0,
        missing_source_count: 0,
      },
    } as unknown as IntelV3Snapshot;
    expect(buildSourceFreshnessSummary(snap).overallStatus).toBe("stale");
  });

  it("returns hard_stale when hard stale sources present", () => {
    const snap = {
      generated_at: new Date().toISOString(),
      diagnostics: {
        source_freshness: {
          filings: { state: "HARD_STALE", is_critical: true, fresh_count: 0, stale_count: 0, hard_stale_count: 1, missing_count: 0, oldest_age_hours: 100, newest_age_hours: 90 },
        },
        stale_source_count: 0,
        hard_stale_source_count: 1,
        missing_source_count: 0,
      },
    } as unknown as IntelV3Snapshot;
    expect(buildSourceFreshnessSummary(snap).overallStatus).toBe("hard_stale");
  });
});

// ── buildHoldingDrawerData ────────────────────────────────────────────────────

describe("buildHoldingDrawerData", () => {
  it("sets isThesisStale=true for THIN evidence holding", () => {
    const holdings = buildLedgerHoldings([makePosition()], [makeIntelCard({ evidence_band: "THIN" })]);
    const drawer = buildHoldingDrawerData(holdings[0]);
    expect(drawer.isThesisStale).toBe(true);
    expect(drawer.staleWarning).toBeTruthy();
  });

  it("sets isThesisStale=false for STRONG evidence holding", () => {
    const holdings = buildLedgerHoldings([makePosition()], [makeIntelCard({ evidence_band: "STRONG" })]);
    const drawer = buildHoldingDrawerData(holdings[0]);
    expect(drawer.isThesisStale).toBe(false);
    expect(drawer.staleWarning).toBeUndefined();
  });
});

// ── Plain-English formatters ──────────────────────────────────────────────────

describe("actionToLabel", () => {
  it("maps BUY to plain-English label", () => expect(actionToLabel("BUY")).toBe("Buy"));
  it("maps HOLD to plain-English label", () => expect(actionToLabel("HOLD")).toBe("Hold"));
  it("maps TRIM to plain-English label", () => expect(actionToLabel("TRIM")).toBe("Trim"));
  it("maps SELL to plain-English label", () => expect(actionToLabel("SELL")).toBe("Sell"));
  it("maps NO_INTEL to dash", () => expect(actionToLabel("NO_INTEL")).toBe("—"));
  it("maps unknown to dash — no raw metric keys exposed", () => expect(actionToLabel("UNKNOWN_KEY")).toBe("—"));
});

describe("evidenceBandToFreshnessCue", () => {
  it("maps STRONG to readable cue", () => expect(evidenceBandToFreshnessCue("STRONG")).toBe("Strong evidence"));
  it("maps PARTIAL to readable cue", () => expect(evidenceBandToFreshnessCue("PARTIAL")).toBe("Partial evidence"));
  it("maps THIN to readable cue", () => expect(evidenceBandToFreshnessCue("THIN")).toBe("Thin evidence"));
  it("maps undefined to readable fallback — not raw key", () => expect(evidenceBandToFreshnessCue(undefined)).toBe("No evidence"));
});

describe("actionChipStyle", () => {
  it("returns buy style for BUY", () => expect(actionChipStyle("BUY")).toContain("action-buy"));
  it("returns sell style for SELL", () => expect(actionChipStyle("SELL")).toContain("action-sell"));
  it("returns muted style for NO_INTEL — not raw key class", () => {
    const style = actionChipStyle("NO_INTEL");
    expect(style).not.toContain("action-buy");
    expect(style).toContain("text-text-muted");
  });
});

describe("formatRelativeAge", () => {
  it("returns — for undefined", () => expect(formatRelativeAge(undefined)).toBe("—"));
  it("returns — for invalid date", () => expect(formatRelativeAge("not-a-date")).toBe("—"));
  it("returns < 1h ago for very recent timestamp", () => {
    expect(formatRelativeAge(new Date().toISOString())).toBe("< 1h ago");
  });
  it("returns Xh ago for timestamps within 24h", () => {
    const iso = new Date(Date.now() - 5 * 3_600_000).toISOString();
    expect(formatRelativeAge(iso)).toBe("5h ago");
  });
  it("returns Xd ago for timestamps > 24h", () => {
    const iso = new Date(Date.now() - 48 * 3_600_000).toISOString();
    expect(formatRelativeAge(iso)).toBe("2d ago");
  });
});

// ── Future-only capsules — Coming-Later contract ──────────────────────────────

describe("Coming-Later contract", () => {
  it("buildLedgerData does not fabricate theme/sector data", () => {
    // Category exposure uses only existing 'category' field from Position
    const positions = [makePosition({ category: "Core" })];
    const data = buildLedgerData(positions, undefined);
    const row = data.categoryExposure[0];
    // Must render existing category only — not invented sector/theme labels
    expect(row.category).toBe("Core");
  });

  it("buildLedgerData marks hasIntelData=false when no snapshot", () => {
    const data = buildLedgerData([makePosition()], undefined);
    expect(data.hasIntelData).toBe(false);
  });

  it("buildThesisHealthSummary does not fabricate scores for holdings without intel", () => {
    const holdings = buildLedgerHoldings([makePosition()], []);
    const result = buildThesisHealthSummary(holdings);
    // Must not invent a numeric thesis score — status string only
    expect(result.status).toBe("unavailable");
    expect(result.noIntelCount).toBe(1);
  });
});
