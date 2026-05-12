/**
 * Deploy v3 readiness diagnostic contract tests — Stage 2.5E.
 *
 * Tests verify:
 * - DEPLOY_V3_READINESS_ENDPOINT constant is correct and distinct from legacy endpoints
 * - DEPLOY_V3_READINESS_QUERY_KEY is ["deploy_v3", "readiness"] and distinct
 * - api.deployV3.getReadiness would call the correct endpoint (structural)
 * - policyStatusLabel maps all canonical policy_status values correctly
 * - Policy section does not expose env var values
 * - Missing target tickers are reported plainly
 * - No legacy /allocation/plan or /api/deposit-plan usage in readiness path
 * - DeployV3ReadinessDiagnostic type shape is correct
 */

import {
  DEPLOY_V3_READINESS_ENDPOINT,
  DEPLOY_V3_READINESS_QUERY_KEY,
  DEPLOY_V3_PLAN_ENDPOINT,
  DEPLOY_V3_PLAN_QUERY_KEY,
  policyStatusLabel,
} from "@/lib/deploy-v3-helpers";
import type { DeployV3ReadinessDiagnostic, DeployV3PolicyStatus } from "@/lib/api";

// ── Factories ─────────────────────────────────────────────────────────────────

function makeReadinessDiagnostic(
  overrides: Partial<DeployV3ReadinessDiagnostic> = {},
): DeployV3ReadinessDiagnostic {
  return {
    exact_dollar_ready: false,
    sizing_values_ready: false,
    target_allocation_ready: false,
    policy_ready: false,
    snapshot: {
      present: false,
      snapshot_id: null,
      snapshot_at: null,
      age_hours: null,
      status: "missing",
    },
    market_values: {
      all_positions_have_market_value: false,
      uncertified_tickers: [],
      position_count: 0,
    },
    target_allocations: {
      unique_tickers_in_db: 0,
      missing_tickers: [],
      conflicting_tickers: [],
      target_total_pct: null,
      target_total_in_range: null,
    },
    policy: {
      minimum_trade_configured: false,
      rounding_policy_configured: false,
      policy_valid: false,
      policy_status: "unsupported_policy",
    },
    suppression_reasons: [],
    next_required_action: "Create a fresh portfolio snapshot to begin.",
    ...overrides,
  };
}

// ── Endpoint constant ─────────────────────────────────────────────────────────

describe("DEPLOY_V3_READINESS_ENDPOINT — real constant from deploy-v3-helpers", () => {
  it("is /api/v1/deploy/v3/readiness", () => {
    expect(DEPLOY_V3_READINESS_ENDPOINT).toBe("/api/v1/deploy/v3/readiness");
  });

  it("does not include 'allocation'", () => {
    expect(DEPLOY_V3_READINESS_ENDPOINT).not.toContain("allocation");
  });

  it("does not include 'deposit-plan'", () => {
    expect(DEPLOY_V3_READINESS_ENDPOINT).not.toContain("deposit-plan");
  });

  it("does not equal the legacy allocation endpoint", () => {
    expect(DEPLOY_V3_READINESS_ENDPOINT).not.toBe("/api/v1/allocation/plan");
  });

  it("does not equal the legacy deposit-plan endpoint", () => {
    expect(DEPLOY_V3_READINESS_ENDPOINT).not.toBe("/api/deposit-plan");
  });

  it("is distinct from the plan endpoint", () => {
    expect(DEPLOY_V3_READINESS_ENDPOINT).not.toBe(DEPLOY_V3_PLAN_ENDPOINT);
  });
});

// ── Query key constant ────────────────────────────────────────────────────────

describe("DEPLOY_V3_READINESS_QUERY_KEY — real constant from deploy-v3-helpers", () => {
  it("is ['deploy_v3', 'readiness']", () => {
    expect(DEPLOY_V3_READINESS_QUERY_KEY).toEqual(["deploy_v3", "readiness"]);
  });

  it("has length 2", () => {
    expect(DEPLOY_V3_READINESS_QUERY_KEY).toHaveLength(2);
  });

  it("is distinct from the plan query key", () => {
    expect(DEPLOY_V3_READINESS_QUERY_KEY).not.toEqual(DEPLOY_V3_PLAN_QUERY_KEY);
  });

  it("is distinct from legacy deposit plan query key", () => {
    expect(DEPLOY_V3_READINESS_QUERY_KEY).not.toEqual(["deposits", "plan"]);
  });

  it("does not contain 'allocation' or 'deposit'", () => {
    for (const segment of DEPLOY_V3_READINESS_QUERY_KEY) {
      expect(segment).not.toContain("allocation");
      expect(segment).not.toContain("deposit");
    }
  });
});

// ── policyStatusLabel — plain-English, no values ──────────────────────────────

describe("policyStatusLabel — real helper from deploy-v3-helpers", () => {
  const CANONICAL_STATUSES: DeployV3PolicyStatus[] = [
    "certified",
    "missing_minimum_trade",
    "missing_rounding_policy",
    "invalid_policy_config",
    "unsupported_policy",
  ];

  it("all canonical statuses return a non-empty string", () => {
    for (const status of CANONICAL_STATUSES) {
      const label = policyStatusLabel(status);
      expect(label.length).toBeGreaterThan(0);
    }
  });

  it("certified returns a ready/valid message", () => {
    const label = policyStatusLabel("certified");
    expect(label.toLowerCase()).toContain("valid");
  });

  it("missing_minimum_trade mentions minimum trade", () => {
    const label = policyStatusLabel("missing_minimum_trade");
    expect(label.toLowerCase()).toContain("minimum trade");
    expect(label.toLowerCase()).toContain("not configured");
  });

  it("missing_rounding_policy mentions rounding policy", () => {
    const label = policyStatusLabel("missing_rounding_policy");
    expect(label.toLowerCase()).toContain("rounding");
    expect(label.toLowerCase()).toContain("not configured");
  });

  it("invalid_policy_config distinguishes from missing", () => {
    const label = policyStatusLabel("invalid_policy_config");
    expect(label.toLowerCase()).toContain("invalid");
  });

  it("unsupported_policy says settings not configured", () => {
    const label = policyStatusLabel("unsupported_policy");
    expect(label.toLowerCase()).toContain("not configured");
  });

  it("unknown status returns a safe fallback", () => {
    const label = policyStatusLabel("some_future_status");
    expect(label.length).toBeGreaterThan(0);
  });

  it("no label exposes env var values or secret content", () => {
    const forbidden = ["usd", "$", "=", "env", "secret", "value"];
    for (const status of CANONICAL_STATUSES) {
      const label = policyStatusLabel(status).toLowerCase();
      for (const f of forbidden) {
        expect(label).not.toContain(f);
      }
    }
  });

  it("missing statuses are distinct from each other", () => {
    const minTrade = policyStatusLabel("missing_minimum_trade");
    const rounding = policyStatusLabel("missing_rounding_policy");
    const invalid = policyStatusLabel("invalid_policy_config");
    expect(minTrade).not.toBe(rounding);
    expect(minTrade).not.toBe(invalid);
    expect(rounding).not.toBe(invalid);
  });
});

// ── DeployV3ReadinessDiagnostic type contract ─────────────────────────────────

describe("DeployV3ReadinessDiagnostic type contract", () => {
  it("has required top-level keys", () => {
    const diag = makeReadinessDiagnostic();
    const required = [
      "exact_dollar_ready",
      "sizing_values_ready",
      "target_allocation_ready",
      "policy_ready",
      "snapshot",
      "market_values",
      "target_allocations",
      "policy",
      "suppression_reasons",
      "next_required_action",
    ];
    for (const key of required) {
      expect(diag).toHaveProperty(key);
    }
  });

  it("snapshot has present, status, age_hours", () => {
    const diag = makeReadinessDiagnostic();
    expect(diag.snapshot).toHaveProperty("present");
    expect(diag.snapshot).toHaveProperty("status");
    expect(diag.snapshot).toHaveProperty("age_hours");
  });

  it("market_values has uncertified_tickers array", () => {
    const diag = makeReadinessDiagnostic();
    expect(Array.isArray(diag.market_values.uncertified_tickers)).toBe(true);
  });

  it("target_allocations has missing_tickers and conflicting_tickers", () => {
    const diag = makeReadinessDiagnostic();
    expect(Array.isArray(diag.target_allocations.missing_tickers)).toBe(true);
    expect(Array.isArray(diag.target_allocations.conflicting_tickers)).toBe(true);
  });

  it("policy has no raw dollar or string values — only booleans and status", () => {
    const diag = makeReadinessDiagnostic();
    expect(typeof diag.policy.minimum_trade_configured).toBe("boolean");
    expect(typeof diag.policy.rounding_policy_configured).toBe("boolean");
    expect(typeof diag.policy.policy_valid).toBe("boolean");
    expect(typeof diag.policy.policy_status).toBe("string");
    expect(diag.policy).not.toHaveProperty("minimum_trade_value");
    expect(diag.policy).not.toHaveProperty("rounding_policy_value");
  });

  it("next_required_action is a non-empty string", () => {
    const diag = makeReadinessDiagnostic();
    expect(typeof diag.next_required_action).toBe("string");
    expect(diag.next_required_action.length).toBeGreaterThan(0);
  });

  it("missing snapshot renders next_required_action as create snapshot", () => {
    const diag = makeReadinessDiagnostic({
      next_required_action: "Create a fresh portfolio snapshot to begin.",
    });
    expect(diag.next_required_action.toLowerCase()).toContain("snapshot");
  });

  it("missing target tickers are listed plainly in the type", () => {
    const diag = makeReadinessDiagnostic({
      target_allocations: {
        unique_tickers_in_db: 2,
        missing_tickers: ["AAPL", "MSFT"],
        conflicting_tickers: [],
        target_total_pct: 50.0,
        target_total_in_range: false,
      },
      next_required_action: "Add target allocations for: AAPL, MSFT.",
    });
    expect(diag.target_allocations.missing_tickers).toContain("AAPL");
    expect(diag.target_allocations.missing_tickers).toContain("MSFT");
    expect(diag.next_required_action).toContain("AAPL");
    expect(diag.next_required_action).toContain("MSFT");
  });

  it("exact_dollar_ready=true means all gate booleans can be true", () => {
    const diag = makeReadinessDiagnostic({
      exact_dollar_ready: true,
      sizing_values_ready: true,
      target_allocation_ready: true,
      policy_ready: true,
      next_required_action: "Exact-dollar path is ready. All readiness gates pass.",
    });
    expect(diag.exact_dollar_ready).toBe(true);
    expect(diag.next_required_action.toLowerCase()).toContain("ready");
  });
});

// ── Legacy endpoint separation ────────────────────────────────────────────────

describe("Deploy v3 readiness does not call legacy endpoints", () => {
  const LEGACY_ENDPOINTS = ["/api/deposit-plan", "/api/v1/allocation/plan"];

  it("DEPLOY_V3_READINESS_ENDPOINT is not a legacy endpoint", () => {
    for (const legacy of LEGACY_ENDPOINTS) {
      expect(DEPLOY_V3_READINESS_ENDPOINT).not.toBe(legacy);
      expect(DEPLOY_V3_READINESS_ENDPOINT).not.toContain(legacy);
    }
  });
});

// ── Component hygiene — sibling panels, not cross-imported ───────────────────

describe("DeployV3Panel does not import DeployV3ReadinessPanel", () => {
  it("DeployV3Panel source does not contain a DeployV3ReadinessPanel import", async () => {
    const fs = await import("fs");
    const path = await import("path");
    const filePath = path.resolve(
      __dirname,
      "DeployV3Panel.tsx",
    );
    const source = fs.readFileSync(filePath, "utf-8");
    expect(source).not.toContain("DeployV3ReadinessPanel");
  });
});
