/**
 * Deploy Ledger helper contract tests — Stage 4E.
 *
 * Tests pure mapping functions from deploy-ledger.ts.
 * No React, no Supabase, no external deps.
 *
 * Coverage:
 * - mapFinalStatusToLedger: all canonical statuses including not_evaluated_yet, actionable_pending_tax
 * - buildLedgerItems: ready, pending, suppressed, blocked, informational, not_ready
 * - buildLedgerPlanState: all readiness statuses including ready_pending_guardrails
 * - buildGuardrailGroups: grouping and ordering
 * - hasPortfolioShapeData: honest false — no fake portfolio shape claims
 * - No fake tax/wash-sale/target-allocation text presented as active intelligence
 */

import {
  mapFinalStatusToLedger,
  buildLedgerItems,
  buildLedgerPlanState,
  buildGuardrailGroups,
  hasPortfolioShapeData,
  isLedgerActionCardItem,
  type LedgerItem,
  type LedgerStatusGroup,
} from "@/lib/deploy-ledger";
import type { DeployV3PlanItem, DeployV3PlanRollup } from "@/lib/api";

// ── Factories ──────────────────────────────────────────────────────────────────

function makeItem(overrides: Partial<DeployV3PlanItem> = {}): DeployV3PlanItem {
  return {
    ticker: "AAPL",
    intel_action: "BUY",
    actionability_status: "actionable",
    action_source: "intel_v3",
    intel_snapshot_id: "snap-001",
    intel_run_id: "run-001",
    plan_status: "SCAFFOLD",
    recommended_dollar_amount: 200,
    final_actionability_status: "actionable_pending_tax",
    pending_guardrails_reason: "",
    suppression_reason: null,
    schema_version: "deploy_v1_scaffold",
    ...overrides,
  };
}

function makeRollup(overrides: Partial<DeployV3PlanRollup> = {}): DeployV3PlanRollup {
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
    plan_readiness_status: "ready_pending_guardrails",
    schema_version: "deploy_v1_scaffold",
    ...overrides,
  };
}

// ── mapFinalStatusToLedger ─────────────────────────────────────────────────────

describe("mapFinalStatusToLedger — status group mapping", () => {
  it("actionable_pending_tax → pending group", () => {
    const result = mapFinalStatusToLedger("actionable_pending_tax");
    expect(result.group).toBe("pending");
    expect(result.label.toLowerCase()).toContain("tax");
  });

  it("pending_guardrails → pending group", () => {
    const result = mapFinalStatusToLedger("pending_guardrails");
    expect(result.group).toBe("pending");
    expect(result.label.toLowerCase()).toContain("guardrail");
  });

  it("ready_pending_guardrails → pending group", () => {
    const result = mapFinalStatusToLedger("ready_pending_guardrails");
    expect(result.group).toBe("pending");
  });

  it("informational_hold → informational group", () => {
    const result = mapFinalStatusToLedger("informational_hold");
    expect(result.group).toBe("informational");
    expect(result.label.toLowerCase()).toContain("hold");
  });

  it("suppressed → suppressed group", () => {
    const result = mapFinalStatusToLedger("suppressed");
    expect(result.group).toBe("suppressed");
    expect(result.label.toLowerCase()).toContain("suppressed");
  });

  it("suppressed with reason → includes reason text in detail", () => {
    const result = mapFinalStatusToLedger("suppressed", "MISSING_POSITION_VALUE");
    expect(result.detail.toLowerCase()).toContain("missing position value");
  });

  it("blocked_cash → blocked group", () => {
    const result = mapFinalStatusToLedger("blocked_cash");
    expect(result.group).toBe("blocked");
    expect(result.label.toLowerCase()).toContain("blocked");
    expect(result.detail.toLowerCase()).toContain("cash");
  });

  it("not_ready → not_ready group", () => {
    const result = mapFinalStatusToLedger("not_ready");
    expect(result.group).toBe("not_ready");
    expect(result.label.toLowerCase()).toContain("not sized");
  });

  it("not_evaluated_yet → not_evaluated_yet group", () => {
    const result = mapFinalStatusToLedger("not_evaluated_yet");
    expect(result.group).toBe("not_evaluated_yet");
    expect(result.label.toLowerCase()).toContain("not evaluated");
  });

  it("unknown status → unknown group with fallback label", () => {
    const result = mapFinalStatusToLedger("some_future_status_xyz");
    expect(result.group).toBe("unknown");
    expect(result.label).toBe("Status unknown");
  });

  it("no label or detail claims execute, trade now, buy now, or fully actionable", () => {
    const statuses = [
      "actionable_pending_tax", "pending_guardrails", "informational_hold",
      "suppressed", "blocked_cash", "not_ready", "not_evaluated_yet",
    ];
    const forbidden = ["execute", "trade now", "buy now", "fully actionable"];
    for (const status of statuses) {
      const result = mapFinalStatusToLedger(status);
      for (const f of forbidden) {
        expect(result.label.toLowerCase()).not.toContain(f);
        expect(result.detail.toLowerCase()).not.toContain(f);
      }
    }
  });

  it("all status groups have non-empty label and detail", () => {
    const statuses = [
      "actionable_pending_tax", "pending_guardrails", "ready_pending_guardrails",
      "informational_hold", "suppressed", "blocked_cash", "not_ready", "not_evaluated_yet",
    ];
    for (const s of statuses) {
      const r = mapFinalStatusToLedger(s);
      expect(r.label.length).toBeGreaterThan(0);
      expect(r.detail.length).toBeGreaterThan(0);
    }
  });
});

// ── buildLedgerItems ───────────────────────────────────────────────────────────

describe("buildLedgerItems — builds LedgerItem[] from DeployV3PlanItem[]", () => {
  it("empty array → empty result", () => {
    expect(buildLedgerItems([])).toHaveLength(0);
  });

  it("maps ticker and action correctly", () => {
    const items = buildLedgerItems([makeItem({ ticker: "MSFT", intel_action: "TRIM" })]);
    expect(items[0].ticker).toBe("MSFT");
    expect(items[0].action).toBe("TRIM");
  });

  it("ready action — actionable_pending_tax → pending group, dollar amount preserved", () => {
    const items = buildLedgerItems([
      makeItem({
        ticker: "AAPL",
        intel_action: "BUY",
        final_actionability_status: "actionable_pending_tax",
        recommended_dollar_amount: 350,
      }),
    ]);
    expect(items[0].ledgerStatus.group).toBe("pending");
    expect(items[0].dollarAmount).toBe(350);
  });

  it("pending guardrail — pending_guardrails → pending group", () => {
    const items = buildLedgerItems([
      makeItem({ final_actionability_status: "pending_guardrails" }),
    ]);
    expect(items[0].ledgerStatus.group).toBe("pending");
  });

  it("not_evaluated_yet → not_evaluated_yet group", () => {
    const items = buildLedgerItems([
      makeItem({ final_actionability_status: "not_evaluated_yet" }),
    ]);
    expect(items[0].ledgerStatus.group).toBe("not_evaluated_yet");
  });

  it("actionable_pending_tax → pending group (explicit)", () => {
    const items = buildLedgerItems([
      makeItem({ final_actionability_status: "actionable_pending_tax" }),
    ]);
    expect(items[0].ledgerStatus.group).toBe("pending");
  });

  it("ready_pending_guardrails → pending group", () => {
    const items = buildLedgerItems([
      makeItem({ final_actionability_status: "ready_pending_guardrails" }),
    ]);
    expect(items[0].ledgerStatus.group).toBe("pending");
  });

  it("informational_hold → informational group, HOLD action", () => {
    const items = buildLedgerItems([
      makeItem({ intel_action: "HOLD", final_actionability_status: "informational_hold", recommended_dollar_amount: null }),
    ]);
    expect(items[0].ledgerStatus.group).toBe("informational");
    expect(items[0].action).toBe("HOLD");
    expect(items[0].dollarAmount).toBeNull();
  });

  it("suppressed → suppressed group", () => {
    const items = buildLedgerItems([
      makeItem({ final_actionability_status: "suppressed", suppression_reason: "STALE_SNAPSHOT" }),
    ]);
    expect(items[0].ledgerStatus.group).toBe("suppressed");
    expect(items[0].ledgerStatus.detail.toLowerCase()).toContain("stale snapshot");
  });

  it("blocked_cash → blocked group", () => {
    const items = buildLedgerItems([
      makeItem({ final_actionability_status: "blocked_cash" }),
    ]);
    expect(items[0].ledgerStatus.group).toBe("blocked");
  });

  it("not_ready → not_ready group", () => {
    const items = buildLedgerItems([
      makeItem({ final_actionability_status: "not_ready", recommended_dollar_amount: null }),
    ]);
    expect(items[0].ledgerStatus.group).toBe("not_ready");
  });

  it("hasPortfolioShape is always false — no fake portfolio shape data", () => {
    const items = buildLedgerItems([
      makeItem({ final_actionability_status: "actionable_pending_tax", recommended_dollar_amount: 200 }),
      makeItem({ ticker: "MSFT", final_actionability_status: "informational_hold", recommended_dollar_amount: null }),
    ]);
    for (const item of items) {
      expect(item.hasPortfolioShape).toBe(false);
    }
  });

  it("rationale for BUY with selection_reason surfaces backend reason", () => {
    const items = buildLedgerItems([
      makeItem({
        intel_action: "BUY",
        final_actionability_status: "actionable_pending_tax",
        selection_reason: "top conviction BUY in sleeve",
      }),
    ]);
    expect(items[0].rationale.toLowerCase()).toContain("top conviction");
  });

  it("rationale for BUY with sentinel 'none' does not surface sentinel", () => {
    const items = buildLedgerItems([
      makeItem({
        intel_action: "BUY",
        final_actionability_status: "actionable_pending_tax",
        selection_reason: "none",
      }),
    ]);
    expect(items[0].rationale.toLowerCase()).not.toContain(" none");
  });

  it("rationale does not claim tax lot, wash-sale, or target-allocation as active intelligence", () => {
    const forbidden = ["tax lot", "wash sale", "wash-sale", "canonical target"];
    const items = buildLedgerItems([
      makeItem({ final_actionability_status: "actionable_pending_tax" }),
      makeItem({ ticker: "MSFT", intel_action: "HOLD", final_actionability_status: "informational_hold", recommended_dollar_amount: null }),
      makeItem({ ticker: "GOOG", final_actionability_status: "suppressed", suppression_reason: "STALE_SNAPSHOT" }),
    ]);
    for (const item of items) {
      for (const f of forbidden) {
        expect(item.rationale.toLowerCase()).not.toContain(f);
      }
    }
  });
});

// ── buildLedgerPlanState ───────────────────────────────────────────────────────

describe("buildLedgerPlanState — plan headline mapping", () => {
  it("null status → unavailable severity", () => {
    const result = buildLedgerPlanState(null, null);
    expect(result.severity).toBe("unavailable");
    expect(result.headline.toLowerCase()).toContain("not built yet");
  });

  it("undefined status → unavailable severity", () => {
    const result = buildLedgerPlanState(undefined, null);
    expect(result.severity).toBe("unavailable");
  });

  it("no_items → unavailable severity, run-Intel copy", () => {
    const result = buildLedgerPlanState("no_items", null);
    expect(result.severity).toBe("unavailable");
    expect(result.headline.toLowerCase()).toContain("no items");
  });

  it("all_informational → ok severity, hold copy", () => {
    const result = buildLedgerPlanState("all_informational", makeRollup({ informational_count: 3 }));
    expect(result.severity).toBe("ok");
    expect(result.headline.toLowerCase()).toContain("hold");
  });

  it("all_suppressed → caution severity, suppressed copy", () => {
    const result = buildLedgerPlanState("all_suppressed", makeRollup({ suppressed_count: 4 }));
    expect(result.severity).toBe("caution");
    expect(result.headline.toLowerCase()).toContain("suppressed");
  });

  it("ready_pending_guardrails → pending severity, guardrail copy", () => {
    const result = buildLedgerPlanState("ready_pending_guardrails", makeRollup({ pending_count: 2 }));
    expect(result.severity).toBe("pending");
    expect(result.headline.toLowerCase()).toContain("pending");
    expect(result.sub.toLowerCase()).toContain("guardrail");
  });

  it("partially_ready → caution severity", () => {
    const result = buildLedgerPlanState("partially_ready", makeRollup({ blocked_count: 1, suppressed_count: 2 }));
    expect(result.severity).toBe("caution");
    expect(result.headline.toLowerCase()).toContain("partially");
  });

  it("blocked → blocked severity, cash copy", () => {
    const result = buildLedgerPlanState("blocked", null);
    expect(result.severity).toBe("blocked");
    expect(result.headline.toLowerCase()).toContain("blocked");
    expect(result.sub.toLowerCase()).toContain("cash");
  });

  it("not_ready → unavailable severity, setup copy", () => {
    const result = buildLedgerPlanState("not_ready", null);
    expect(result.severity).toBe("unavailable");
    expect(result.headline.toLowerCase()).toContain("setup");
  });

  it("unknown status → unavailable severity, unknown copy", () => {
    const result = buildLedgerPlanState("some_unknown_status_xyz", null);
    expect(result.severity).toBe("unavailable");
    expect(result.headline.toLowerCase()).toContain("unknown");
  });

  it("no headline claims execute, buy now, or fully actionable", () => {
    const statuses = [
      "no_items", "all_informational", "all_suppressed",
      "ready_pending_guardrails", "partially_ready", "blocked", "not_ready",
    ];
    const forbidden = ["execute", "buy now", "trade now", "fully actionable"];
    for (const status of statuses) {
      const result = buildLedgerPlanState(status, null);
      for (const f of forbidden) {
        expect(result.headline.toLowerCase()).not.toContain(f);
        expect(result.sub.toLowerCase()).not.toContain(f);
      }
    }
  });
});

// ── buildGuardrailGroups ───────────────────────────────────────────────────────

describe("buildGuardrailGroups — groups LedgerItems by status group", () => {
  function makeledgerItem(
    ticker: string,
    group: LedgerStatusGroup,
    action: LedgerItem["action"] = "BUY",
  ): LedgerItem {
    return {
      ticker,
      action,
      dollarAmount: action !== "HOLD" ? 100 : null,
      ledgerStatus: {
        group,
        label: group,
        detail: "",
      },
      rationale: "",
      hasPortfolioShape: false,
    };
  }

  it("empty items → empty groups", () => {
    expect(buildGuardrailGroups([])).toHaveLength(0);
  });

  it("single pending item → one pending group", () => {
    const groups = buildGuardrailGroups([makeledgerItem("AAPL", "pending")]);
    expect(groups).toHaveLength(1);
    expect(groups[0].group).toBe("pending");
    expect(groups[0].items).toHaveLength(1);
  });

  it("items from multiple groups → correct group count", () => {
    const items: LedgerItem[] = [
      makeledgerItem("AAPL", "pending"),
      makeledgerItem("MSFT", "informational", "HOLD"),
      makeledgerItem("GOOG", "suppressed"),
    ];
    const groups = buildGuardrailGroups(items);
    expect(groups).toHaveLength(3);
    const groupNames = groups.map((g) => g.group);
    expect(groupNames).toContain("pending");
    expect(groupNames).toContain("informational");
    expect(groupNames).toContain("suppressed");
  });

  it("pending appears before informational in order", () => {
    const items: LedgerItem[] = [
      makeledgerItem("MSFT", "informational", "HOLD"),
      makeledgerItem("AAPL", "pending"),
    ];
    const groups = buildGuardrailGroups(items);
    const order = groups.map((g) => g.group);
    expect(order.indexOf("pending")).toBeLessThan(order.indexOf("informational"));
  });

  it("blocked appears after pending and informational", () => {
    const items: LedgerItem[] = [
      makeledgerItem("A", "blocked"),
      makeledgerItem("B", "pending"),
      makeledgerItem("C", "informational", "HOLD"),
    ];
    const groups = buildGuardrailGroups(items);
    const order = groups.map((g) => g.group);
    expect(order.indexOf("pending")).toBeLessThan(order.indexOf("blocked"));
    expect(order.indexOf("informational")).toBeLessThan(order.indexOf("blocked"));
  });

  it("all groups have non-empty displayLabel and explanation", () => {
    const groups: LedgerStatusGroup[] = [
      "actionable", "pending", "blocked", "suppressed",
      "informational", "not_ready", "not_evaluated_yet", "unknown",
    ];
    for (const g of groups) {
      const result = buildGuardrailGroups([makeledgerItem("T1", g)]);
      expect(result[0].displayLabel.length).toBeGreaterThan(0);
      expect(result[0].explanation.length).toBeGreaterThan(0);
    }
  });

  it("group explanation does not claim tax lot, wash-sale, or canonical target as active intelligence", () => {
    const forbidden = ["tax lot", "wash sale", "wash-sale", "canonical target"];
    const allGroups: LedgerStatusGroup[] = [
      "actionable", "pending", "blocked", "suppressed",
      "informational", "not_ready", "not_evaluated_yet", "unknown",
    ];
    for (const g of allGroups) {
      const result = buildGuardrailGroups([makeledgerItem("T1", g)]);
      for (const f of forbidden) {
        expect(result[0].explanation.toLowerCase()).not.toContain(f);
      }
    }
  });
});

// ── hasPortfolioShapeData ──────────────────────────────────────────────────────

describe("hasPortfolioShapeData — honest false, no fake portfolio shape", () => {
  it("always returns false for empty items", () => {
    expect(hasPortfolioShapeData([])).toBe(false);
  });

  it("always returns false even when items have dollar amounts", () => {
    const items = buildLedgerItems([
      makeItem({ final_actionability_status: "actionable_pending_tax", recommended_dollar_amount: 500 }),
    ]);
    expect(hasPortfolioShapeData(items)).toBe(false);
  });

  it("always returns false regardless of action type", () => {
    const items = buildLedgerItems([
      makeItem({ intel_action: "BUY",  final_actionability_status: "actionable_pending_tax" }),
      makeItem({ intel_action: "TRIM", final_actionability_status: "actionable_pending_tax" }),
      makeItem({ intel_action: "SELL", final_actionability_status: "actionable_pending_tax" }),
      makeItem({ intel_action: "HOLD", final_actionability_status: "informational_hold", recommended_dollar_amount: null }),
    ]);
    expect(hasPortfolioShapeData(items)).toBe(false);
  });
});

// ── No fake intelligence claims ────────────────────────────────────────────────

describe("No fake tax/wash-sale/target-allocation intelligence", () => {
  it("mapFinalStatusToLedger detail does not claim tax lot intelligence", () => {
    const forbidden = ["tax lot", "wash sale", "wash-sale", "canonical target", "optimizer"];
    const statuses = [
      "actionable_pending_tax", "pending_guardrails", "informational_hold",
      "suppressed", "blocked_cash", "not_ready", "not_evaluated_yet",
    ];
    for (const s of statuses) {
      const r = mapFinalStatusToLedger(s);
      for (const f of forbidden) {
        expect(r.label.toLowerCase()).not.toContain(f);
        expect(r.detail.toLowerCase()).not.toContain(f);
      }
    }
  });

  it("buildLedgerPlanState sub does not claim wash-sale or tax-lot intelligence", () => {
    const forbidden = ["wash-sale", "tax lot", "optimizer"];
    const statuses = [
      "no_items", "all_informational", "all_suppressed", "ready_pending_guardrails",
      "partially_ready", "blocked", "not_ready",
    ];
    for (const s of statuses) {
      const result = buildLedgerPlanState(s, null);
      for (const f of forbidden) {
        expect(result.sub.toLowerCase()).not.toContain(f);
      }
    }
  });
});

// ── isLedgerActionCardItem ────────────────────────────────────────────────────

function makeLedgerItem(overrides: Partial<LedgerItem> = {}): LedgerItem {
  return {
    ticker: "AAPL",
    action: "BUY",
    dollarAmount: 200,
    ledgerStatus: mapFinalStatusToLedger("actionable_pending_tax"),
    rationale: "Intel Buy recommendation.",
    hasPortfolioShape: false,
    ...overrides,
  };
}

describe("isLedgerActionCardItem — action card filter", () => {
  it("BUY + positive amount + pending group → true", () => {
    const item = makeLedgerItem({ action: "BUY", dollarAmount: 500, ledgerStatus: mapFinalStatusToLedger("actionable_pending_tax") });
    expect(isLedgerActionCardItem(item)).toBe(true);
  });

  it("TRIM + positive amount + pending group → true", () => {
    const item = makeLedgerItem({ action: "TRIM", dollarAmount: 300, ledgerStatus: mapFinalStatusToLedger("pending_guardrails") });
    expect(isLedgerActionCardItem(item)).toBe(true);
  });

  it("SELL + positive amount + pending group → true", () => {
    const item = makeLedgerItem({ action: "SELL", dollarAmount: 100, ledgerStatus: mapFinalStatusToLedger("ready_pending_guardrails") });
    expect(isLedgerActionCardItem(item)).toBe(true);
  });

  it("positive amount + blocked group → false", () => {
    const item = makeLedgerItem({ action: "BUY", dollarAmount: 400, ledgerStatus: mapFinalStatusToLedger("blocked_cash") });
    expect(isLedgerActionCardItem(item)).toBe(false);
  });

  it("positive amount + suppressed group → false", () => {
    const item = makeLedgerItem({ action: "BUY", dollarAmount: 200, ledgerStatus: mapFinalStatusToLedger("suppressed") });
    expect(isLedgerActionCardItem(item)).toBe(false);
  });

  it("positive amount + not_ready group → false", () => {
    const item = makeLedgerItem({ action: "BUY", dollarAmount: 150, ledgerStatus: mapFinalStatusToLedger("not_ready") });
    expect(isLedgerActionCardItem(item)).toBe(false);
  });

  it("positive amount + not_evaluated_yet group → false", () => {
    const item = makeLedgerItem({ action: "BUY", dollarAmount: 100, ledgerStatus: mapFinalStatusToLedger("not_evaluated_yet") });
    expect(isLedgerActionCardItem(item)).toBe(false);
  });

  it("positive amount + unknown group → false", () => {
    const item = makeLedgerItem({ action: "BUY", dollarAmount: 100, ledgerStatus: mapFinalStatusToLedger("completely_unknown_status") });
    expect(isLedgerActionCardItem(item)).toBe(false);
  });

  it("HOLD + positive amount + pending group → false", () => {
    const item = makeLedgerItem({ action: "HOLD", dollarAmount: 500, ledgerStatus: mapFinalStatusToLedger("actionable_pending_tax") });
    expect(isLedgerActionCardItem(item)).toBe(false);
  });

  it("null dollar amount → false", () => {
    const item = makeLedgerItem({ action: "BUY", dollarAmount: null, ledgerStatus: mapFinalStatusToLedger("actionable_pending_tax") });
    expect(isLedgerActionCardItem(item)).toBe(false);
  });

  it("zero dollar amount → false", () => {
    const item = makeLedgerItem({ action: "BUY", dollarAmount: 0, ledgerStatus: mapFinalStatusToLedger("actionable_pending_tax") });
    expect(isLedgerActionCardItem(item)).toBe(false);
  });

  it("informational_hold (HOLD) → false even with positive amount", () => {
    const item = makeLedgerItem({ action: "HOLD", dollarAmount: 200, ledgerStatus: mapFinalStatusToLedger("informational_hold") });
    expect(isLedgerActionCardItem(item)).toBe(false);
  });
});
