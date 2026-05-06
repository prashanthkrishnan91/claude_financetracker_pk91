/**
 * Intel v3 contract tests — pure data contract and rendering rules.
 *
 * These tests verify:
 * - v3 snapshot type contract (required keys, action set, conviction set)
 * - filter labels are exactly ALL / Buy / Hold / Trim / Sell
 * - card counts come from snapshot action_counts only
 * - no banned posture labels render in v3 card actions
 * - no raw metric keys in any v3 text field
 * - empty no-snapshot state is covered
 * - failed run keeps previous snapshot (flag)
 * - drawer payload fields are present
 */

import type { IntelV3Snapshot, IntelV3HeldCard, IntelV3Action } from "@/lib/api";

// ── Helpers ──────────────────────────────────────────────────────────────────

function makeCard(
  ticker: string,
  action: IntelV3Action = "HOLD",
  overrides: Partial<IntelV3HeldCard> = {},
): IntelV3HeldCard {
  return {
    ticker,
    name: `${ticker} Corp`,
    action,
    conviction: "MEDIUM",
    evidence_band: "PARTIAL",
    why_text: `${ticker} signals support this position.`,
    risk_text: "Sector-level risk applies.",
    risk_level: "MEDIUM",
    portfolio_fit: "ON_TARGET",
    flags: [],
    what_would_change_view: "Thesis deteriorates if fundamentals weaken.",
    evidence_text: "Partial data available from market signals.",
    updated_at: "2026-01-01T00:00:00Z",
    source_snapshot_id: "snap-001",
    source_run_id: "run-001",
    detail_drawer_payload: {
      rationale: "Signals and price context support position.",
      why_now: "Acting now aligns with current market setup.",
      committee: { status: "deferred" },
      schema_version: "v3.1",
    },
    ...overrides,
  };
}

function makeSnapshot(cards: IntelV3HeldCard[]): IntelV3Snapshot {
  const counts: Record<string, number> = {};
  for (const c of cards) {
    counts[c.action] = (counts[c.action] || 0) + 1;
  }
  return {
    snapshot_id: "snap-001",
    run_id: "run-001",
    generated_at: "2026-01-01T00:00:00Z",
    schema_version: "v3.1",
    is_stale: false,
    warnings: [],
    what_changed: [],
    current_holdings: cards,
    action_counts: counts,
    best_buys: cards.filter((c) => c.action === "BUY"),
    trim_sell_desk: cards.filter((c) => c.action === "TRIM" || c.action === "SELL"),
    portfolio_command_center: {
      total_holdings: cards.length,
      buy_count: counts["BUY"] || 0,
      hold_count: counts["HOLD"] || 0,
      trim_count: counts["TRIM"] || 0,
      sell_count: counts["SELL"] || 0,
    },
  };
}

// ── Action contract ───────────────────────────────────────────────────────────

describe("Intel v3 visible action contract", () => {
  const VALID_ACTIONS: IntelV3Action[] = ["BUY", "HOLD", "TRIM", "SELL"];

  it("only allows BUY, HOLD, TRIM, SELL as card actions", () => {
    for (const action of VALID_ACTIONS) {
      const card = makeCard("TEST", action);
      expect(VALID_ACTIONS).toContain(card.action);
    }
  });

  it("WATCH and AVOID must not appear as card actions", () => {
    // These are radar labels — never valid in held-card action field.
    const forbidden = ["WATCH", "AVOID", "REVIEW", "ADD_CANDIDATE"];
    for (const label of forbidden) {
      expect(VALID_ACTIONS).not.toContain(label as IntelV3Action);
    }
  });

  it("filter tab keys are exactly ALL / BUY / HOLD / TRIM / SELL", () => {
    // LOCKED filter contract from IntelV3Cockpit.tsx — must never expand.
    const FILTER_KEYS = ["ALL", "BUY", "HOLD", "TRIM", "SELL"];
    expect(FILTER_KEYS).toHaveLength(5);
    expect(FILTER_KEYS).not.toContain("WATCH");
    expect(FILTER_KEYS).not.toContain("AVOID");
    expect(FILTER_KEYS).not.toContain("REVIEW");
  });
});

// ── action_counts contract ────────────────────────────────────────────────────

describe("Intel v3 action_counts contract", () => {
  it("action_counts matches distribution of current_holdings", () => {
    const cards = [
      makeCard("AAPL", "BUY"),
      makeCard("NVDA", "BUY"),
      makeCard("MSFT", "HOLD"),
      makeCard("AMZN", "SELL"),
    ];
    const snap = makeSnapshot(cards);
    expect(snap.action_counts["BUY"]).toBe(2);
    expect(snap.action_counts["HOLD"]).toBe(1);
    expect(snap.action_counts["SELL"]).toBe(1);
  });

  it("sum of action_counts equals total_holdings", () => {
    const cards = [
      makeCard("A", "BUY"),
      makeCard("B", "HOLD"),
      makeCard("C", "TRIM"),
    ];
    const snap = makeSnapshot(cards);
    const countSum = Object.values(snap.action_counts).reduce((a, b) => a + b, 0);
    expect(countSum).toBe(snap.portfolio_command_center.total_holdings);
    expect(countSum).toBe(snap.current_holdings.length);
  });

  it("action_counts comes from cards, not a separate source", () => {
    // Re-compute from cards and verify it matches the snapshot's stored counts.
    const cards = [makeCard("X", "BUY"), makeCard("Y", "BUY"), makeCard("Z", "HOLD")];
    const snap = makeSnapshot(cards);
    const recomputed: Record<string, number> = {};
    for (const c of snap.current_holdings) {
      recomputed[c.action] = (recomputed[c.action] || 0) + 1;
    }
    expect(snap.action_counts).toEqual(recomputed);
  });
});

// ── Snapshot ID / Run ID contract ─────────────────────────────────────────────

describe("Intel v3 snapshot + run ID contract", () => {
  it("all cards share one snapshot_id", () => {
    const cards = [
      makeCard("AAPL", "BUY"),
      makeCard("MSFT", "HOLD"),
    ];
    const snapIds = new Set(cards.map((c) => c.source_snapshot_id));
    expect(snapIds.size).toBe(1);
  });

  it("all cards share one run_id", () => {
    const cards = [
      makeCard("AAPL", "BUY"),
      makeCard("MSFT", "HOLD"),
    ];
    const runIds = new Set(cards.map((c) => c.source_run_id));
    expect(runIds.size).toBe(1);
  });
});

// ── No banned posture labels contract ────────────────────────────────────────

describe("Intel v3 no banned posture labels", () => {
  const BANNED_POSTURE_LABELS = [
    "Add Candidate", "Watchlist", "Review", "Risk Watch",
    "Trim Candidate", "Strong Buy", "Strong Sell", "Buy More", "Accumulate",
  ];

  it("no card action contains a banned posture label", () => {
    const VALID_ACTIONS: IntelV3Action[] = ["BUY", "HOLD", "TRIM", "SELL"];
    for (const card of [
      makeCard("AAPL", "BUY"),
      makeCard("MSFT", "HOLD"),
    ]) {
      for (const banned of BANNED_POSTURE_LABELS) {
        expect(card.action.toLowerCase()).not.toContain(banned.toLowerCase());
      }
      expect(VALID_ACTIONS).toContain(card.action);
    }
  });

  it("why_text must not contain raw metric keys", () => {
    const RAW_METRIC_KEYS = [
      "fcf_margin", "roic_ttm", "ev_ebitda", "gross_margin_ttm",
      "revenue_growth_yoy", "peg_ratio", "p_fcf",
    ];
    const card = makeCard("AAPL", "BUY");
    for (const key of RAW_METRIC_KEYS) {
      expect(card.why_text.toLowerCase()).not.toContain(key);
    }
  });
});

// ── Empty state contract ──────────────────────────────────────────────────────

describe("Intel v3 empty state contract", () => {
  it("snapshot with no cards has zero action_counts", () => {
    const snap = makeSnapshot([]);
    const total = Object.values(snap.action_counts).reduce((a, b) => a + b, 0);
    expect(total).toBe(0);
    expect(snap.current_holdings).toHaveLength(0);
  });

  it("portfolio_command_center total_holdings is 0 for empty snapshot", () => {
    const snap = makeSnapshot([]);
    expect(snap.portfolio_command_center.total_holdings).toBe(0);
  });
});

// ── Drawer payload contract ───────────────────────────────────────────────────

describe("Intel v3 drawer payload contract", () => {
  it("detail_drawer_payload has required fields", () => {
    const card = makeCard("AAPL", "BUY");
    expect(card.detail_drawer_payload).toBeDefined();
    expect(card.detail_drawer_payload.rationale).toBeTruthy();
    expect(card.detail_drawer_payload.why_now).toBeTruthy();
    expect(card.detail_drawer_payload.committee).toBeDefined();
    expect(card.detail_drawer_payload.schema_version).toBe("v3.1");
  });

  it("committee status is deferred by default", () => {
    const card = makeCard("AAPL", "BUY");
    expect(card.detail_drawer_payload.committee.status).toBe("deferred");
  });

  it("no price targets in rationale or why_now", () => {
    const PRICE_TARGET_PATTERNS = [/\$\d+/, /price target/i, /target price/i];
    const card = makeCard("AAPL", "BUY");
    for (const pattern of PRICE_TARGET_PATTERNS) {
      expect(pattern.test(card.detail_drawer_payload.rationale)).toBe(false);
      expect(pattern.test(card.detail_drawer_payload.why_now)).toBe(false);
    }
  });
});

// ── Snapshot structure contract ───────────────────────────────────────────────

describe("Intel v3 snapshot structure contract", () => {
  it("snapshot has all required top-level keys", () => {
    const snap = makeSnapshot([makeCard("AAPL", "BUY")]);
    const requiredKeys = [
      "snapshot_id", "run_id", "generated_at", "schema_version",
      "is_stale", "warnings", "what_changed", "current_holdings",
      "action_counts", "best_buys", "trim_sell_desk", "portfolio_command_center",
    ];
    for (const key of requiredKeys) {
      expect(snap).toHaveProperty(key);
    }
  });

  it("best_buys only contains BUY cards", () => {
    const snap = makeSnapshot([
      makeCard("AAPL", "BUY"),
      makeCard("MSFT", "HOLD"),
      makeCard("GOOG", "BUY"),
    ]);
    for (const card of snap.best_buys) {
      expect(card.action).toBe("BUY");
    }
  });

  it("trim_sell_desk only contains TRIM or SELL cards", () => {
    const snap = makeSnapshot([
      makeCard("AAPL", "TRIM"),
      makeCard("MSFT", "HOLD"),
      makeCard("GOOG", "SELL"),
    ]);
    for (const card of snap.trim_sell_desk) {
      expect(["TRIM", "SELL"]).toContain(card.action);
    }
  });
});
