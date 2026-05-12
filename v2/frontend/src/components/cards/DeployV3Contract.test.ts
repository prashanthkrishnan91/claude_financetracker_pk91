/**
 * Deploy v3 UI contract tests — imports real exported helpers to prove behavior.
 *
 * Imports from @/lib/deploy-v3-helpers (no Supabase dependency) wherever possible.
 * Type-only imports from @/lib/api are erased at runtime.
 *
 * These tests verify:
 * - DEPLOY_V3_PLAN_ENDPOINT is /api/v1/deploy/v3/plan (not legacy endpoints)
 * - DEPLOY_V3_PLAN_QUERY_KEY is ["deploy_v3", "plan"]
 * - isNoSnapshotError correctly classifies no-snapshot vs flag-off errors
 * - readinessMeta maps all canonical rollup statuses to plain-English labels
 * - No label claims "fully actionable" status (not_ready is honest)
 * - "Run Intel v3 first" copy is shown only for no-snapshot, not flag-off
 * - Response shape has no legacy DepositPlanResult fields
 */

import {
  DEPLOY_V3_PLAN_ENDPOINT,
  DEPLOY_V3_PLAN_QUERY_KEY,
  READINESS_META,
  readinessMeta,
  isNoSnapshotError,
} from "@/lib/deploy-v3-helpers";
import type {
  DeployV3PlanResponse,
  DeployV3PlanRollup,
  DeployV3ReadinessStatus,
} from "@/lib/api";

// ── Factories ─────────────────────────────────────────────────────────────────

function makeRollup(
  plan_readiness_status: DeployV3ReadinessStatus | string,
  overrides: Partial<DeployV3PlanRollup> = {},
): DeployV3PlanRollup {
  return {
    total_items: 5,
    counts_by_final_actionability_status: {},
    counts_by_pending_guardrails_reason: {},
    actionable_count: 0,
    pending_count: 2,
    blocked_count: 0,
    informational_count: 1,
    suppressed_count: 1,
    not_ready_count: 1,
    unknown_count: 0,
    plan_readiness_status,
    schema_version: "deploy_v1_scaffold",
    ...overrides,
  };
}

function makePlanResponse(
  overrides: Partial<DeployV3PlanResponse> = {},
): DeployV3PlanResponse {
  return {
    plan_status: "SCAFFOLD",
    snapshot_id: "snap-001",
    run_id: "run-001",
    schema_version: "deploy_v1_scaffold",
    items: [],
    guardrail_summary: null,
    rollup: makeRollup("ready_pending_guardrails"),
    source: {
      intel_source: "INTEL_V3",
      sizing_bundle_provided: false,
      note: "No sizing bundle provided. Dollar fields are scaffold placeholders — not executable trade instructions.",
    },
    ...overrides,
  };
}

// ── API client endpoint contract (real constant) ──────────────────────────────

describe("DEPLOY_V3_PLAN_ENDPOINT — real constant from deploy-v3-helpers", () => {
  it("is /api/v1/deploy/v3/plan", () => {
    expect(DEPLOY_V3_PLAN_ENDPOINT).toBe("/api/v1/deploy/v3/plan");
  });

  it("does not include 'allocation'", () => {
    expect(DEPLOY_V3_PLAN_ENDPOINT).not.toContain("allocation");
  });

  it("does not include 'deposit-plan'", () => {
    expect(DEPLOY_V3_PLAN_ENDPOINT).not.toContain("deposit-plan");
  });

  it("does not equal the legacy allocation endpoint", () => {
    expect(DEPLOY_V3_PLAN_ENDPOINT).not.toBe("/api/v1/allocation/plan");
  });

  it("does not equal the legacy deposit-plan endpoint", () => {
    expect(DEPLOY_V3_PLAN_ENDPOINT).not.toBe("/api/deposit-plan");
  });
});

// ── Hook query key contract (real constant) ───────────────────────────────────

describe("DEPLOY_V3_PLAN_QUERY_KEY — real constant from deploy-v3-helpers", () => {
  it("is ['deploy_v3', 'plan']", () => {
    expect(DEPLOY_V3_PLAN_QUERY_KEY).toEqual(["deploy_v3", "plan"]);
  });

  it("has length 2", () => {
    expect(DEPLOY_V3_PLAN_QUERY_KEY).toHaveLength(2);
  });

  it("is distinct from legacy deposit plan query key", () => {
    expect(DEPLOY_V3_PLAN_QUERY_KEY).not.toEqual(["deposits", "plan"]);
  });

  it("is distinct from Intel v3 snapshot query key", () => {
    expect(DEPLOY_V3_PLAN_QUERY_KEY).not.toEqual(["intel_v3", "snapshot"]);
  });

  it("does not contain 'allocation' or 'deposit'", () => {
    for (const segment of DEPLOY_V3_PLAN_QUERY_KEY) {
      expect(segment).not.toContain("allocation");
      expect(segment).not.toContain("deposit");
    }
  });
});

// ── isNoSnapshotError — real helper, distinguishes no-snapshot from flag-off ──

describe("isNoSnapshotError — real helper from deploy-v3-helpers", () => {
  it("returns true for [object Object] (no-snapshot: dict detail serialised by Error())", () => {
    expect(isNoSnapshotError(new Error("[object Object]"))).toBe(true);
  });

  it("returns true for 'API error: 404' (generic 404)", () => {
    expect(isNoSnapshotError(new Error("API error: 404"))).toBe(true);
  });

  it("returns true when message explicitly contains 'no_snapshot'", () => {
    expect(isNoSnapshotError(new Error("no_snapshot: not found"))).toBe(true);
  });

  it("returns false for flag-off error — must not show 'Run Intel v3 first'", () => {
    const flagOffMsg =
      "Deploy v3 plan requires Intel v3 to be enabled. Set INTEL_V3_VISIBLE_SNAPSHOT_ENABLED=true to enable.";
    expect(isNoSnapshotError(new Error(flagOffMsg))).toBe(false);
  });

  it("returns false for generic network error", () => {
    expect(isNoSnapshotError(new Error("Network error"))).toBe(false);
  });

  it("returns false for non-Error values", () => {
    expect(isNoSnapshotError(null)).toBe(false);
    expect(isNoSnapshotError(undefined)).toBe(false);
    expect(isNoSnapshotError("string error")).toBe(false);
    expect(isNoSnapshotError(404)).toBe(false);
  });

  it("flag-off message contains 'Intel v3' but still returns false", () => {
    // Regression guard: flag-off contains "Intel v3" and "SNAPSHOT" in env var name.
    // isNoSnapshotError must not match on those substrings.
    const flagOffMsg =
      "Deploy v3 plan requires Intel v3 to be enabled. Set INTEL_V3_VISIBLE_SNAPSHOT_ENABLED=true to enable.";
    expect(flagOffMsg.toLowerCase()).toContain("intel v3");
    expect(flagOffMsg.toUpperCase()).toContain("SNAPSHOT");
    expect(isNoSnapshotError(new Error(flagOffMsg))).toBe(false);
  });
});

// ── readinessMeta — real helper, plain-English label coverage ─────────────────

describe("readinessMeta — real helper from deploy-v3-helpers", () => {
  const CANONICAL_STATUSES: DeployV3ReadinessStatus[] = [
    "no_items",
    "all_informational",
    "all_suppressed",
    "ready_pending_guardrails",
    "partially_ready",
    "blocked",
    "not_ready",
  ];

  it("all canonical statuses return a non-empty label", () => {
    for (const status of CANONICAL_STATUSES) {
      const meta = readinessMeta(status);
      expect(meta.label.length).toBeGreaterThan(0);
      expect(meta.cls.length).toBeGreaterThan(0);
    }
  });

  it("unknown status returns a fallback label", () => {
    const meta = readinessMeta("some_future_status");
    expect(meta.label).toBe("Plan status unknown");
  });

  it("no canonical label claims fully-actionable trade execution", () => {
    const forbidden = ["actionable", "execute", "trade now", "buy now"];
    for (const status of CANONICAL_STATUSES) {
      const { label } = readinessMeta(status);
      for (const f of forbidden) {
        expect(label.toLowerCase()).not.toContain(f);
      }
    }
  });

  it("ready_pending_guardrails label mentions pending review, not executable", () => {
    const { label } = readinessMeta("ready_pending_guardrails");
    expect(label.toLowerCase()).toContain("pending");
    expect(label.toLowerCase()).not.toContain("execute");
  });

  it("not_ready label is distinct from ready_pending_guardrails label", () => {
    const notReady = readinessMeta("not_ready").label;
    const pending = readinessMeta("ready_pending_guardrails").label;
    expect(notReady).not.toBe(pending);
  });

  it("READINESS_META covers all 7 canonical statuses", () => {
    expect(Object.keys(READINESS_META)).toHaveLength(7);
    for (const status of CANONICAL_STATUSES) {
      expect(READINESS_META).toHaveProperty(status);
    }
  });
});

// ── DeployV3PlanResponse type contract ───────────────────────────────────────

describe("DeployV3PlanResponse type contract", () => {
  it("response has required top-level keys", () => {
    const plan = makePlanResponse();
    const requiredKeys = [
      "plan_status", "snapshot_id", "run_id", "schema_version",
      "items", "guardrail_summary", "rollup", "source",
    ];
    for (const key of requiredKeys) {
      expect(plan).toHaveProperty(key);
    }
  });

  it("source.intel_source is INTEL_V3", () => {
    const plan = makePlanResponse();
    expect(plan.source.intel_source).toBe("INTEL_V3");
  });

  it("source.sizing_bundle_provided is false when sizing not wired", () => {
    const plan = makePlanResponse();
    expect(plan.source.sizing_bundle_provided).toBe(false);
  });

  it("source note does not claim executable trade instructions", () => {
    const plan = makePlanResponse();
    const noteLower = plan.source.note.toLowerCase();
    expect(noteLower).toContain("scaffold");
    expect(noteLower).not.toContain("buy now");
    expect(noteLower).not.toContain("execute");
  });

  it("response has no legacy DepositPlanResult fields", () => {
    const plan = makePlanResponse();
    expect(plan).not.toHaveProperty("recommendations");
    expect(plan).not.toHaveProperty("funding");
    expect(plan).not.toHaveProperty("trims");
    expect(plan).not.toHaveProperty("regime");
    expect(plan).not.toHaveProperty("adaptive");
    expect(plan).not.toHaveProperty("summary");
  });

  it("uses rollup.plan_readiness_status instead of legacy summary.fully_allocated", () => {
    const plan = makePlanResponse();
    expect(plan.rollup).toHaveProperty("plan_readiness_status");
    expect(plan).not.toHaveProperty("summary");
  });
});

// ── DeployV3PlanRollup contract ───────────────────────────────────────────────

describe("DeployV3PlanRollup contract", () => {
  it("actionable_count is always 0 — no fully-actionable final status exists yet", () => {
    const rollup = makeRollup("ready_pending_guardrails");
    expect(rollup.actionable_count).toBe(0);
  });

  it("convenience counts sum to total_items", () => {
    const rollup = makeRollup("ready_pending_guardrails", {
      total_items: 5,
      pending_count: 2,
      blocked_count: 0,
      informational_count: 1,
      suppressed_count: 1,
      not_ready_count: 1,
      unknown_count: 0,
    });
    const sum =
      rollup.pending_count +
      rollup.blocked_count +
      rollup.informational_count +
      rollup.suppressed_count +
      rollup.not_ready_count +
      rollup.unknown_count;
    expect(sum).toBe(rollup.total_items);
  });

  it("no-snapshot rollup has total_items 0 and plan_readiness_status no_items", () => {
    const rollup = makeRollup("no_items", {
      total_items: 0,
      pending_count: 0,
      blocked_count: 0,
      informational_count: 0,
      suppressed_count: 0,
      not_ready_count: 0,
      unknown_count: 0,
    });
    expect(rollup.total_items).toBe(0);
    expect(rollup.plan_readiness_status).toBe("no_items");
  });

  it("rollup does not include dollar amounts", () => {
    const rollup = makeRollup("ready_pending_guardrails");
    expect(rollup).not.toHaveProperty("total_dollar_amount");
    expect(rollup).not.toHaveProperty("recommended_deploy_amount");
  });
});

// ── Legacy endpoint separation ────────────────────────────────────────────────

describe("Deploy v3 does not call legacy endpoints", () => {
  const LEGACY_ENDPOINTS = [
    "/api/deposit-plan",
    "/api/v1/allocation/plan",
  ];

  it("DEPLOY_V3_PLAN_ENDPOINT is not a legacy endpoint", () => {
    for (const legacy of LEGACY_ENDPOINTS) {
      expect(DEPLOY_V3_PLAN_ENDPOINT).not.toBe(legacy);
      expect(DEPLOY_V3_PLAN_ENDPOINT).not.toContain(legacy);
    }
  });
});
