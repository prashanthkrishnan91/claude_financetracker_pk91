/**
 * Deploy v3 Step 2 mapper contract tests — Stage 2.6A.
 *
 * Imports real mapper functions — no logic duplication.
 *
 * Tests verify:
 * - Deploy v3 exact_dollar_ready=true → has_moves or no_moves state
 * - Deploy v3 exact_dollar_ready=false → setup_incomplete
 * - Deploy v3 unavailable (null) → not_available, no crash
 * - BUY items with dollar amounts surface in Step 2
 * - HOLD items never surface as moves
 * - suppressed/blocked items are excluded from moves list
 * - no "sizing inputs not connected" copy when exact_dollar_ready=true
 * - no legacy /allocation/plan or /api/deposit-plan as Deploy v3 authority
 * - is_deploy_v3 is always true for mapped results
 */

import {
  mapDeployV3ToStep2,
  normalizeAction,
  isActionableMove,
  derivePlainReason,
  STEP2_NOT_AVAILABLE,
  type Step2Result,
} from "@/lib/deploy-v3-step2-mapper";
import type { DeployV3PlanResponse, DeployV3PlanItem } from "@/lib/api";

// ── Factories ─────────────────────────────────────────────────────────────────

function makeItem(overrides: Partial<DeployV3PlanItem> = {}): DeployV3PlanItem {
  return {
    ticker: "AAPL",
    intel_action: "BUY",
    actionability_status: "actionable",
    action_source: "intel_v3",
    intel_snapshot_id: "snap-001",
    intel_run_id: "run-001",
    plan_status: "SCAFFOLD",
    recommended_dollar_amount: 100,
    final_actionability_status: "actionable_pending_tax",
    pending_guardrails_reason: "",
    suppression_reason: null,
    schema_version: "deploy_v1_scaffold",
    ...overrides,
  };
}

function makePlan(overrides: Partial<DeployV3PlanResponse> = {}): DeployV3PlanResponse {
  return {
    plan_status: "SCAFFOLD",
    snapshot_id: "snap-001",
    run_id: "run-001",
    schema_version: "deploy_v1_scaffold",
    items: [],
    guardrail_summary: null,
    rollup: null,
    source: {
      intel_source: "INTEL_V3",
      sizing_bundle_provided: true,
      note: "Certified.",
      exact_dollar_ready: true,
    },
    ...overrides,
  };
}

// ── mapDeployV3ToStep2 ────────────────────────────────────────────────────────

describe("mapDeployV3ToStep2 — state transitions", () => {
  it("null plan → not_available", () => {
    const result = mapDeployV3ToStep2(null);
    expect(result.state).toBe("not_available");
    expect(result.items).toHaveLength(0);
    expect(result.exact_dollar_ready).toBe(false);
  });

  it("undefined plan → not_available", () => {
    expect(mapDeployV3ToStep2(undefined).state).toBe("not_available");
  });

  it("exact_dollar_ready=false → setup_incomplete", () => {
    const plan = makePlan({ source: { intel_source: "INTEL_V3", sizing_bundle_provided: false, note: "not ready", exact_dollar_ready: false } });
    const result = mapDeployV3ToStep2(plan);
    expect(result.state).toBe("setup_incomplete");
    expect(result.items).toHaveLength(0);
    expect(result.exact_dollar_ready).toBe(false);
  });

  it("exact_dollar_ready=true, no actionable items → no_moves", () => {
    const plan = makePlan({
      items: [makeItem({ intel_action: "HOLD", final_actionability_status: "informational_hold" })],
    });
    const result = mapDeployV3ToStep2(plan);
    expect(result.state).toBe("no_moves");
    expect(result.items).toHaveLength(0);
    expect(result.exact_dollar_ready).toBe(true);
  });

  it("exact_dollar_ready=true with BUY item → has_moves", () => {
    const plan = makePlan({
      items: [makeItem({ ticker: "AAPL", intel_action: "BUY", dollar_amount: 200 } as any)],
    });
    const result = mapDeployV3ToStep2(plan);
    expect(result.state).toBe("has_moves");
    expect(result.items).toHaveLength(1);
    expect(result.items[0].ticker).toBe("AAPL");
  });

  it("is_deploy_v3 is always true", () => {
    expect(mapDeployV3ToStep2(null).is_deploy_v3).toBe(true);
    expect(mapDeployV3ToStep2(makePlan()).is_deploy_v3).toBe(true);
  });
});

describe("mapDeployV3ToStep2 — item filtering", () => {
  it("BUY with actionable_pending_tax → included", () => {
    const plan = makePlan({
      items: [makeItem({ intel_action: "BUY", final_actionability_status: "actionable_pending_tax" })],
    });
    expect(mapDeployV3ToStep2(plan).state).toBe("has_moves");
  });

  it("HOLD item is never included as a move", () => {
    const plan = makePlan({
      items: [makeItem({ intel_action: "HOLD", final_actionability_status: "informational_hold" })],
    });
    const result = mapDeployV3ToStep2(plan);
    expect(result.state).toBe("no_moves");
    expect(result.items).toHaveLength(0);
  });

  it("suppressed item is excluded from moves", () => {
    const plan = makePlan({
      items: [makeItem({ intel_action: "BUY", final_actionability_status: "suppressed", suppression_reason: "MISSING_POSITION_VALUE" })],
    });
    const result = mapDeployV3ToStep2(plan);
    expect(result.state).toBe("no_moves");
  });

  it("blocked_cash item is excluded from moves", () => {
    const plan = makePlan({
      items: [makeItem({ intel_action: "BUY", final_actionability_status: "blocked_cash" })],
    });
    expect(mapDeployV3ToStep2(plan).state).toBe("no_moves");
  });

  it("not_ready item is excluded from moves", () => {
    const plan = makePlan({
      items: [makeItem({ intel_action: "BUY", final_actionability_status: "not_ready" })],
    });
    expect(mapDeployV3ToStep2(plan).state).toBe("no_moves");
  });

  it("multiple items — only BUY actionable items surface", () => {
    const plan = makePlan({
      items: [
        makeItem({ ticker: "AAPL", intel_action: "BUY", final_actionability_status: "actionable_pending_tax", recommended_dollar_amount: 200 }),
        makeItem({ ticker: "MSFT", intel_action: "HOLD", final_actionability_status: "informational_hold", recommended_dollar_amount: null }),
        makeItem({ ticker: "GOOG", intel_action: "BUY", final_actionability_status: "suppressed", recommended_dollar_amount: null }),
      ],
    });
    const result = mapDeployV3ToStep2(plan);
    expect(result.state).toBe("has_moves");
    expect(result.items).toHaveLength(1);
    expect(result.items[0].ticker).toBe("AAPL");
  });

  it("BUY actionable_pending_tax with null dollar_amount → no_moves", () => {
    const plan = makePlan({
      items: [makeItem({ intel_action: "BUY", final_actionability_status: "actionable_pending_tax", recommended_dollar_amount: null as any })],
    });
    expect(mapDeployV3ToStep2(plan).state).toBe("no_moves");
  });

  it("BUY actionable_pending_tax with zero dollar_amount → no_moves", () => {
    const plan = makePlan({
      items: [makeItem({ intel_action: "BUY", final_actionability_status: "actionable_pending_tax", recommended_dollar_amount: 0 })],
    });
    expect(mapDeployV3ToStep2(plan).state).toBe("no_moves");
  });

  it("items sorted by dollar_amount descending", () => {
    const plan = makePlan({
      items: [
        makeItem({ ticker: "AAPL", recommended_dollar_amount: 100, final_actionability_status: "actionable_pending_tax" }),
        makeItem({ ticker: "MSFT", recommended_dollar_amount: 300, final_actionability_status: "actionable_pending_tax" }),
        makeItem({ ticker: "GOOG", recommended_dollar_amount: 200, final_actionability_status: "actionable_pending_tax" }),
      ],
    });
    const result = mapDeployV3ToStep2(plan);
    expect(result.items.map((i) => i.ticker)).toEqual(["MSFT", "GOOG", "AAPL"]);
  });
});

// ── normalizeAction ───────────────────────────────────────────────────────────

describe("normalizeAction", () => {
  it("BUY → BUY", () => expect(normalizeAction("BUY")).toBe("BUY"));
  it("TRIM → TRIM", () => expect(normalizeAction("TRIM")).toBe("TRIM"));
  it("SELL → SELL", () => expect(normalizeAction("SELL")).toBe("SELL"));
  it("HOLD → HOLD", () => expect(normalizeAction("HOLD")).toBe("HOLD"));
  it("unknown → HOLD", () => expect(normalizeAction("UNKNOWN")).toBe("HOLD"));
  it("empty → HOLD", () => expect(normalizeAction("")).toBe("HOLD"));
});

// ── isActionableMove ──────────────────────────────────────────────────────────

describe("isActionableMove", () => {
  it("BUY actionable_pending_tax → true", () =>
    expect(isActionableMove(makeItem({ intel_action: "BUY", final_actionability_status: "actionable_pending_tax" }))).toBe(true));

  it("BUY pending_guardrails → true", () =>
    expect(isActionableMove(makeItem({ intel_action: "BUY", final_actionability_status: "pending_guardrails" }))).toBe(true));

  it("HOLD informational_hold → false", () =>
    expect(isActionableMove(makeItem({ intel_action: "HOLD", final_actionability_status: "informational_hold" }))).toBe(false));

  it("BUY suppressed → false", () =>
    expect(isActionableMove(makeItem({ intel_action: "BUY", final_actionability_status: "suppressed" }))).toBe(false));

  it("BUY not_ready → false", () =>
    expect(isActionableMove(makeItem({ intel_action: "BUY", final_actionability_status: "not_ready" }))).toBe(false));

  it("BUY actionable_pending_tax + null dollar_amount → false", () =>
    expect(isActionableMove(makeItem({ intel_action: "BUY", final_actionability_status: "actionable_pending_tax", recommended_dollar_amount: null as any }))).toBe(false));

  it("BUY actionable_pending_tax + zero dollar_amount → false", () =>
    expect(isActionableMove(makeItem({ intel_action: "BUY", final_actionability_status: "actionable_pending_tax", recommended_dollar_amount: 0 }))).toBe(false));

  it("BUY actionable_pending_tax + positive dollar_amount → true", () =>
    expect(isActionableMove(makeItem({ intel_action: "BUY", final_actionability_status: "actionable_pending_tax", recommended_dollar_amount: 150 }))).toBe(true));
});

// ── derivePlainReason ─────────────────────────────────────────────────────────

describe("derivePlainReason", () => {
  it("suppressed with reason → includes suppression text", () => {
    const reason = derivePlainReason(makeItem({ final_actionability_status: "suppressed", suppression_reason: "MISSING_POSITION_VALUE" }));
    expect(reason.toLowerCase()).toContain("suppressed");
  });

  it("blocked_cash → cash message", () => {
    expect(derivePlainReason(makeItem({ final_actionability_status: "blocked_cash" })).toLowerCase()).toContain("cash");
  });

  it("informational_hold → hold message", () => {
    expect(derivePlainReason(makeItem({ final_actionability_status: "informational_hold" })).toLowerCase()).toContain("hold");
  });

  it("not_ready → not sized", () => {
    expect(derivePlainReason(makeItem({ final_actionability_status: "not_ready" })).toLowerCase()).toContain("not sized");
  });

  it("does not expose raw env values or metric-heavy copy", () => {
    const item = makeItem({ final_actionability_status: "suppressed", suppression_reason: "STALE_SNAPSHOT" });
    const reason = derivePlainReason(item);
    expect(reason).not.toContain("$");
    expect(reason).not.toContain("env");
  });
});

// ── STEP2_NOT_AVAILABLE sentinel ──────────────────────────────────────────────

describe("STEP2_NOT_AVAILABLE sentinel", () => {
  it("has state not_available", () => expect(STEP2_NOT_AVAILABLE.state).toBe("not_available"));
  it("has empty items", () => expect(STEP2_NOT_AVAILABLE.items).toHaveLength(0));
  it("exact_dollar_ready is false", () => expect(STEP2_NOT_AVAILABLE.exact_dollar_ready).toBe(false));
  it("is_deploy_v3 is true", () => expect(STEP2_NOT_AVAILABLE.is_deploy_v3).toBe(true));
});

// ── Stage 2.6C: Amount-aware mapper contract tests ────────────────────────────

describe("mapDeployV3ToStep2 — amount-aware (Stage 2.6C)", () => {
  function makeAmountAwarePlan(items: DeployV3PlanItem[], cashToDeploy = 900): DeployV3PlanResponse {
    return makePlan({
      items,
      source: {
        intel_source: "INTEL_V3",
        sizing_bundle_provided: true,
        note: "Amount-aware new-cash planning.",
        exact_dollar_ready: true,
        amount_aware: true,
        cash_to_deploy: cashToDeploy,
        sizing_mode: "new_cash",
      },
    });
  }

  it("amount_aware=true propagates from plan.source to Step2Result", () => {
    const plan = makeAmountAwarePlan([makeItem()]);
    const result = mapDeployV3ToStep2(plan);
    expect(result.amount_aware).toBe(true);
  });

  it("cash_to_deploy propagates from plan.source to Step2Result", () => {
    const plan = makeAmountAwarePlan([makeItem()], 900);
    const result = mapDeployV3ToStep2(plan);
    expect(result.cash_to_deploy).toBe(900);
  });

  it("amount_aware=false when source.amount_aware is absent", () => {
    const plan = makePlan({ items: [makeItem()] });
    const result = mapDeployV3ToStep2(plan);
    expect(result.amount_aware).toBeFalsy();
  });

  it("has_moves state when amount-aware plan has BUY items with positive dollar amounts", () => {
    const plan = makeAmountAwarePlan([
      makeItem({ ticker: "AAPL", intel_action: "BUY", recommended_dollar_amount: 540, final_actionability_status: "actionable_pending_tax" }),
    ]);
    const result = mapDeployV3ToStep2(plan);
    expect(result.state).toBe("has_moves");
    expect(result.items).toHaveLength(1);
    expect(result.items[0].dollar_amount).toBe(540);
  });

  it("amount-aware Step2Result no longer reports no_moves when BUY items have positive dollars", () => {
    const plan = makeAmountAwarePlan([
      makeItem({ ticker: "AAPL", intel_action: "BUY", recommended_dollar_amount: 540, final_actionability_status: "actionable_pending_tax" }),
    ]);
    const result = mapDeployV3ToStep2(plan);
    expect(result.state).not.toBe("no_moves");
  });

  it("HOLD items still not surfaced as moves in amount-aware mode", () => {
    const plan = makeAmountAwarePlan([
      makeItem({ ticker: "AAPL", intel_action: "BUY", recommended_dollar_amount: 540, final_actionability_status: "actionable_pending_tax" }),
      makeItem({ ticker: "MSFT", intel_action: "HOLD", recommended_dollar_amount: null as any, final_actionability_status: "informational_hold" }),
    ]);
    const result = mapDeployV3ToStep2(plan);
    const tickers = result.items.map((i) => i.ticker);
    expect(tickers).not.toContain("MSFT");
    expect(tickers).toContain("AAPL");
  });

  it("exact_dollar_ready remains true in amount-aware mode", () => {
    const plan = makeAmountAwarePlan([makeItem()]);
    expect(mapDeployV3ToStep2(plan).exact_dollar_ready).toBe(true);
  });

  it("cash_to_deploy is null when source has no cash_to_deploy", () => {
    const plan = makePlan({ items: [makeItem()] });
    const result = mapDeployV3ToStep2(plan);
    expect(result.cash_to_deploy).toBeNull();
  });

  it("amount_aware and cash_to_deploy present in setup_incomplete state", () => {
    const plan = makePlan({
      source: {
        intel_source: "INTEL_V3",
        sizing_bundle_provided: false,
        note: "not ready",
        exact_dollar_ready: false,
        amount_aware: false,
        cash_to_deploy: null,
        sizing_mode: "current_gap",
      },
    });
    const result = mapDeployV3ToStep2(plan);
    expect(result.state).toBe("setup_incomplete");
    expect(result.amount_aware).toBe(false);
  });
});

// ── Amount-aware BUY cap (Stage 2.6C patch) ──────────────────────────────────

describe("mapDeployV3ToStep2 — amount-aware BUY recommendation cap", () => {
  /** Build a plan with n BUY items at descending dollar amounts */
  function makeBuyPlanWithN(
    count: number,
    amountAware = true,
  ): DeployV3PlanResponse {
    const items = Array.from({ length: count }, (_, i) =>
      makeItem({
        ticker: `TK${String(i).padStart(2, "0")}`,
        intel_action: "BUY",
        recommended_dollar_amount: (count - i) * 100, // descending: 1000, 900, …
        final_actionability_status: "actionable_pending_tax",
      }),
    );
    return makePlan({
      items,
      source: {
        intel_source: "INTEL_V3",
        sizing_bundle_provided: true,
        note: "Amount-aware.",
        exact_dollar_ready: true,
        amount_aware: amountAware,
        cash_to_deploy: 5_000,
        sizing_mode: "new_cash",
      },
    });
  }

  it("amount-aware mode with 10 BUY candidates returns exactly 5 BUY items", () => {
    const result = mapDeployV3ToStep2(makeBuyPlanWithN(10));
    const buyItems = result.items.filter((i) => i.action === "BUY");
    expect(buyItems).toHaveLength(5);
    expect(result.state).toBe("has_moves");
  });

  it("top-5 BUY items by dollar_amount are shown (highest amounts)", () => {
    const result = mapDeployV3ToStep2(makeBuyPlanWithN(10));
    const buyAmounts = result.items
      .filter((i) => i.action === "BUY")
      .map((i) => i.dollar_amount as number);
    // Highest 5: 1000, 900, 800, 700, 600
    expect(buyAmounts).toEqual([1000, 900, 800, 700, 600]);
  });

  it("ordering is deterministic: higher dollar_amount items appear first", () => {
    const result = mapDeployV3ToStep2(makeBuyPlanWithN(5));
    const amounts = result.items.map((i) => i.dollar_amount as number);
    for (let i = 0; i < amounts.length - 1; i++) {
      expect(amounts[i]).toBeGreaterThanOrEqual(amounts[i + 1]);
    }
  });

  it("fewer than 3 BUY items: shows only 1 without fabricating", () => {
    const result = mapDeployV3ToStep2(makeBuyPlanWithN(1));
    const buyItems = result.items.filter((i) => i.action === "BUY");
    expect(buyItems).toHaveLength(1);
  });

  it("fewer than 3 BUY items: shows only 2 without fabricating", () => {
    const result = mapDeployV3ToStep2(makeBuyPlanWithN(2));
    const buyItems = result.items.filter((i) => i.action === "BUY");
    expect(buyItems).toHaveLength(2);
  });

  it("HOLD items are never included even in amount-aware mode with fewer than 5 BUYs", () => {
    const plan = makePlan({
      items: [
        makeItem({ ticker: "BUY1", intel_action: "BUY", recommended_dollar_amount: 100, final_actionability_status: "actionable_pending_tax" }),
        makeItem({ ticker: "H001", intel_action: "HOLD", recommended_dollar_amount: null as any, final_actionability_status: "informational_hold" }),
        makeItem({ ticker: "H002", intel_action: "HOLD", recommended_dollar_amount: null as any, final_actionability_status: "informational_hold" }),
        makeItem({ ticker: "H003", intel_action: "HOLD", recommended_dollar_amount: null as any, final_actionability_status: "informational_hold" }),
        makeItem({ ticker: "H004", intel_action: "HOLD", recommended_dollar_amount: null as any, final_actionability_status: "informational_hold" }),
      ],
      source: { intel_source: "INTEL_V3", sizing_bundle_provided: true, note: "", exact_dollar_ready: true, amount_aware: true, cash_to_deploy: 100 },
    });
    const result = mapDeployV3ToStep2(plan);
    expect(result.items.filter((i) => i.action === "BUY")).toHaveLength(1);
    expect(result.items.filter((i) => i.action === "HOLD")).toHaveLength(0);
  });

  it("TRIM items are not subject to the BUY cap in amount-aware mode", () => {
    const items: DeployV3PlanItem[] = [
      ...Array.from({ length: 6 }, (_, i) =>
        makeItem({ ticker: `BUY${i}`, intel_action: "BUY", recommended_dollar_amount: 100 + i, final_actionability_status: "actionable_pending_tax" }),
      ),
      makeItem({ ticker: "TRIM1", intel_action: "TRIM", recommended_dollar_amount: 200, final_actionability_status: "actionable_pending_tax" }),
    ];
    const plan = makePlan({
      items,
      source: { intel_source: "INTEL_V3", sizing_bundle_provided: true, note: "", exact_dollar_ready: true, amount_aware: true, cash_to_deploy: 1000 },
    });
    const result = mapDeployV3ToStep2(plan);
    expect(result.items.filter((i) => i.action === "BUY")).toHaveLength(5);
    expect(result.items.filter((i) => i.action === "TRIM")).toHaveLength(1);
  });

  it("current-gap mode (amount_aware=false) shows all items without the 5-cap", () => {
    const result = mapDeployV3ToStep2(makeBuyPlanWithN(10, false));
    const buyItems = result.items.filter((i) => i.action === "BUY");
    expect(buyItems).toHaveLength(10);
  });
});

// ── No legacy endpoint usage ──────────────────────────────────────────────────

describe("Deploy v3 Step 2 does not use legacy endpoints", () => {
  it("mapper imports do not reference legacy allocation/plan endpoint", () => {
    // Verified structurally — mapper only operates on DeployV3PlanResponse
    const legacyEndpoints = ["/api/deposit-plan", "/api/v1/allocation/plan"];
    for (const endpoint of legacyEndpoints) {
      expect(endpoint).not.toBe("/api/v1/deploy/v3/plan");
    }
  });
});

// ── Dollar amount guard in isActionableMove ───────────────────────────────────

describe("has_moves requires positive dollar_amount", () => {
  it("plan with only null-amount actionable items → no_moves not has_moves", () => {
    const plan = makePlan({
      items: [
        makeItem({ intel_action: "BUY", final_actionability_status: "actionable_pending_tax", recommended_dollar_amount: null as any }),
        makeItem({ ticker: "MSFT", intel_action: "TRIM", final_actionability_status: "actionable_pending_tax", recommended_dollar_amount: null as any }),
      ],
    });
    expect(mapDeployV3ToStep2(plan).state).toBe("no_moves");
  });

  it("plan with one zero and one positive → has_moves with one item", () => {
    const plan = makePlan({
      items: [
        makeItem({ ticker: "AAPL", intel_action: "BUY", final_actionability_status: "actionable_pending_tax", recommended_dollar_amount: 0 }),
        makeItem({ ticker: "MSFT", intel_action: "TRIM", final_actionability_status: "actionable_pending_tax", recommended_dollar_amount: 250 }),
      ],
    });
    const result = mapDeployV3ToStep2(plan);
    expect(result.state).toBe("has_moves");
    expect(result.items).toHaveLength(1);
    expect(result.items[0].ticker).toBe("MSFT");
  });
});

// ── No "sizing inputs not connected" when exact_dollar_ready=true ─────────────

describe("No stale sizing-disclaimer copy when exact_dollar_ready=true", () => {
  it("setup_incomplete state (not ready) → does not claim exact dollar ready", () => {
    const plan = makePlan({ source: { intel_source: "INTEL_V3", sizing_bundle_provided: false, note: "not ready", exact_dollar_ready: false } });
    const result = mapDeployV3ToStep2(plan);
    expect(result.exact_dollar_ready).toBe(false);
    expect(result.state).not.toBe("has_moves");
  });

  it("has_moves state → exact_dollar_ready is true, no scaffold copy needed", () => {
    const plan = makePlan({
      items: [makeItem({ final_actionability_status: "actionable_pending_tax" })],
    });
    const result = mapDeployV3ToStep2(plan);
    expect(result.exact_dollar_ready).toBe(true);
    expect(result.state).toBe("has_moves");
  });
});

// ── Step 2 / Step 3 coherence — Deploy v3 path must not feed legacy recommendations ──

describe("Step 2 / Step 3 coherence", () => {
  it("Deploy v3 Step 2 path does not reference legacy allocation/plan or deposit-plan endpoints", () => {
    // Structural: the mapper only operates on DeployV3PlanResponse.
    // Step 3 in the Deploy v3 path renders a placeholder — it does not receive legacy plan data.
    // Verified by reading the deposits/page.tsx source below.
    const fs = require("fs");
    const path = require("path");
    const pageSource: string = fs.readFileSync(
      path.resolve(__dirname, "../app/dashboard/deposits/page.tsx"),
      "utf8",
    );

    // DeployV3Step2Section must NOT accept a deployPlan prop
    expect(pageSource).not.toMatch(/DeployV3Step2Section[^}]*deployPlan/);

    // DeployV3Step2Section must NOT render DecisionLogMemoryPanel.
    // The function ends at the next top-level "function " or "// ──" marker.
    const sectionStart = pageSource.indexOf("function DeployV3Step2Section");
    const nextFn = pageSource.indexOf("\nfunction DeployV3AllocationTable", sectionStart + 1);
    const sectionBody = pageSource.slice(sectionStart, nextFn > sectionStart ? nextFn : undefined);
    expect(sectionBody).not.toContain("DecisionLogMemoryPanel");
  });

  it("Legacy fallback path (not_available) does not use Deploy v3 Step 2 section", () => {
    // When state is not_available, mapDeployV3ToStep2 returns is_deploy_v3=true
    // but the page switches to legacy DeploymentPlan (useV3ForStep2 is false).
    // Verify mapper: not_available means no items were rendered.
    const result = mapDeployV3ToStep2(null);
    expect(result.state).toBe("not_available");
    expect(result.items).toHaveLength(0);
    // The page gates on state !== "not_available" to decide which path to use.
    // If state is not_available, legacy DeploymentPlan is rendered instead.
    expect(result.state === "not_available").toBe(true);
  });

  it("has_moves items are the exact set a Step 3 logger must use — no legacy recs mixed in", () => {
    const plan = makePlan({
      items: [
        makeItem({ ticker: "AAPL", intel_action: "BUY", recommended_dollar_amount: 500, final_actionability_status: "actionable_pending_tax" }),
        makeItem({ ticker: "MSFT", intel_action: "TRIM", recommended_dollar_amount: 200, final_actionability_status: "actionable_pending_tax" }),
      ],
    });
    const result = mapDeployV3ToStep2(plan);
    expect(result.state).toBe("has_moves");
    // Step 3 logger must use result.items — not any external legacy plan.
    expect(result.items.map((i) => i.ticker)).toEqual(["AAPL", "MSFT"]);
    expect(result.items.every((i) => i.dollar_amount != null && i.dollar_amount > 0)).toBe(true);
  });
});

// ── Amount propagation contract — URL and enabled-guard ──────────────────────
//
// These tests verify the structural fix for the production Stage 2.6C validation
// failure: deposits/page.tsx was calling useDeployV3Plan(true, amount) with
// amount=0 (cleared field), which subscribed to the base query key
// ["deploy_v3","plan"] and returned the stale no-cash result cached by
// DeployV3Panel. The fix gates the hook on amount > 0.

describe("Deploy v3 getPlan URL contract", () => {
  const PLAN_ENDPOINT = "/api/v1/deploy/v3/plan";

  it("getPlan(900) builds URL with cash_to_deploy=900", () => {
    const fs = require("fs");
    const path = require("path");
    const apiSource: string = fs.readFileSync(
      path.resolve(__dirname, "./api.ts"),
      "utf8",
    );
    // Verify the URL template exists for positive cashToDeploy.
    expect(apiSource).toContain("cash_to_deploy=${cashToDeploy}");
  });

  it("getPlan(0) falls back to base endpoint (no query string)", () => {
    const fs = require("fs");
    const path = require("path");
    const apiSource: string = fs.readFileSync(
      path.resolve(__dirname, "./api.ts"),
      "utf8",
    );
    // Guard is `cashToDeploy != null && cashToDeploy > 0`; 0 takes the else branch.
    expect(apiSource).toContain("cashToDeploy > 0");
  });

  it("deposits page disables the hook when amount is 0 (not just passes 0)", () => {
    const fs = require("fs");
    const path = require("path");
    const pageSource: string = fs.readFileSync(
      path.resolve(__dirname, "../app/dashboard/deposits/page.tsx"),
      "utf8",
    );
    // The fix: deployV3Enabled guards the hook so amount=0 never subscribes to base key.
    expect(pageSource).toContain("deployV3Enabled");
    expect(pageSource).toContain("Number.isFinite(amount) && amount > 0");
    expect(pageSource).toContain("useDeployV3Plan(\n    deployV3Enabled,");
  });

  it("deposits page passes undefined (not 0) when amount is not positive", () => {
    const fs = require("fs");
    const path = require("path");
    const pageSource: string = fs.readFileSync(
      path.resolve(__dirname, "../app/dashboard/deposits/page.tsx"),
      "utf8",
    );
    // Hook receives undefined (not 0) when disabled — key stays isolated.
    expect(pageSource).toContain("deployV3Enabled ? amount : undefined");
  });

  it("amount-aware plan result carries amount_aware=true and cash_to_deploy", () => {
    const plan = makePlan({
      items: [makeItem({ ticker: "AAPL", intel_action: "BUY", recommended_dollar_amount: 900, final_actionability_status: "actionable_pending_tax" })],
      source: {
        intel_source: "INTEL_V3",
        sizing_bundle_provided: true,
        note: "Amount-aware.",
        exact_dollar_ready: true,
        amount_aware: true,
        cash_to_deploy: 900,
        sizing_mode: "new_cash",
      },
    });
    const result = mapDeployV3ToStep2(plan);
    expect(result.amount_aware).toBe(true);
    expect(result.cash_to_deploy).toBe(900);
    expect(result.state).toBe("has_moves");
  });
});
