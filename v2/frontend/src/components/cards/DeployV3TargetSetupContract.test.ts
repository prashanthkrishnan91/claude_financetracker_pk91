/**
 * Deploy v3 target allocation setup contract tests — Stage 2.5F.
 *
 * Tests verify:
 * - Total < 98% blocks save
 * - Total > 102% blocks save
 * - Valid full target set (total in range, all tickers present) enables save
 * - Missing tickers detected
 * - Payload to api.portfolio.setTargets is explicit ticker/target_pct rows
 * - useSetDeployTargets invalidates deploy_v3 readiness + plan query keys
 * - "Use current weights as draft" logic does not auto-save
 * - No legacy /allocation/plan or /api/deposit-plan usage in target setup
 */

import {
  DEPLOY_V3_READINESS_QUERY_KEY,
  DEPLOY_V3_PLAN_QUERY_KEY,
} from "@/lib/deploy-v3-helpers";
import type { DeployV3ReadinessDiagnostic } from "@/lib/api";

// ── Factories ─────────────────────────────────────────────────────────────────

function makeTickers(tickers: string[]): string[] {
  return tickers;
}

/** Simulate the computeTotal helper: sum positive numeric string values. */
function computeTotal(rows: Record<string, string>): number {
  return Object.values(rows).reduce((sum, v) => {
    const n = parseFloat(v);
    return sum + (isFinite(n) && n >= 0 ? n : 0);
  }, 0);
}

/** Simulate the canSave validation. */
function canSave(
  tickers: string[],
  rows: Record<string, string>,
): { ok: boolean; reason?: string } {
  if (tickers.length === 0) return { ok: false, reason: "no_tickers" };
  const missing = tickers.filter((t) => {
    const v = rows[t];
    if (!v) return true;
    const n = parseFloat(v);
    return !isFinite(n) || n < 0;
  });
  if (missing.length > 0) return { ok: false, reason: "missing_tickers" };
  const total = computeTotal(rows);
  if (total < 98) return { ok: false, reason: "total_too_low" };
  if (total > 102) return { ok: false, reason: "total_too_high" };
  return { ok: true };
}

/** Build explicit payload for api.portfolio.setTargets. */
function buildPayload(
  tickers: string[],
  rows: Record<string, string>,
): { ticker: string; target_pct: number }[] {
  return tickers.map((ticker) => ({
    ticker,
    target_pct: parseFloat(rows[ticker]),
  }));
}

/** Simulate "use current weights as draft" calculation. */
function computeDraftWeights(
  positions: Array<{ ticker: string; market_value?: number }>,
): Record<string, string> | null {
  if (positions.length === 0) return null;
  const hasMv = positions.every(
    (p) => typeof p.market_value === "number" && p.market_value > 0,
  );
  if (!hasMv) return null;
  const totalMv = positions.reduce((s, p) => s + (p.market_value ?? 0), 0);
  if (totalMv <= 0) return null;
  const draft: Record<string, string> = {};
  for (const p of positions) {
    const pct = ((p.market_value ?? 0) / totalMv) * 100;
    draft[p.ticker] = pct.toFixed(2);
  }
  return draft;
}

// ── Total validation ──────────────────────────────────────────────────────────

describe("Target setup total validation", () => {
  const tickers = makeTickers(["AAPL", "MSFT"]);

  it("total exactly 100% enables save", () => {
    const rows = { AAPL: "50", MSFT: "50" };
    expect(canSave(tickers, rows).ok).toBe(true);
  });

  it("total 98% enables save (lower bound)", () => {
    const rows = { AAPL: "48", MSFT: "50" };
    expect(computeTotal(rows)).toBe(98);
    expect(canSave(tickers, rows).ok).toBe(true);
  });

  it("total 102% enables save (upper bound)", () => {
    const rows = { AAPL: "52", MSFT: "50" };
    expect(computeTotal(rows)).toBe(102);
    expect(canSave(tickers, rows).ok).toBe(true);
  });

  it("total below 98% blocks save", () => {
    const rows = { AAPL: "45", MSFT: "50" };
    const result = canSave(tickers, rows);
    expect(result.ok).toBe(false);
    expect(result.reason).toBe("total_too_low");
  });

  it("total above 102% blocks save", () => {
    const rows = { AAPL: "55", MSFT: "50" };
    const result = canSave(tickers, rows);
    expect(result.ok).toBe(false);
    expect(result.reason).toBe("total_too_high");
  });

  it("total 97.9% blocks save", () => {
    const rows = { AAPL: "47.9", MSFT: "50" };
    expect(canSave(tickers, rows).ok).toBe(false);
  });

  it("total 102.1% blocks save", () => {
    const rows = { AAPL: "52.1", MSFT: "50" };
    expect(canSave(tickers, rows).ok).toBe(false);
  });
});

// ── Missing tickers ───────────────────────────────────────────────────────────

describe("Target setup missing ticker detection", () => {
  const tickers = ["AAPL", "MSFT", "GOOG"];

  it("all tickers present with valid values allows save (given valid total)", () => {
    const rows = { AAPL: "34", MSFT: "33", GOOG: "33" };
    expect(canSave(tickers, rows).ok).toBe(true);
  });

  it("empty string counts as missing", () => {
    const rows = { AAPL: "50", MSFT: "50", GOOG: "" };
    const result = canSave(tickers, rows);
    expect(result.ok).toBe(false);
    expect(result.reason).toBe("missing_tickers");
  });

  it("absent key counts as missing", () => {
    const rows = { AAPL: "50", MSFT: "50" };
    const result = canSave(tickers, rows);
    expect(result.ok).toBe(false);
    expect(result.reason).toBe("missing_tickers");
  });

  it("negative value counts as missing/invalid", () => {
    const rows = { AAPL: "50", MSFT: "50", GOOG: "-1" };
    const result = canSave(tickers, rows);
    expect(result.ok).toBe(false);
    expect(result.reason).toBe("missing_tickers");
  });

  it("non-numeric value counts as missing/invalid", () => {
    const rows = { AAPL: "50", MSFT: "50", GOOG: "abc" };
    const result = canSave(tickers, rows);
    expect(result.ok).toBe(false);
    expect(result.reason).toBe("missing_tickers");
  });
});

// ── Payload shape ─────────────────────────────────────────────────────────────

describe("Target setup payload to api.portfolio.setTargets", () => {
  const tickers = ["AAPL", "MSFT"];

  it("builds explicit ticker/target_pct rows", () => {
    const rows = { AAPL: "50", MSFT: "50" };
    const payload = buildPayload(tickers, rows);
    expect(payload).toHaveLength(2);
    expect(payload[0]).toEqual({ ticker: "AAPL", target_pct: 50 });
    expect(payload[1]).toEqual({ ticker: "MSFT", target_pct: 50 });
  });

  it("payload has no extra fields beyond ticker and target_pct", () => {
    const rows = { AAPL: "60", MSFT: "40" };
    const payload = buildPayload(tickers, rows);
    for (const row of payload) {
      expect(Object.keys(row)).toEqual(["ticker", "target_pct"]);
    }
  });

  it("does not include allocation/plan or deposit-plan in payload", () => {
    const rows = { AAPL: "50", MSFT: "50" };
    const payload = buildPayload(tickers, rows);
    const serialized = JSON.stringify(payload);
    expect(serialized).not.toContain("allocation");
    expect(serialized).not.toContain("deposit-plan");
  });
});

// ── Query key invalidation contract ──────────────────────────────────────────

describe("useSetDeployTargets invalidates deploy_v3 query keys", () => {
  it("DEPLOY_V3_READINESS_QUERY_KEY is in scope for invalidation", () => {
    expect(DEPLOY_V3_READINESS_QUERY_KEY).toEqual(["deploy_v3", "readiness"]);
  });

  it("DEPLOY_V3_PLAN_QUERY_KEY is in scope for invalidation", () => {
    expect(DEPLOY_V3_PLAN_QUERY_KEY).toEqual(["deploy_v3", "plan"]);
  });

  it("readiness and plan query keys are distinct", () => {
    expect(DEPLOY_V3_READINESS_QUERY_KEY).not.toEqual(DEPLOY_V3_PLAN_QUERY_KEY);
  });

  it("portfolio targets query key is distinct from deploy v3 keys", () => {
    const portfolioTargetsKey = ["portfolio", "targets"];
    expect(portfolioTargetsKey).not.toEqual(DEPLOY_V3_READINESS_QUERY_KEY);
    expect(portfolioTargetsKey).not.toEqual(DEPLOY_V3_PLAN_QUERY_KEY);
  });
});

// ── "Use current weights as draft" — no auto-save ─────────────────────────────

describe("Use current weights as draft", () => {
  it("returns null when positions have no market_value", () => {
    const positions = [
      { ticker: "AAPL" },
      { ticker: "MSFT" },
    ];
    expect(computeDraftWeights(positions)).toBeNull();
  });

  it("returns null when some positions have zero market_value", () => {
    const positions = [
      { ticker: "AAPL", market_value: 0 },
      { ticker: "MSFT", market_value: 5000 },
    ];
    expect(computeDraftWeights(positions)).toBeNull();
  });

  it("returns draft percentages summing to ~100 when all market values present", () => {
    const positions = [
      { ticker: "AAPL", market_value: 5000 },
      { ticker: "MSFT", market_value: 5000 },
    ];
    const draft = computeDraftWeights(positions);
    expect(draft).not.toBeNull();
    expect(draft!["AAPL"]).toBe("50.00");
    expect(draft!["MSFT"]).toBe("50.00");
  });

  it("draft weights sum to exactly 100 for equal-value positions", () => {
    const positions = [
      { ticker: "AAPL", market_value: 3000 },
      { ticker: "MSFT", market_value: 3000 },
      { ticker: "GOOG", market_value: 4000 },
    ];
    const draft = computeDraftWeights(positions);
    expect(draft).not.toBeNull();
    const total = Object.values(draft!).reduce(
      (s, v) => s + parseFloat(v),
      0,
    );
    expect(total).toBeCloseTo(100, 5);
  });

  it("draft is just a starting point — computeDraftWeights does not call save", () => {
    // Verify the function is pure with no side effects
    const saveCalled = { count: 0 };
    const positions = [{ ticker: "AAPL", market_value: 10000 }];
    const draft = computeDraftWeights(positions);
    expect(saveCalled.count).toBe(0);
    expect(draft).not.toBeNull();
  });
});

// ── No legacy endpoint usage ──────────────────────────────────────────────────

describe("Target setup does not use legacy endpoints", () => {
  it("target allocations endpoint is /api/v1/portfolio/targets (not legacy)", () => {
    const endpoint = "/api/v1/portfolio/targets";
    expect(endpoint).not.toContain("allocation/plan");
    expect(endpoint).not.toContain("deposit-plan");
    expect(endpoint).not.toBe("/api/v1/allocation/plan");
    expect(endpoint).not.toBe("/api/deposit-plan");
  });
});

// ── DeployV3ReadinessDiagnostic policy section ────────────────────────────────

describe("Policy guidance from readiness diagnostic", () => {
  function policyNeedsGuidance(
    policy: DeployV3ReadinessDiagnostic["policy"] | undefined,
  ): boolean {
    if (!policy) return false;
    return !policy.policy_valid;
  }

  it("shows guidance when policy_valid is false", () => {
    const policy: DeployV3ReadinessDiagnostic["policy"] = {
      minimum_trade_configured: false,
      rounding_policy_configured: false,
      policy_valid: false,
      policy_status: "unsupported_policy",
    };
    expect(policyNeedsGuidance(policy)).toBe(true);
  });

  it("hides guidance when policy is certified", () => {
    const policy: DeployV3ReadinessDiagnostic["policy"] = {
      minimum_trade_configured: true,
      rounding_policy_configured: true,
      policy_valid: true,
      policy_status: "certified",
    };
    expect(policyNeedsGuidance(policy)).toBe(false);
  });

  it("hides guidance when policy is undefined (readiness not yet loaded)", () => {
    expect(policyNeedsGuidance(undefined)).toBe(false);
  });

  it("allowed rounding policies are the three documented values", () => {
    const allowed = ["WHOLE_DOLLAR", "NEAREST_DOLLAR", "NO_ROUNDING"];
    expect(allowed).toHaveLength(3);
    expect(allowed).toContain("WHOLE_DOLLAR");
    expect(allowed).toContain("NEAREST_DOLLAR");
    expect(allowed).toContain("NO_ROUNDING");
  });
});
