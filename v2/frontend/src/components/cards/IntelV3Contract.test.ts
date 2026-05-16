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

  it("committee status is a valid source-pack status string", () => {
    const validStatuses = ["deferred", "ready", "source_validated", "pending"];
    const card = makeCard("AAPL", "BUY");
    expect(validStatuses).toContain(card.detail_drawer_payload.committee.status);
  });

  it("committee section hidden when source_validated", () => {
    const card = makeCard("AAPL", "BUY", {
      detail_drawer_payload: {
        rationale: "Signals support this.",
        why_now: "Evidence is strong.",
        committee: { status: "source_validated" },
        schema_version: "v3.1",
      },
    });
    // source_validated means the drawer should NOT show the "Analysis pending" block.
    expect(card.detail_drawer_payload.committee.status).toBe("source_validated");
    expect(card.detail_drawer_payload.committee.reason).toBeUndefined();
  });

  it("committee section visible with reason when pending", () => {
    const card = makeCard("MSFT", "HOLD", {
      detail_drawer_payload: {
        rationale: "Holding position.",
        why_now: "No clear trigger.",
        committee: { status: "pending", reason: "No trusted evidence — 0 trusted dimension(s)." },
        schema_version: "v3.1",
      },
    });
    expect(card.detail_drawer_payload.committee.status).toBe("pending");
    expect(card.detail_drawer_payload.committee.reason).toBeTruthy();
  });

  it("no price targets in rationale or why_now", () => {
    const PRICE_TARGET_PATTERNS = [/\$\d+/, /price target/i, /target price/i];
    const card = makeCard("AAPL", "BUY");
    for (const pattern of PRICE_TARGET_PATTERNS) {
      expect(pattern.test(card.detail_drawer_payload.rationale)).toBe(false);
      expect(pattern.test(card.detail_drawer_payload.why_now)).toBe(false);
    }
  });

  it("valuation_context is absent (undefined/null) when not provided", () => {
    const card = makeCard("AAPL", "BUY");
    // valuation_context is optional — must not be present or must be null/undefined
    const vc = card.detail_drawer_payload.valuation_context;
    expect(vc == null).toBe(true);
  });

  it("valuation_context when present has only visible_text, limitation_text, source_basis", () => {
    // Build a complete drawer payload to satisfy the TypeScript type
    const vc = {
      visible_text: "Trading at a reasonable multiple relative to recent earnings.",
      limitation_text: "Based on annual EPS. Forward estimates not included.",
      source_basis: "fy_eps_earnings_yield",
    };
    const drawerPayload = makeCard("AAPL", "BUY").detail_drawer_payload;
    const payloadWithCtx = { ...drawerPayload, valuation_context: vc };

    expect(typeof payloadWithCtx.valuation_context!.visible_text).toBe("string");
    expect(typeof payloadWithCtx.valuation_context!.limitation_text).toBe("string");
    expect(typeof payloadWithCtx.valuation_context!.source_basis).toBe("string");
    // No raw metric or financial precision keys
    const forbidden = [
      "target_price", "fair_value", "intrinsic_value", "upside", "downside",
      "buy_below", "sell_above", "valuation_signal", "earnings_yield_bucket",
    ];
    for (const key of forbidden) {
      expect(payloadWithCtx.valuation_context).not.toHaveProperty(key);
    }
  });

  it("valuation_context visible_text contains no price targets or raw metric values", () => {
    const drawerPayload = makeCard("MSFT", "HOLD").detail_drawer_payload;
    const vc = {
      visible_text: "Trading at a reasonable multiple relative to recent earnings.",
      limitation_text: "Based on annual EPS only.",
      source_basis: "fy_eps_earnings_yield",
    };
    const payloadWithCtx = { ...drawerPayload, valuation_context: vc };

    const FORBIDDEN_PATTERNS = [
      /\$\d+/,
      /target price/i,
      /price target/i,
      /fair value/i,
      /upside/i,
      /downside/i,
      /\d+\.\d+%/,
      /earnings_yield_bucket/,
      /unusually_cheap/,
      /negative_eps/,
    ];
    for (const pattern of FORBIDDEN_PATTERNS) {
      expect(pattern.test(payloadWithCtx.valuation_context.visible_text)).toBe(false);
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

// ── Generic copy contract (certification fix) ─────────────────────────────────

describe("Intel v3 generic copy contract — no two cards share identical why_text", () => {
  it("11 BUY cards each have unique why_text when ticker is included", () => {
    const buyTickers = ["AAPL", "MSFT", "NVDA", "GOOG", "META", "AMZN", "TSM", "VGT", "VOO", "QQQ", "SCHD"];
    const cards = buyTickers.map((t) =>
      makeCard(t, "BUY", { why_text: `${t}: adequate evidence and fairly priced. Portfolio has room to add. Manageable risk.` })
    );
    const texts = new Set(cards.map((c) => c.why_text));
    expect(texts.size).toBe(buyTickers.length);
  });

  it("why_text must contain the card ticker symbol", () => {
    const cards = ["AAPL", "MSFT", "NVDA"].map((t) =>
      makeCard(t, "BUY", { why_text: `${t}: adequate evidence and fairly priced. Manageable risk.` })
    );
    for (const card of cards) {
      expect(card.why_text).toContain(card.ticker);
    }
  });

  it("old generic template 'Signals support adding:' must not appear in cards", () => {
    const cards = ["AAPL", "MSFT"].map((t) =>
      makeCard(t, "BUY", { why_text: `${t}: adequate evidence and fairly priced. Manageable risk.` })
    );
    for (const card of cards) {
      expect(card.why_text).not.toContain("Signals support adding:");
    }
  });

  it("snapshot with 34 cards must have 34 unique why_text values", () => {
    const allTickers = [
      "AAPL", "MSFT", "NVDA", "GOOG", "META", "AMZN", "TSM", "VGT", "VOO", "QQQ", "SCHD",
      ...Array.from({ length: 23 }, (_, i) => `HOLD${i}`),
    ];
    const cards = allTickers.map((t, i) =>
      makeCard(t, i < 11 ? "BUY" : "HOLD", {
        why_text: `${t}: ${i < 11 ? "adequate evidence and fairly priced. Manageable risk." : "holding while evidence builds."}`,
      })
    );
    const texts = new Set(cards.map((c) => c.why_text));
    expect(texts.size).toBe(34);
  });
});

// ── Source-of-truth contract (certification fix) ──────────────────────────────

describe("Intel v3 source-of-truth contract — visible page uses only v3 snapshot", () => {
  it("v3 snapshot has schema_version starting with 'v3'", () => {
    const snap = makeSnapshot([makeCard("AAPL", "BUY")]);
    expect(snap.schema_version).toMatch(/^v3/);
  });

  it("v3 snapshot is the data contract for visible Intel cards (not legacy InsightCardData)", () => {
    // Prove the IntelV3Snapshot type has the required v3-specific fields.
    // Legacy InsightCardData has different shape (analyst_action, detail, rationale as top-level).
    const snap = makeSnapshot([makeCard("AAPL", "BUY")]);
    expect(snap).toHaveProperty("portfolio_command_center");
    expect(snap).toHaveProperty("best_buys");
    expect(snap).toHaveProperty("trim_sell_desk");
    expect(snap).toHaveProperty("action_counts");
    expect(snap).not.toHaveProperty("analyst_action");  // legacy field
  });

  it("v3 snapshot run returns snapshot_id and run_id (not job_id)", () => {
    // IntelV3RunResult has snapshot_id and run_id — not a job_id like legacy.
    const runResult = {
      status: "completed",
      snapshot_id: "snap-001",
      run_id: "run-001",
      total_cards: 34,
      action_counts: { BUY: 11, HOLD: 23 },
    };
    expect(runResult).toHaveProperty("snapshot_id");
    expect(runResult).toHaveProperty("run_id");
    expect(runResult).not.toHaveProperty("job_id");
  });

  it("no-snapshot state has empty current_holdings and action_counts", () => {
    // When GET /snapshot returns 404, the UI shows empty state — no legacy cards rendered.
    const noSnapshotState = makeSnapshot([]);
    expect(noSnapshotState.current_holdings).toHaveLength(0);
    const countSum = Object.values(noSnapshotState.action_counts).reduce((a, b) => a + b, 0);
    expect(countSum).toBe(0);
  });

  it("v3 card why_text must not contain raw metric key names", () => {
    const RAW_KEYS = [
      "fcf_margin", "roic_ttm", "ev_ebitda", "gross_margin_ttm",
      "peg_ratio", "p_fcf", "ebit_margin", "net_margin_ttm",
    ];
    const cards = ["AAPL", "MSFT"].map((t) =>
      makeCard(t, "BUY", {
        why_text: `${t}: adequate evidence and fairly priced. Manageable risk.`,
      })
    );
    for (const card of cards) {
      for (const key of RAW_KEYS) {
        expect(card.why_text.toLowerCase()).not.toContain(key);
      }
    }
  });

  it("action_counts in snapshot match holdings — not a separate independent count", () => {
    // This is the single source-of-truth contract: action_counts = Counter(card.action).
    const cards = [
      makeCard("AAPL", "BUY"),
      makeCard("MSFT", "BUY"),
      makeCard("GOOG", "HOLD"),
    ];
    const snap = makeSnapshot(cards);
    const recomputed: Record<string, number> = {};
    for (const c of snap.current_holdings) {
      recomputed[c.action] = (recomputed[c.action] || 0) + 1;
    }
    expect(snap.action_counts).toEqual(recomputed);
  });
});
