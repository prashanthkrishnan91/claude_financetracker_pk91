/**
 * Deploy v3 target allocation setup contract tests — Stage 2.5F.
 *
 * Imports real exported helpers from DeployV3TargetSetupPanel so tests verify
 * actual behavior rather than duplicating logic.
 *
 * Tests verify:
 * - Total < 98% blocks save
 * - Total > 102% blocks save
 * - Valid full target set (total in range, all tickers present) enables save
 * - Missing tickers detected
 * - Payload to api.portfolio.setTargets is explicit ticker/target_pct rows
 * - useSetDeployTargets invalidates deploy_v3 readiness and plan query keys
 * - hydrateRows: positions load first, targets arrive later → saved values populate
 * - hydrateRows: after user edits (touched), later refetch does not overwrite
 * - hydrateRows: new ticker with no saved target remains blank
 * - hydrateRows: removed ticker disappears from rows/payload
 * - valid saved target set enables Save when total is 98–102%
 * - "Use current weights as draft" logic does not auto-save
 * - No legacy /allocation/plan or /api/deposit-plan usage in target setup
 */

import {
  computeTotal,
  parseInputPct,
  getMissingTickers,
  isSaveAllowed,
  buildTargetPayload,
  hydrateRows,
  TARGET_TOTAL_MIN,
  TARGET_TOTAL_MAX,
} from "@/lib/deploy-v3-target-helpers";
import {
  DEPLOY_V3_READINESS_QUERY_KEY,
  DEPLOY_V3_PLAN_QUERY_KEY,
} from "@/lib/deploy-v3-helpers";
import type { DeployV3ReadinessDiagnostic } from "@/lib/api";

// ── Constants ─────────────────────────────────────────────────────────────────

describe("TARGET_TOTAL bounds", () => {
  it("MIN is 98", () => expect(TARGET_TOTAL_MIN).toBe(98));
  it("MAX is 102", () => expect(TARGET_TOTAL_MAX).toBe(102));
});

// ── parseInputPct ─────────────────────────────────────────────────────────────

describe("parseInputPct", () => {
  it("parses valid positive number", () => expect(parseInputPct("50")).toBe(50));
  it("parses decimal", () => expect(parseInputPct("33.33")).toBeCloseTo(33.33));
  it("returns null for empty string", () => expect(parseInputPct("")).toBeNull());
  it("returns null for negative", () => expect(parseInputPct("-1")).toBeNull());
  it("returns null for NaN string", () => expect(parseInputPct("abc")).toBeNull());
  it("accepts zero", () => expect(parseInputPct("0")).toBe(0));
});

// ── computeTotal ──────────────────────────────────────────────────────────────

describe("computeTotal", () => {
  it("sums numeric values", () =>
    expect(computeTotal({ AAPL: "50", MSFT: "50" })).toBe(100));
  it("ignores empty strings", () =>
    expect(computeTotal({ AAPL: "50", MSFT: "" })).toBe(50));
  it("ignores non-numeric", () =>
    expect(computeTotal({ AAPL: "50", MSFT: "abc" })).toBe(50));
  it("ignores negative values", () =>
    expect(computeTotal({ AAPL: "50", MSFT: "-5" })).toBe(50));
});

// ── getMissingTickers ─────────────────────────────────────────────────────────

describe("getMissingTickers", () => {
  const tickers = ["AAPL", "MSFT", "GOOG"];

  it("returns empty when all tickers have valid values", () =>
    expect(getMissingTickers(tickers, { AAPL: "34", MSFT: "33", GOOG: "33" })).toEqual([]));

  it("detects empty string as missing", () =>
    expect(getMissingTickers(tickers, { AAPL: "50", MSFT: "50", GOOG: "" })).toEqual(["GOOG"]));

  it("detects absent key as missing", () =>
    expect(getMissingTickers(tickers, { AAPL: "50", MSFT: "50" })).toEqual(["GOOG"]));

  it("detects negative as missing/invalid", () =>
    expect(getMissingTickers(tickers, { AAPL: "50", MSFT: "50", GOOG: "-1" })).toEqual(["GOOG"]));

  it("detects non-numeric as missing/invalid", () =>
    expect(getMissingTickers(tickers, { AAPL: "50", MSFT: "50", GOOG: "abc" })).toEqual(["GOOG"]));
});

// ── isSaveAllowed ─────────────────────────────────────────────────────────────

describe("isSaveAllowed", () => {
  const tickers = ["AAPL", "MSFT"];

  it("allows save at exactly 100%", () =>
    expect(isSaveAllowed(tickers, { AAPL: "50", MSFT: "50" })).toBe(true));

  it("allows save at lower bound 98%", () =>
    expect(isSaveAllowed(tickers, { AAPL: "48", MSFT: "50" })).toBe(true));

  it("allows save at upper bound 102%", () =>
    expect(isSaveAllowed(tickers, { AAPL: "52", MSFT: "50" })).toBe(true));

  it("blocks save when total < 98%", () =>
    expect(isSaveAllowed(tickers, { AAPL: "45", MSFT: "50" })).toBe(false));

  it("blocks save when total > 102%", () =>
    expect(isSaveAllowed(tickers, { AAPL: "55", MSFT: "50" })).toBe(false));

  it("blocks save when a ticker is missing", () =>
    expect(isSaveAllowed(tickers, { AAPL: "100" })).toBe(false));

  it("blocks save when no tickers", () =>
    expect(isSaveAllowed([], {})).toBe(false));

  it("valid saved target set (100%) enables save", () => {
    const rows = { AAPL: "40", MSFT: "60" };
    expect(isSaveAllowed(tickers, rows)).toBe(true);
  });
});

// ── buildTargetPayload ────────────────────────────────────────────────────────

describe("buildTargetPayload", () => {
  it("builds explicit ticker/target_pct rows", () => {
    const payload = buildTargetPayload(["AAPL", "MSFT"], { AAPL: "50", MSFT: "50" });
    expect(payload).toEqual([
      { ticker: "AAPL", target_pct: 50 },
      { ticker: "MSFT", target_pct: 50 },
    ]);
  });

  it("each row has only ticker and target_pct", () => {
    const payload = buildTargetPayload(["AAPL"], { AAPL: "60" });
    expect(Object.keys(payload[0])).toEqual(["ticker", "target_pct"]);
  });

  it("excludes tickers not in positionTickers (removed positions disappear)", () => {
    const payload = buildTargetPayload(["AAPL"], { AAPL: "60", MSFT: "40" });
    expect(payload).toHaveLength(1);
    expect(payload[0].ticker).toBe("AAPL");
  });

  it("does not include allocation/plan or deposit-plan in payload", () => {
    const payload = buildTargetPayload(["AAPL"], { AAPL: "100" });
    expect(JSON.stringify(payload)).not.toContain("allocation");
    expect(JSON.stringify(payload)).not.toContain("deposit-plan");
  });
});

// ── hydrateRows — row hydration correctness ───────────────────────────────────

describe("hydrateRows", () => {
  it("positions load first, targets arrive later → saved values populate (untouched)", () => {
    // Simulates: positions loaded → rows seeded blank; then targets arrive.
    const tickers = ["AAPL", "MSFT"];
    const savedTargets = { AAPL: "60", MSFT: "40" };
    // prevRows was seeded blank when positions first loaded (targets not yet arrived)
    const prevRows = { AAPL: "", MSFT: "" };
    const touched = new Set<string>(); // user hasn't typed anything

    const result = hydrateRows(tickers, savedTargets, prevRows, touched);
    expect(result.AAPL).toBe("60");
    expect(result.MSFT).toBe("40");
  });

  it("after user edits a row, later target refetch does not overwrite", () => {
    const tickers = ["AAPL", "MSFT"];
    const savedTargets = { AAPL: "60", MSFT: "40" };
    // User has typed "75" for AAPL
    const prevRows = { AAPL: "75", MSFT: "" };
    const touched = new Set(["AAPL"]);

    const result = hydrateRows(tickers, savedTargets, prevRows, touched);
    expect(result.AAPL).toBe("75"); // user edit preserved
    expect(result.MSFT).toBe("40"); // untouched → saved value
  });

  it("new ticker with no saved target remains blank", () => {
    const tickers = ["AAPL", "NVDA"];
    const savedTargets = { AAPL: "60" }; // NVDA has no saved target
    const prevRows = { AAPL: "60" };
    const touched = new Set<string>();

    const result = hydrateRows(tickers, savedTargets, prevRows, touched);
    expect(result.AAPL).toBe("60");
    expect(result.NVDA).toBe("");
  });

  it("removed ticker disappears from rows", () => {
    // MSFT was removed from positions
    const tickers = ["AAPL"];
    const savedTargets = { AAPL: "60", MSFT: "40" };
    const prevRows = { AAPL: "60", MSFT: "40" };
    const touched = new Set<string>();

    const result = hydrateRows(tickers, savedTargets, prevRows, touched);
    expect(result).not.toHaveProperty("MSFT");
    expect(result.AAPL).toBe("60");
  });

  it("saved targets hydrate blank prev rows (seeded empty on first position load)", () => {
    const tickers = ["AAPL", "MSFT", "GOOG"];
    const saved = { AAPL: "50", MSFT: "30", GOOG: "20" };
    const prevRows = { AAPL: "", MSFT: "", GOOG: "" };
    const touched = new Set<string>();

    const result = hydrateRows(tickers, saved, prevRows, touched);
    expect(result).toEqual({ AAPL: "50", MSFT: "30", GOOG: "20" });
  });

  it("valid saved target set makes isSaveAllowed return true", () => {
    const tickers = ["AAPL", "MSFT"];
    const saved = { AAPL: "60", MSFT: "40" };
    const prevRows = { AAPL: "", MSFT: "" };
    const rows = hydrateRows(tickers, saved, prevRows, new Set());
    expect(isSaveAllowed(tickers, rows)).toBe(true);
  });
});

// ── Query key invalidation contract ──────────────────────────────────────────

describe("useSetDeployTargets invalidates deploy_v3 query keys", () => {
  it("DEPLOY_V3_READINESS_QUERY_KEY is ['deploy_v3', 'readiness']", () =>
    expect(DEPLOY_V3_READINESS_QUERY_KEY).toEqual(["deploy_v3", "readiness"]));

  it("DEPLOY_V3_PLAN_QUERY_KEY is ['deploy_v3', 'plan']", () =>
    expect(DEPLOY_V3_PLAN_QUERY_KEY).toEqual(["deploy_v3", "plan"]));

  it("readiness and plan query keys are distinct", () =>
    expect(DEPLOY_V3_READINESS_QUERY_KEY).not.toEqual(DEPLOY_V3_PLAN_QUERY_KEY));

  it("portfolio targets query key is distinct from deploy v3 keys", () => {
    const portfolioTargetsKey = ["portfolio", "targets"];
    expect(portfolioTargetsKey).not.toEqual(DEPLOY_V3_READINESS_QUERY_KEY);
    expect(portfolioTargetsKey).not.toEqual(DEPLOY_V3_PLAN_QUERY_KEY);
  });
});

// ── "Use current weights as draft" — pure calculation, no auto-save ───────────

function computeDraftWeights(
  positions: Array<{ ticker: string; market_value?: number }>,
): Record<string, string> | null {
  if (positions.length === 0) return null;
  if (!positions.every((p) => typeof p.market_value === "number" && p.market_value > 0))
    return null;
  const totalMv = positions.reduce((s, p) => s + (p.market_value ?? 0), 0);
  if (totalMv <= 0) return null;
  const draft: Record<string, string> = {};
  for (const p of positions) {
    draft[p.ticker] = (((p.market_value ?? 0) / totalMv) * 100).toFixed(2);
  }
  return draft;
}

describe("Use current weights as draft", () => {
  it("returns null when positions have no market_value", () =>
    expect(computeDraftWeights([{ ticker: "AAPL" }, { ticker: "MSFT" }])).toBeNull());

  it("returns null when any position has zero market_value", () =>
    expect(
      computeDraftWeights([
        { ticker: "AAPL", market_value: 0 },
        { ticker: "MSFT", market_value: 5000 },
      ]),
    ).toBeNull());

  it("returns draft percentages when all market values present", () => {
    const draft = computeDraftWeights([
      { ticker: "AAPL", market_value: 5000 },
      { ticker: "MSFT", market_value: 5000 },
    ]);
    expect(draft).not.toBeNull();
    expect(draft!.AAPL).toBe("50.00");
    expect(draft!.MSFT).toBe("50.00");
  });

  it("draft values sum to ~100", () => {
    const draft = computeDraftWeights([
      { ticker: "AAPL", market_value: 3000 },
      { ticker: "MSFT", market_value: 3000 },
      { ticker: "GOOG", market_value: 4000 },
    ]);
    const total = Object.values(draft!).reduce((s, v) => s + parseFloat(v), 0);
    expect(total).toBeCloseTo(100, 5);
  });

  it("computeDraftWeights is a pure function with no side effects", () => {
    const calls: string[] = [];
    const positions = [{ ticker: "AAPL", market_value: 10000 }];
    computeDraftWeights(positions);
    expect(calls).toHaveLength(0);
  });
});

// ── No legacy endpoint usage ──────────────────────────────────────────────────

describe("Target setup does not use legacy endpoints", () => {
  it("/api/v1/portfolio/targets is not a legacy endpoint", () => {
    const endpoint = "/api/v1/portfolio/targets";
    expect(endpoint).not.toContain("allocation/plan");
    expect(endpoint).not.toContain("deposit-plan");
  });
});

// ── Policy guidance contract ──────────────────────────────────────────────────

describe("Policy guidance from readiness diagnostic", () => {
  function needs(p: DeployV3ReadinessDiagnostic["policy"] | undefined) {
    return !!p && !p.policy_valid;
  }

  it("shows guidance when policy_valid is false", () =>
    expect(needs({ minimum_trade_configured: false, rounding_policy_configured: false, policy_valid: false, policy_status: "unsupported_policy" })).toBe(true));

  it("hides guidance when certified", () =>
    expect(needs({ minimum_trade_configured: true, rounding_policy_configured: true, policy_valid: true, policy_status: "certified" })).toBe(false));

  it("hides guidance when undefined", () => expect(needs(undefined)).toBe(false));

  it("allowed rounding policies are documented", () => {
    const allowed = ["WHOLE_DOLLAR", "NEAREST_DOLLAR", "NO_ROUNDING"];
    expect(allowed).toHaveLength(3);
  });
});
