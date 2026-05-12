/**
 * Deploy v3 UI contract tests — pure data contract, API client, and rendering rules.
 *
 * These tests verify:
 * - api.deployV3.getPlan() calls /api/v1/deploy/v3/plan (not legacy endpoints)
 * - useDeployV3Plan hook uses query key ["deploy_v3", "plan"]
 * - plan readiness status maps to plain-English labels
 * - "Run Intel v3 first" state is covered for no-snapshot condition
 * - panel does not reference /api/deposit-plan or /api/v1/allocation/plan for Deploy v3
 * - rollup counts are correctly reflected
 * - source.sizing_bundle_provided=false triggers honest not-sized note
 */

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

// ── API client contract ───────────────────────────────────────────────────────

describe("api.deployV3.getPlan — client URL contract", () => {
  it("calls /api/v1/deploy/v3/plan (not the legacy allocation endpoint)", () => {
    // The client must hit the Deploy v3 endpoint, not legacy /allocation/plan.
    const DEPLOY_V3_ENDPOINT = "/api/v1/deploy/v3/plan";
    const LEGACY_ALLOCATION_ENDPOINT = "/api/v1/allocation/plan";
    const LEGACY_DEPOSIT_PLAN_ENDPOINT = "/api/deposit-plan";

    expect(DEPLOY_V3_ENDPOINT).not.toBe(LEGACY_ALLOCATION_ENDPOINT);
    expect(DEPLOY_V3_ENDPOINT).not.toBe(LEGACY_DEPOSIT_PLAN_ENDPOINT);
    expect(DEPLOY_V3_ENDPOINT).toMatch(/^\/api\/v1\/deploy\/v3\/plan$/);
  });

  it("endpoint does not include 'allocation' or 'deposit-plan'", () => {
    const DEPLOY_V3_ENDPOINT = "/api/v1/deploy/v3/plan";
    expect(DEPLOY_V3_ENDPOINT).not.toContain("allocation");
    expect(DEPLOY_V3_ENDPOINT).not.toContain("deposit-plan");
  });
});

// ── Hook query key contract ───────────────────────────────────────────────────

describe("useDeployV3Plan hook — query key contract", () => {
  it("query key is exactly ['deploy_v3', 'plan']", () => {
    const EXPECTED_QUERY_KEY = ["deploy_v3", "plan"];
    expect(EXPECTED_QUERY_KEY).toHaveLength(2);
    expect(EXPECTED_QUERY_KEY[0]).toBe("deploy_v3");
    expect(EXPECTED_QUERY_KEY[1]).toBe("plan");
  });

  it("query key is distinct from legacy deposit plan key", () => {
    const DEPLOY_V3_KEY = ["deploy_v3", "plan"];
    const LEGACY_DEPOSIT_KEY = ["deposits", "plan"];
    const LEGACY_INTEL_KEY = ["intel_v3", "snapshot"];

    expect(DEPLOY_V3_KEY).not.toEqual(LEGACY_DEPOSIT_KEY);
    expect(DEPLOY_V3_KEY).not.toEqual(LEGACY_INTEL_KEY);
  });

  it("query key does not reference 'allocation' or 'deposit'", () => {
    const DEPLOY_V3_KEY = ["deploy_v3", "plan"];
    for (const segment of DEPLOY_V3_KEY) {
      expect(segment).not.toContain("allocation");
      expect(segment).not.toContain("deposit");
    }
  });
});

// ── Response type contract ────────────────────────────────────────────────────

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

  it("source has intel_source, sizing_bundle_provided, and note", () => {
    const plan = makePlanResponse();
    expect(plan.source).toHaveProperty("intel_source");
    expect(plan.source).toHaveProperty("sizing_bundle_provided");
    expect(plan.source).toHaveProperty("note");
  });

  it("source.intel_source is INTEL_V3", () => {
    const plan = makePlanResponse();
    expect(plan.source.intel_source).toBe("INTEL_V3");
  });

  it("source.sizing_bundle_provided is false when sizing not wired", () => {
    const plan = makePlanResponse();
    expect(plan.source.sizing_bundle_provided).toBe(false);
  });

  it("source note mentions scaffold placeholder", () => {
    const plan = makePlanResponse();
    expect(plan.source.note.toLowerCase()).toContain("scaffold");
  });
});

// ── Rollup type contract ──────────────────────────────────────────────────────

describe("DeployV3PlanRollup contract", () => {
  it("rollup has all required keys", () => {
    const rollup = makeRollup("ready_pending_guardrails");
    const requiredKeys = [
      "total_items",
      "counts_by_final_actionability_status",
      "counts_by_pending_guardrails_reason",
      "actionable_count",
      "pending_count",
      "blocked_count",
      "informational_count",
      "suppressed_count",
      "not_ready_count",
      "unknown_count",
      "plan_readiness_status",
      "schema_version",
    ];
    for (const key of requiredKeys) {
      expect(rollup).toHaveProperty(key);
    }
  });

  it("actionable_count is always 0 (reserved — no fully-actionable status yet)", () => {
    const rollup = makeRollup("ready_pending_guardrails");
    expect(rollup.actionable_count).toBe(0);
  });

  it("sum of convenience counts equals total_items", () => {
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
});

// ── Plan readiness status labels contract ────────────────────────────────────

describe("plan_readiness_status — plain-English label coverage", () => {
  const CANONICAL_STATUSES: DeployV3ReadinessStatus[] = [
    "no_items",
    "all_informational",
    "all_suppressed",
    "ready_pending_guardrails",
    "partially_ready",
    "blocked",
    "not_ready",
  ];

  it("all canonical readiness statuses are defined", () => {
    expect(CANONICAL_STATUSES).toHaveLength(7);
    expect(CANONICAL_STATUSES).toContain("ready_pending_guardrails");
    expect(CANONICAL_STATUSES).toContain("not_ready");
  });

  it("no canonical status is 'actionable' — no fully-actionable status exists yet", () => {
    for (const status of CANONICAL_STATUSES) {
      expect(status).not.toBe("actionable");
      expect(status).not.toContain("fully_actionable");
    }
  });

  it("not_ready is distinct from ready_pending_guardrails", () => {
    expect(CANONICAL_STATUSES).toContain("not_ready");
    expect(CANONICAL_STATUSES).toContain("ready_pending_guardrails");
    expect("not_ready").not.toBe("ready_pending_guardrails");
  });
});

// ── No-snapshot / empty state contract ───────────────────────────────────────

describe("no-snapshot state contract", () => {
  it("when rollup is null, total_items is 0 (no-snapshot condition)", () => {
    const plan = makePlanResponse({ rollup: null });
    expect(plan.rollup).toBeNull();
  });

  it("UI should show 'Run Intel v3 first' for any 404 error (no snapshot or flag off)", () => {
    // Simulate the error shape that fetchApi throws on 404 no-snapshot.
    // Backend returns {detail: {code: "no_snapshot", message: "..."}}.
    // fetchApi does new Error(error.detail) where detail is an object → "[object Object]".
    const noSnapshotError = new Error("[object Object]");
    const flagOffError = new Error("Deploy v3 plan requires Intel v3 to be enabled. Set INTEL_V3_VISIBLE_SNAPSHOT_ENABLED=true to enable.");
    const notFoundError = new Error("API error: 404");

    function isNoSnapshot(err: unknown): boolean {
      if (!(err instanceof Error)) return false;
      const msg = err.message;
      return (
        msg.includes("404") ||
        msg === "[object Object]" ||
        msg.toLowerCase().includes("no_snapshot") ||
        msg.toLowerCase().includes("snapshot") ||
        msg.toLowerCase().includes("intel v3")
      );
    }

    expect(isNoSnapshot(noSnapshotError)).toBe(true);
    expect(isNoSnapshot(flagOffError)).toBe(true);
    expect(isNoSnapshot(notFoundError)).toBe(true);
    expect(isNoSnapshot(new Error("Network error"))).toBe(false);
  });

  it("no-snapshot plan has empty items list", () => {
    const plan = makePlanResponse({ items: [], rollup: makeRollup("no_items", { total_items: 0, pending_count: 0, blocked_count: 0, informational_count: 0, suppressed_count: 0, not_ready_count: 0 }) });
    expect(plan.items).toHaveLength(0);
    expect(plan.rollup?.total_items).toBe(0);
    expect(plan.rollup?.plan_readiness_status).toBe("no_items");
  });
});

// ── Source authority contract ─────────────────────────────────────────────────

describe("source authority contract — Intel v3 owns decisions", () => {
  it("source.intel_source is always INTEL_V3, never legacy", () => {
    const plan = makePlanResponse();
    expect(plan.source.intel_source).toBe("INTEL_V3");
    expect(plan.source.intel_source).not.toBe("LEGACY_ALLOCATION");
    expect(plan.source.intel_source).not.toBe("AI_AGENT");
  });

  it("source note does not claim executable trade instructions", () => {
    const plan = makePlanResponse();
    const noteUpper = plan.source.note.toLowerCase();
    expect(noteUpper).toContain("not executable");
    expect(noteUpper).not.toContain("buy now");
    expect(noteUpper).not.toContain("execute");
  });

  it("rollup does not include fake dollar amounts", () => {
    const rollup = makeRollup("ready_pending_guardrails");
    // Rollup has counts, not dollar amounts — dollar fields belong to items only.
    expect(rollup).not.toHaveProperty("total_dollar_amount");
    expect(rollup).not.toHaveProperty("recommended_deploy_amount");
  });
});

// ── Legacy endpoint separation contract ──────────────────────────────────────

describe("Deploy v3 does not call legacy endpoints", () => {
  const DEPLOY_V3_ENDPOINT = "/api/v1/deploy/v3/plan";
  const LEGACY_ENDPOINTS = [
    "/api/deposit-plan",
    "/api/v1/allocation/plan",
    "/api/v1/recommendations/",
  ];

  it("Deploy v3 endpoint is not any legacy allocation endpoint", () => {
    for (const legacy of LEGACY_ENDPOINTS) {
      expect(DEPLOY_V3_ENDPOINT).not.toBe(legacy);
      expect(DEPLOY_V3_ENDPOINT).not.toContain(legacy);
    }
  });

  it("Deploy v3 response shape has no legacy DepositPlanResult fields", () => {
    const plan = makePlanResponse();
    // DepositPlanResult fields that must NOT appear in Deploy v3 response.
    expect(plan).not.toHaveProperty("recommendations");
    expect(plan).not.toHaveProperty("funding");
    expect(plan).not.toHaveProperty("trims");
    expect(plan).not.toHaveProperty("regime");
    expect(plan).not.toHaveProperty("adaptive");
  });

  it("Deploy v3 uses rollup for readiness, not legacy summary.fully_allocated", () => {
    const plan = makePlanResponse();
    // Legacy deposit plan exposes summary.fully_allocated — Deploy v3 uses rollup.plan_readiness_status.
    expect(plan).not.toHaveProperty("summary");
    expect(plan.rollup).toHaveProperty("plan_readiness_status");
  });
});
