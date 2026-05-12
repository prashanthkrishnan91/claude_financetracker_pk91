/**
 * Deploy v3 decision logging contract tests — Stage 2.6B.
 *
 * Pure helper tests only — no React, no Supabase, no hooks.
 *
 * Tests verify:
 * - Snapshot records entered amount as context only, not sizing authority
 * - Snapshot source is always "deploy_v3"
 * - Visible Step 2 items are recorded exactly as logged recommended items
 * - Initial actual decisions mirror visible Step 2 items
 * - no_moves / setup_incomplete states do not generate fake recommendations
 * - Action default mapping (BUY→BOUGHT, TRIM→TRIMMED, SELL→SOLD)
 * - Session key is stable for the same plan items
 * - No legacy /allocation/plan or /api/deposit-plan used as Deploy v3 authority
 */

import {
  buildDeployV3DecisionSnapshot,
  buildDeployV3InitialActualDecisions,
  buildDeployV3SessionKey,
  mapActionToActualDefault,
} from "@/lib/deploy-v3-decision-log";
import type { Step2Item, Step2Result } from "@/lib/deploy-v3-step2-mapper";

// ── Factories ─────────────────────────────────────────────────────────────────

function makeItem(overrides: Partial<Step2Item> = {}): Step2Item {
  return {
    ticker: "AAPL",
    action: "BUY",
    dollar_amount: 200,
    reason: "Buy",
    final_actionability_status: "actionable_pending_tax",
    ...overrides,
  };
}

function makeStep2(state: Step2Result["state"], items: Step2Item[] = []): Pick<Step2Result, "state" | "items" | "exact_dollar_ready"> {
  return {
    state,
    items,
    exact_dollar_ready: state === "has_moves" || state === "no_moves",
  };
}

const V3_META = { snapshot_id: "snap-001", run_id: "run-001", plan_status: "SCAFFOLD" };
const CTX = { entered_amount: 900 };

// ── buildDeployV3DecisionSnapshot ─────────────────────────────────────────────

describe("buildDeployV3DecisionSnapshot", () => {
  it("source is always deploy_v3", () => {
    const snap = buildDeployV3DecisionSnapshot(makeStep2("has_moves", [makeItem()]), V3_META, CTX);
    expect(snap.source).toBe("deploy_v3");
  });

  it("entered amount is recorded as context, not sizing authority", () => {
    const snap = buildDeployV3DecisionSnapshot(makeStep2("has_moves", [makeItem()]), V3_META, { entered_amount: 1500 });
    expect(snap.entered_amount_context).toBe(1500);
    expect(typeof snap.amount_awareness_note).toBe("string");
    expect((snap.amount_awareness_note as string).toLowerCase()).toContain("not amount-aware");
  });

  it("does not contain a field that claims Deploy v3 sized for the entered amount", () => {
    const snap = buildDeployV3DecisionSnapshot(makeStep2("has_moves", [makeItem()]), V3_META, CTX);
    // No field should claim the entered amount IS the Deploy v3 sizing input
    expect(snap).not.toHaveProperty("deploy_now_amount");
    expect(snap).not.toHaveProperty("sizing_amount");
  });

  it("records intel snapshot and run identifiers", () => {
    const snap = buildDeployV3DecisionSnapshot(makeStep2("has_moves"), V3_META, CTX);
    expect(snap.intel_snapshot_id).toBe("snap-001");
    expect(snap.intel_run_id).toBe("run-001");
  });

  it("handles null v3Meta gracefully", () => {
    const snap = buildDeployV3DecisionSnapshot(makeStep2("has_moves"), null, CTX);
    expect(snap.intel_snapshot_id).toBeNull();
    expect(snap.intel_run_id).toBeNull();
  });

  it("visible_step2_items matches Step 2 items exactly", () => {
    const items = [
      makeItem({ ticker: "AAPL", action: "BUY", dollar_amount: 300 }),
      makeItem({ ticker: "MSFT", action: "TRIM", dollar_amount: 150 }),
    ];
    const snap = buildDeployV3DecisionSnapshot(makeStep2("has_moves", items), V3_META, CTX);
    const logged = snap.visible_step2_items as Array<{ ticker: string; action: string; dollar_amount: number }>;
    expect(logged).toHaveLength(2);
    expect(logged[0].ticker).toBe("AAPL");
    expect(logged[0].action).toBe("BUY");
    expect(logged[0].dollar_amount).toBe(300);
    expect(logged[1].ticker).toBe("MSFT");
    expect(logged[1].action).toBe("TRIM");
  });

  it("no_moves state records empty visible items — no fake recommendations", () => {
    const snap = buildDeployV3DecisionSnapshot(makeStep2("no_moves", []), V3_META, CTX);
    expect((snap.visible_step2_items as unknown[]).length).toBe(0);
  });

  it("setup_incomplete state records empty visible items", () => {
    const step2 = { state: "setup_incomplete" as const, items: [], exact_dollar_ready: false };
    const snap = buildDeployV3DecisionSnapshot(step2, V3_META, CTX);
    expect((snap.visible_step2_items as unknown[]).length).toBe(0);
  });

  it("exact_dollar_ready is recorded in snapshot", () => {
    const snap = buildDeployV3DecisionSnapshot(makeStep2("has_moves", [makeItem()]), V3_META, CTX);
    expect(snap.exact_dollar_ready).toBe(true);
  });

  it("snapshot does not reference legacy allocation/plan or deposit-plan endpoints", () => {
    const snap = buildDeployV3DecisionSnapshot(makeStep2("has_moves", [makeItem()]), V3_META, CTX);
    const serialized = JSON.stringify(snap);
    expect(serialized).not.toContain("/api/deposit-plan");
    expect(serialized).not.toContain("/allocation/plan");
  });
});

// ── buildDeployV3InitialActualDecisions ───────────────────────────────────────

describe("buildDeployV3InitialActualDecisions", () => {
  it("returns one entry per Step 2 item", () => {
    const items = [makeItem({ ticker: "AAPL" }), makeItem({ ticker: "MSFT", action: "TRIM" })];
    const decisions = buildDeployV3InitialActualDecisions(items);
    expect(decisions).toHaveLength(2);
  });

  it("recommended_action matches the visible Step 2 action", () => {
    const decisions = buildDeployV3InitialActualDecisions([makeItem({ action: "BUY" })]);
    expect(decisions[0].recommended_action).toBe("BUY");
  });

  it("recommended_amount matches visible dollar_amount", () => {
    const decisions = buildDeployV3InitialActualDecisions([makeItem({ dollar_amount: 400 })]);
    expect(decisions[0].recommended_amount).toBe(400);
    expect(decisions[0].actual_amount).toBe(400);
  });

  it("actual_amount defaults to recommended_amount", () => {
    const decisions = buildDeployV3InitialActualDecisions([makeItem({ dollar_amount: 250 })]);
    expect(decisions[0].actual_amount).toBe(decisions[0].recommended_amount);
  });

  it("null dollar_amount defaults to 0, not fabricated", () => {
    const decisions = buildDeployV3InitialActualDecisions([makeItem({ dollar_amount: null })]);
    expect(decisions[0].recommended_amount).toBe(0);
    expect(decisions[0].actual_amount).toBe(0);
  });

  it("returns empty array for empty items — no_moves path does not fabricate", () => {
    expect(buildDeployV3InitialActualDecisions([])).toHaveLength(0);
  });

  it("ticker is preserved exactly", () => {
    const decisions = buildDeployV3InitialActualDecisions([makeItem({ ticker: "GOOG" })]);
    expect(decisions[0].ticker).toBe("GOOG");
  });
});

// ── mapActionToActualDefault ──────────────────────────────────────────────────

describe("mapActionToActualDefault", () => {
  it("BUY → BOUGHT", () => expect(mapActionToActualDefault("BUY")).toBe("BOUGHT"));
  it("TRIM → TRIMMED", () => expect(mapActionToActualDefault("TRIM")).toBe("TRIMMED"));
  it("SELL → SOLD", () => expect(mapActionToActualDefault("SELL")).toBe("SOLD"));
  it("HOLD → HELD", () => expect(mapActionToActualDefault("HOLD")).toBe("HELD"));
});

// ── buildDeployV3SessionKey ───────────────────────────────────────────────────

describe("buildDeployV3SessionKey", () => {
  it("same run_id and items → same key", () => {
    const items = [makeItem({ ticker: "AAPL", dollar_amount: 200 })];
    const k1 = buildDeployV3SessionKey("run-001", items);
    const k2 = buildDeployV3SessionKey("run-001", items);
    expect(k1).toBe(k2);
  });

  it("different run_id → different key", () => {
    const items = [makeItem()];
    expect(buildDeployV3SessionKey("run-001", items)).not.toBe(buildDeployV3SessionKey("run-002", items));
  });

  it("different items → different key", () => {
    const a = [makeItem({ ticker: "AAPL", dollar_amount: 200 })];
    const b = [makeItem({ ticker: "MSFT", dollar_amount: 200 })];
    expect(buildDeployV3SessionKey("run-001", a)).not.toBe(buildDeployV3SessionKey("run-001", b));
  });

  it("null run_id is handled", () => {
    const key = buildDeployV3SessionKey(null, [makeItem()]);
    expect(key).toContain("deploy_v3:");
    expect(key).toContain("no_run");
  });

  it("empty items → stable key", () => {
    const k1 = buildDeployV3SessionKey("run-001", []);
    const k2 = buildDeployV3SessionKey("run-001", []);
    expect(k1).toBe(k2);
  });
});

// ── Visible Step 2 items equal logged recommended items ───────────────────────

describe("Step 2 items are the exact logged recommended items", () => {
  it("each visible item appears once in the snapshot with unchanged values", () => {
    const items: Step2Item[] = [
      makeItem({ ticker: "AAPL", action: "BUY", dollar_amount: 500, final_actionability_status: "actionable_pending_tax" }),
      makeItem({ ticker: "TSLA", action: "TRIM", dollar_amount: 100, final_actionability_status: "actionable_pending_tax" }),
    ];
    const snap = buildDeployV3DecisionSnapshot(makeStep2("has_moves", items), V3_META, CTX);
    const logged = snap.visible_step2_items as Step2Item[];
    expect(logged).toHaveLength(items.length);
    items.forEach((item, i) => {
      expect(logged[i].ticker).toBe(item.ticker);
      expect(logged[i].action).toBe(item.action);
      expect(logged[i].dollar_amount).toBe(item.dollar_amount);
      expect(logged[i].final_actionability_status).toBe(item.final_actionability_status);
    });
  });

  it("initial actual decisions match visible items 1-to-1", () => {
    const items: Step2Item[] = [
      makeItem({ ticker: "AAPL", action: "BUY", dollar_amount: 500 }),
      makeItem({ ticker: "MSFT", action: "TRIM", dollar_amount: 200 }),
    ];
    const decisions = buildDeployV3InitialActualDecisions(items);
    expect(decisions).toHaveLength(items.length);
    decisions.forEach((d, i) => {
      expect(d.ticker).toBe(items[i].ticker);
      expect(d.recommended_action).toBe(items[i].action);
      expect(d.recommended_amount).toBe(items[i].dollar_amount);
    });
  });
});

// ── No legacy endpoint usage ──────────────────────────────────────────────────

describe("Deploy v3 decision logging does not use legacy endpoints", () => {
  it("deploy-v3-decision-log module does not import legacy api paths", () => {
    const fs = require("fs");
    const path = require("path");
    const src: string = fs.readFileSync(
      path.resolve(__dirname, "deploy-v3-decision-log.ts"),
      "utf8",
    );
    expect(src).not.toContain("/api/deposit-plan");
    expect(src).not.toContain("/allocation/plan");
    expect(src).not.toContain("DepositPlanResult");
  });
});
