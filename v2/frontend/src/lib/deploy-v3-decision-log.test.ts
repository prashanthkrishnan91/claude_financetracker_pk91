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
  buildDeployV3ManualRow,
  buildDeployV3SessionKey,
  classifyActualAction,
  computeJournalTotals,
  getDeployV3LogSessionKey,
  isManualDecisionRow,
  isSessionKeyChanged,
  mapActionToActualDefault,
  shouldUpdateExistingLog,
} from "@/lib/deploy-v3-decision-log";
import type { ActualDecisionItem } from "@/lib/api";
import type { DecisionMemoryLog } from "@/lib/api";
import type { Step2Item, Step2Result } from "@/lib/deploy-v3-step2-mapper";

// ── Factories ─────────────────────────────────────────────────────

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

// ── buildDeployV3DecisionSnapshot ────────────────────────────────────────────────

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

// ── buildDeployV3InitialActualDecisions ─────────────────────────────────────────────

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

// ── mapActionToActualDefault ──────────────────────────────────────────────

describe("mapActionToActualDefault", () => {
  it("BUY → BOUGHT", () => expect(mapActionToActualDefault("BUY")).toBe("BOUGHT"));
  it("TRIM → TRIMMED", () => expect(mapActionToActualDefault("TRIM")).toBe("TRIMMED"));
  it("SELL → SOLD", () => expect(mapActionToActualDefault("SELL")).toBe("SOLD"));
  it("HOLD → HELD", () => expect(mapActionToActualDefault("HOLD")).toBe("HELD"));
});

// ── buildDeployV3SessionKey ────────────────────────────────────────────────

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

// ── Visible Step 2 items equal logged recommended items ───────────────────────────

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

// ── No legacy endpoint usage ──────────────────────────────────────────────

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

// ── Notes + source contract ───────────────────────────────────────────────────

describe("Deploy v3 create API payload includes notes and source", () => {
  it("frontend api.ts createDecisionLog signature accepts notes and source opts", () => {
    const fs = require("fs");
    const path = require("path");
    const src: string = fs.readFileSync(path.resolve(__dirname, "api.ts"), "utf8");
    // opts parameter must exist on createDecisionLog
    expect(src).toContain("opts?.notes");
    expect(src).toContain("opts?.source");
  });

  it("hooks.ts useCreateDecisionMemoryLog threads notes and source to api", () => {
    const fs = require("fs");
    const path = require("path");
    const src: string = fs.readFileSync(path.resolve(__dirname, "hooks.ts"), "utf8");
    expect(src).toContain("notes, source");
    expect(src).toContain("{ notes, source }");
  });

  it("Deploy v3 onSave passes source: deploy_v3 on create (not legacy 'deploy')", () => {
    const fs = require("fs");
    const path = require("path");
    const pageSource: string = fs.readFileSync(
      path.resolve(__dirname, "../app/dashboard/deposits/page.tsx"),
      "utf8",
    );
    // The Deploy v3 create call must include source: "deploy_v3"
    expect(pageSource).toContain('source: "deploy_v3"');
  });

  it("Deploy v3 onSave passes notes on create", () => {
    const fs = require("fs");
    const path = require("path");
    const pageSource: string = fs.readFileSync(
      path.resolve(__dirname, "../app/dashboard/deposits/page.tsx"),
      "utf8",
    );
    // The createLog call for Deploy v3 includes notes
    expect(pageSource).toContain("createLog.mutateAsync({ snapshot, actualDecisions, notes, source");
  });

  it("legacy DecisionLogMemoryPanel create call is not changed (still uses default source)", () => {
    const fs = require("fs");
    const path = require("path");
    const pageSource: string = fs.readFileSync(
      path.resolve(__dirname, "../app/dashboard/deposits/page.tsx"),
      "utf8",
    );
    // Legacy path: createLog.mutateAsync({ snapshot, actualDecisions: decisionData })
    // It does NOT pass source: "deploy_v3" — source defaults to "deploy" via the API
    expect(pageSource).toContain("createLog.mutateAsync({ snapshot, actualDecisions: decisionData })");
  });

  it("snapshot source field remains deploy_v3 for Deploy v3 logs", () => {
    const snap = buildDeployV3DecisionSnapshot(makeStep2("has_moves", [makeItem()]), V3_META, CTX);
    // recommendation_snapshot.source = "deploy_v3" (snapshot-level source)
    expect(snap.source).toBe("deploy_v3");
  });
});

// ── Amount-aware snapshot (Stage 2.6C) ───────────────────────────────────────

// ── Stage 2.7: Editable actual amounts ─────────────────────────────────────

describe("Editable actual amounts (Stage 2.7)", () => {
  it("editing actual_amount preserves the recommended_amount alongside it", () => {
    // Simulate the visible Step 2 NFLX $245 recommendation.
    const decisions = buildDeployV3InitialActualDecisions([
      makeItem({ ticker: "NFLX", action: "BUY", dollar_amount: 245 }),
    ]);
    expect(decisions[0].recommended_amount).toBe(245);
    expect(decisions[0].actual_amount).toBe(245);

    // User edits NFLX 245 → 250
    const edited = decisions.map((row, i) => (i === 0 ? { ...row, actual_amount: 250 } : row));

    expect(edited[0].actual_amount).toBe(250);
    // Recommended is preserved so the log can show actual vs recommended cleanly.
    expect(edited[0].recommended_amount).toBe(245);
    expect(edited[0].recommended_action).toBe("BUY");
    expect(edited[0].is_manual).toBe(false);
  });

  it("regression: with no edits, payload uses exactly the visible Step 2 defaults", () => {
    const items: Step2Item[] = [
      makeItem({ ticker: "NFLX", action: "BUY", dollar_amount: 245 }),
      makeItem({ ticker: "META", action: "BUY", dollar_amount: 229 }),
      makeItem({ ticker: "GOOGL", action: "BUY", dollar_amount: 203 }),
      makeItem({ ticker: "TSM", action: "BUY", dollar_amount: 160 }),
      makeItem({ ticker: "CRM", action: "BUY", dollar_amount: 61 }),
    ];
    const decisions = buildDeployV3InitialActualDecisions(items);
    expect(decisions.map((d) => ({ ticker: d.ticker, actual: d.actual_amount, rec: d.recommended_amount }))).toEqual([
      { ticker: "NFLX", actual: 245, rec: 245 },
      { ticker: "META", actual: 229, rec: 229 },
      { ticker: "GOOGL", actual: 203, rec: 203 },
      { ticker: "TSM", actual: 160, rec: 160 },
      { ticker: "CRM", actual: 61, rec: 61 },
    ]);
    // Initial rows are never flagged as manual.
    expect(decisions.every((d) => d.is_manual === false)).toBe(true);
  });
});

// ── Stage 2.7: Manual user-added rows ──────────────────────────────────────

describe("buildDeployV3ManualRow / isManualDecisionRow (Stage 2.7)", () => {
  it("NVDA BUY $100 manual entry is flagged is_manual and has no recommended fields", () => {
    const row = buildDeployV3ManualRow("nvda", "BUY", 100, "added on my own");
    expect(row.ticker).toBe("NVDA");
    expect(row.actual_action).toBe("BOUGHT");
    expect(row.actual_amount).toBe(100);
    expect(row.is_manual).toBe(true);
    expect(row.recommended_action).toBeUndefined();
    expect(row.recommended_amount).toBeUndefined();
    expect(row.reason).toBe("added on my own");
    expect(isManualDecisionRow(row)).toBe(true);
  });

  it("manual TRIM/SELL map to default actual_action", () => {
    expect(buildDeployV3ManualRow("AAPL", "TRIM", 50).actual_action).toBe("TRIMMED");
    expect(buildDeployV3ManualRow("AAPL", "SELL", 50).actual_action).toBe("SOLD");
  });

  it("non-positive amount clamps to 0", () => {
    expect(buildDeployV3ManualRow("X", "BUY", -10).actual_amount).toBe(0);
    expect(buildDeployV3ManualRow("X", "BUY", Number.NaN).actual_amount).toBe(0);
  });

  it("a recommended row is NOT considered manual", () => {
    const rec = buildDeployV3InitialActualDecisions([makeItem({ ticker: "NFLX", dollar_amount: 245 })])[0];
    expect(isManualDecisionRow(rec)).toBe(false);
  });

  it("payload containing manual NVDA row preserves manual marker for backend", () => {
    const initial = buildDeployV3InitialActualDecisions([makeItem({ ticker: "NFLX", dollar_amount: 245 })]);
    const manual = buildDeployV3ManualRow("NVDA", "BUY", 100);
    const payload = [...initial, manual];
    const manualRows = payload.filter(isManualDecisionRow);
    expect(manualRows).toHaveLength(1);
    expect(manualRows[0].ticker).toBe("NVDA");
    expect(manualRows[0].is_manual).toBe(true);
  });
});

// ── Stage 2.7: Active fingerprint update vs duplicate (page wiring) ────────

describe("Deploy v3 active fingerprint update behavior (page wiring)", () => {
  it("page reuses buildDeployV3SessionKey to look up matchingLog (not a new fingerprint scheme)", () => {
    const fs = require("fs");
    const path = require("path");
    const pageSource: string = fs.readFileSync(
      path.resolve(__dirname, "../app/dashboard/deposits/page.tsx"),
      "utf8",
    );
    expect(pageSource).toContain("buildDeployV3SessionKey(v3Plan?.run_id, step2.items)");
    // Matching active log triggers update, not a new create.
    expect(pageSource).toContain("updateLog.mutateAsync({ id: target.id");
    // session_key is mirrored under decision_context so dedupe util works.
    expect(pageSource).toContain("decision_context: {");
    expect(pageSource).toContain("session_key: sessionKey");
  });
});

// ── Stage 2.7: Decision log history is rendered in primary Deploy UX ───────

describe("Deploy v3 primary UX shows decision log history (Stage 2.7)", () => {
  it("page wires DecisionHistoryEntry below Step 3 using recent decision logs", () => {
    const fs = require("fs");
    const path = require("path");
    const pageSource: string = fs.readFileSync(
      path.resolve(__dirname, "../app/dashboard/deposits/page.tsx"),
      "utf8",
    );
    expect(pageSource).toContain('id="deploy-v3-decision-history"');
    expect(pageSource).toContain("useDecisionMemoryLogs(10, true)");
    expect(pageSource).toContain("dedupeDecisionLogsForDisplay(recentLogs");
    // The v3 history section renders DecisionHistoryEntry rows (reuse, not a parallel impl).
    const v3Section = pageSource.split('id="deploy-v3-decision-history"')[1] ?? "";
    expect(v3Section).toContain("DecisionHistoryEntry");
  });

  it("there is only one DecisionHistoryEntry component definition (no duplicate history surfaces)", () => {
    const fs = require("fs");
    const path = require("path");
    const pageSource: string = fs.readFileSync(
      path.resolve(__dirname, "../app/dashboard/deposits/page.tsx"),
      "utf8",
    );
    const defMatches = pageSource.match(/function DecisionHistoryEntry\b/g) ?? [];
    expect(defMatches).toHaveLength(1);
  });
});

// ── Stage 2.7: Clarity copy (no new intelligence) ──────────────────────────

describe("Deploy v3 Step 3 clarity copy (Stage 2.7)", () => {
  it("includes plain-English note that recommendations are not broker-executed", () => {
    const fs = require("fs");
    const path = require("path");
    const pageSource: string = fs.readFileSync(
      path.resolve(__dirname, "../app/dashboard/deposits/page.tsx"),
      "utf8",
    );
    expect(pageSource).toContain("Intel v3 planning recommendations, not broker-executed trades");
  });
});

// ── Stage 2.7 patch: session-key reconciliation ────────────────────────────

describe("getDeployV3LogSessionKey (Stage 2.7 patch)", () => {
  function makeLog(snapshot: Record<string, unknown>): DecisionMemoryLog {
    return {
      id: "log-1",
      user_id: "u",
      source: "deploy_v3",
      status: "DRAFT",
      recommendation_snapshot: snapshot,
      actual_decisions: [],
      created_at: "",
      updated_at: "",
    } as unknown as DecisionMemoryLog;
  }

  it("reads top-level session_key", () => {
    expect(getDeployV3LogSessionKey(makeLog({ session_key: "deploy_v3:r1:AAPL:BUY:200" }))).toBe(
      "deploy_v3:r1:AAPL:BUY:200",
    );
  });

  it("falls back to decision_context.session_key", () => {
    expect(
      getDeployV3LogSessionKey(makeLog({ decision_context: { session_key: "deploy_v3:r1:AAPL:BUY:200" } })),
    ).toBe("deploy_v3:r1:AAPL:BUY:200");
  });

  it("returns null when neither location has a string key", () => {
    expect(getDeployV3LogSessionKey(makeLog({}))).toBeNull();
    expect(getDeployV3LogSessionKey(makeLog({ session_key: "" }))).toBeNull();
    expect(getDeployV3LogSessionKey(null)).toBeNull();
  });
});

describe("shouldUpdateExistingLog (Stage 2.7 patch)", () => {
  function makeLogWithKey(key: string | null): DecisionMemoryLog {
    return {
      id: "log-x",
      user_id: "u",
      source: "deploy_v3",
      status: "DRAFT",
      recommendation_snapshot: key === null ? {} : { session_key: key },
      actual_decisions: [],
      created_at: "",
      updated_at: "",
    } as unknown as DecisionMemoryLog;
  }

  it("returns true only when the candidate log's session_key matches the current sessionKey", () => {
    expect(shouldUpdateExistingLog(makeLogWithKey("k1"), "k1")).toBe(true);
  });

  it("returns false when the candidate log is from a previous session", () => {
    expect(shouldUpdateExistingLog(makeLogWithKey("k_old"), "k_new")).toBe(false);
  });

  it("returns false when the candidate log has no session_key (legacy log)", () => {
    expect(shouldUpdateExistingLog(makeLogWithKey(null), "k_new")).toBe(false);
  });

  it("returns false when candidate is null", () => {
    expect(shouldUpdateExistingLog(null, "k_new")).toBe(false);
  });
});

describe("isSessionKeyChanged (Stage 2.7 patch)", () => {
  it("initial mount (previous=null) is NOT a change", () => {
    expect(isSessionKeyChanged(null, "k1")).toBe(false);
  });

  it("same key on re-render is NOT a change", () => {
    expect(isSessionKeyChanged("k1", "k1")).toBe(false);
  });

  it("different key IS a change (amount/plan/run_id changed)", () => {
    expect(isSessionKeyChanged("k1", "k2")).toBe(true);
  });
});

describe("Stage 2.7 patch: sessionKey reset behavior in page wiring", () => {
  it("page tracks previousSessionKeyRef and resets state when sessionKey changes", () => {
    const fs = require("fs");
    const path = require("path");
    const pageSource: string = fs.readFileSync(
      path.resolve(__dirname, "../app/dashboard/deposits/page.tsx"),
      "utf8",
    );
    expect(pageSource).toContain("previousSessionKeyRef");
    expect(pageSource).toContain("isSessionKeyChanged(previousSessionKeyRef.current, sessionKey)");
    // On sessionKey change: clear savedLog, clear notes, reset to fresh defaults.
    expect(pageSource).toContain("setSavedLog(null)");
    expect(pageSource).toContain('setNotes("")');
    expect(pageSource).toContain("buildDeployV3InitialActualDecisions(step2.items)");
  });

  it("save guard uses shouldUpdateExistingLog so previous-session log is never patched", () => {
    const fs = require("fs");
    const path = require("path");
    const pageSource: string = fs.readFileSync(
      path.resolve(__dirname, "../app/dashboard/deposits/page.tsx"),
      "utf8",
    );
    expect(pageSource).toContain("shouldUpdateExistingLog(candidate, sessionKey)");
    // Active-log indicator is also session-key-guarded.
    expect(pageSource).toContain("shouldUpdateExistingLog(savedLog ?? matchingLog, sessionKey)");
  });

  it("matchingLog rehydrate effect is guarded by session_key equality", () => {
    const fs = require("fs");
    const path = require("path");
    const pageSource: string = fs.readFileSync(
      path.resolve(__dirname, "../app/dashboard/deposits/page.tsx"),
      "utf8",
    );
    expect(pageSource).toContain("getDeployV3LogSessionKey(matchingLog) !== sessionKey");
  });
});

describe("Stage 2.7 patch: session-local actual decisions (no manual leak)", () => {
  it("a previous-session manual NVDA row is NOT carried into a new session's defaults", () => {
    // Session A: NFLX $245 recommended + manual NVDA $100 user-added.
    const sessionAItems = [makeItem({ ticker: "NFLX", action: "BUY", dollar_amount: 245 })];
    const sessionAState = buildDeployV3InitialActualDecisions(sessionAItems).concat(
      buildDeployV3ManualRow("NVDA", "BUY", 100),
    );
    expect(sessionAState.some((r) => r.ticker === "NVDA" && r.is_manual === true)).toBe(true);

    // Session B (different recommendation set): GOOGL $203 + META $229. The
    // reset-on-sessionKey-change logic recomputes from Step 2 defaults only.
    const sessionBItems = [
      makeItem({ ticker: "GOOGL", action: "BUY", dollar_amount: 203 }),
      makeItem({ ticker: "META", action: "BUY", dollar_amount: 229 }),
    ];
    const sessionBDefaults = buildDeployV3InitialActualDecisions(sessionBItems);

    expect(sessionBDefaults.map((r) => r.ticker)).toEqual(["GOOGL", "META"]);
    // Crucially: no NVDA, no manual rows.
    expect(sessionBDefaults.some((r) => r.ticker === "NVDA")).toBe(false);
    expect(sessionBDefaults.some((r) => r.is_manual === true)).toBe(false);
  });

  it("same active plan (sessionKey unchanged) still updates the matching log, not duplicate-creates", () => {
    // Both calls produce the same key — page logic uses this to detect "same active plan".
    const items = [
      makeItem({ ticker: "NFLX", action: "BUY", dollar_amount: 245 }),
      makeItem({ ticker: "META", action: "BUY", dollar_amount: 229 }),
    ];
    const keyA = buildDeployV3SessionKey("run-001", items);
    const keyB = buildDeployV3SessionKey("run-001", items);
    expect(keyA).toBe(keyB);
    expect(isSessionKeyChanged(keyA, keyB)).toBe(false);

    // Same-key candidate is update-eligible.
    const sameLog = {
      id: "L",
      user_id: "u",
      source: "deploy_v3",
      status: "DRAFT",
      recommendation_snapshot: { session_key: keyA },
      actual_decisions: [],
      created_at: "",
      updated_at: "",
    } as unknown as DecisionMemoryLog;
    expect(shouldUpdateExistingLog(sameLog, keyB)).toBe(true);
  });

  it("changed amount → different sessionKey → previous log is NOT update-eligible", () => {
    // Different dollar_amount yields a different sessionKey (amount baked into key).
    const $900Items = [makeItem({ ticker: "NFLX", action: "BUY", dollar_amount: 245 })];
    const $1500Items = [makeItem({ ticker: "NFLX", action: "BUY", dollar_amount: 408 })];
    const key900 = buildDeployV3SessionKey("run-001", $900Items);
    const key1500 = buildDeployV3SessionKey("run-001", $1500Items);
    expect(key900).not.toBe(key1500);
    expect(isSessionKeyChanged(key900, key1500)).toBe(true);

    const previousLog = {
      id: "L_old",
      user_id: "u",
      source: "deploy_v3",
      status: "DRAFT",
      recommendation_snapshot: { session_key: key900 },
      actual_decisions: [],
      created_at: "",
      updated_at: "",
    } as unknown as DecisionMemoryLog;
    // Critically: when user is on the $1500 session, the previous $900 log must
    // NOT be the update target — that would overwrite it with $1500-session edits.
    expect(shouldUpdateExistingLog(previousLog, key1500)).toBe(false);
  });
});

// ── Stage 2.8: Journal accounting helpers ───────────────────────────────────

describe("classifyActualAction (Stage 2.8)", () => {
  it("maps BOUGHT/PARTIAL/REPLACED → buy", () => {
    expect(classifyActualAction("BOUGHT")).toBe("buy");
    expect(classifyActualAction("PARTIAL")).toBe("buy");
    expect(classifyActualAction("REPLACED")).toBe("buy");
  });

  it("maps TRIMMED/SOLD → trim_sell", () => {
    expect(classifyActualAction("TRIMMED")).toBe("trim_sell");
    expect(classifyActualAction("SOLD")).toBe("trim_sell");
  });

  it("maps SKIPPED/WATCHED/HELD → skipped (no journal effect)", () => {
    expect(classifyActualAction("SKIPPED")).toBe("skipped");
    expect(classifyActualAction("WATCHED")).toBe("skipped");
    expect(classifyActualAction("HELD")).toBe("skipped");
  });

  it("unknown/missing actions → other", () => {
    expect(classifyActualAction(undefined)).toBe("other");
    expect(classifyActualAction("")).toBe("other");
    expect(classifyActualAction("RANDOM")).toBe("other");
  });
});

describe("computeJournalTotals (Stage 2.8)", () => {
  function row(over: Partial<ActualDecisionItem>): ActualDecisionItem {
    return { ticker: "X", actual_action: "BOUGHT", actual_amount: 0, ...over };
  }

  it("$900 plan + manual NVDA BUY $100 + TRIM does not produce negative reserve", () => {
    // Acceptance scenario from the prompt: previously surfaced "Invested $1,186 / Reserved -$286".
    const recommended = buildDeployV3InitialActualDecisions([
      { ticker: "NFLX", action: "BUY", dollar_amount: 245, reason: "", final_actionability_status: "actionable_pending_tax" },
      { ticker: "META", action: "BUY", dollar_amount: 229, reason: "", final_actionability_status: "actionable_pending_tax" },
      { ticker: "GOOGL", action: "BUY", dollar_amount: 203, reason: "", final_actionability_status: "actionable_pending_tax" },
      { ticker: "TSM", action: "BUY", dollar_amount: 160, reason: "", final_actionability_status: "actionable_pending_tax" },
      { ticker: "CRM", action: "BUY", dollar_amount: 61, reason: "", final_actionability_status: "actionable_pending_tax" },
    ]);
    const manualBuy = buildDeployV3ManualRow("NVDA", "BUY", 100);
    const manualTrim = buildDeployV3ManualRow("AAPL", "TRIM", 188);
    const rows = [...recommended, manualBuy, manualTrim];

    const totals = computeJournalTotals(rows, 900);
    // BUY spend = recommended (898) + manual NVDA (100) = 998 — over plan by 98.
    expect(totals.actualBuyTotal).toBe(998);
    expect(totals.manualBuyTotal).toBe(100);
    // TRIM is separate, NOT folded into BUY.
    expect(totals.trimSellTotal).toBe(188);
    // Over plan surfaces, no negative reserve.
    expect(totals.overPlannedAmount).toBe(98);
    expect(totals.unallocatedAmount).toBe(0);
  });

  it("skipped/watched rows do not count as invested", () => {
    const rows: ActualDecisionItem[] = [
      row({ ticker: "AAPL", actual_action: "BOUGHT", actual_amount: 500, recommended_action: "BUY", recommended_amount: 500 }),
      row({ ticker: "MSFT", actual_action: "SKIPPED", actual_amount: 300, recommended_action: "BUY", recommended_amount: 300 }),
      row({ ticker: "GOOG", actual_action: "WATCHED", actual_amount: 200, recommended_action: "BUY", recommended_amount: 200 }),
      row({ ticker: "TSLA", actual_action: "HELD", actual_amount: 150, recommended_action: "BUY", recommended_amount: 150 }),
    ];
    const totals = computeJournalTotals(rows, 900);
    expect(totals.actualBuyTotal).toBe(500);
    expect(totals.skippedTotal).toBe(650);
    expect(totals.unallocatedAmount).toBe(400);
    expect(totals.overPlannedAmount).toBe(0);
  });

  it("manual BUY row is labelled and counted in manual + actual BUY totals", () => {
    const rows: ActualDecisionItem[] = [
      row({ ticker: "AAPL", actual_action: "BOUGHT", actual_amount: 600, recommended_action: "BUY", recommended_amount: 600 }),
      buildDeployV3ManualRow("NVDA", "BUY", 100),
    ];
    expect(isManualDecisionRow(rows[1])).toBe(true);
    const totals = computeJournalTotals(rows, 900);
    expect(totals.actualBuyTotal).toBe(700);
    expect(totals.manualBuyTotal).toBe(100);
    expect(totals.unallocatedAmount).toBe(200);
  });

  it("under-plan BUYs surface unallocated, never negative reserve", () => {
    const rows: ActualDecisionItem[] = [
      row({ ticker: "AAPL", actual_action: "BOUGHT", actual_amount: 400, recommended_action: "BUY", recommended_amount: 500 }),
    ];
    const totals = computeJournalTotals(rows, 900);
    expect(totals.actualBuyTotal).toBe(400);
    expect(totals.unallocatedAmount).toBe(500);
    expect(totals.overPlannedAmount).toBe(0);
  });

  it("recommendedBuyTotal sums only rows whose recommended_action is BUY", () => {
    const rows: ActualDecisionItem[] = [
      row({ ticker: "AAPL", actual_action: "BOUGHT", actual_amount: 500, recommended_action: "BUY", recommended_amount: 500 }),
      row({ ticker: "MSFT", actual_action: "TRIMMED", actual_amount: 200, recommended_action: "TRIM", recommended_amount: 200 }),
    ];
    const totals = computeJournalTotals(rows, 500);
    expect(totals.recommendedBuyTotal).toBe(500);
    expect(totals.trimSellTotal).toBe(200);
  });
});

// ── Stage 2.8: Deploy v3 evaluation compatibility ─────────────────────────────

describe("Deploy v3 snapshot exposes normalized_tickers for evaluation (Stage 2.8)", () => {
  it("BUY items are mirrored to normalized_tickers (amount, action, ticker) so existing backend evaluation works", () => {
    const items = [
      { ticker: "NFLX", action: "BUY" as const, dollar_amount: 245, reason: "", final_actionability_status: "actionable_pending_tax" as const },
      { ticker: "META", action: "BUY" as const, dollar_amount: 229, reason: "", final_actionability_status: "actionable_pending_tax" as const },
      { ticker: "AAPL", action: "TRIM" as const, dollar_amount: 100, reason: "", final_actionability_status: "actionable_pending_tax" as const },
    ];
    const snap = buildDeployV3DecisionSnapshot(
      { state: "has_moves", items, exact_dollar_ready: true, amount_aware: true, cash_to_deploy: 900 },
      { snapshot_id: "snap-x", run_id: "run-x", plan_status: "SCAFFOLD" },
      { entered_amount: 900 },
    );
    const normalized = snap.normalized_tickers as Array<{ ticker: string; action: string; amount: number }>;
    expect(normalized).toHaveLength(2);
    expect(normalized.map((n) => n.ticker).sort()).toEqual(["META", "NFLX"]);
    // TRIM rows must not inflate evaluation as if they were BUY investments.
    expect(normalized.find((n) => n.ticker === "AAPL")).toBeUndefined();
  });

  it("no BUY items → normalized_tickers is empty (evaluation returns missing_price gracefully)", () => {
    const snap = buildDeployV3DecisionSnapshot(
      { state: "no_moves", items: [], exact_dollar_ready: true },
      null,
      { entered_amount: 0 },
    );
    expect((snap.normalized_tickers as unknown[]).length).toBe(0);
  });
});

// ── Stage 2.8: History UI accounting wiring ───────────────────────────────────

describe("DecisionHistoryEntry uses action-aware journal totals (Stage 2.8)", () => {
  it("page imports computeJournalTotals/classifyActualAction and renders BUY spend separately from Trim/Sell", () => {
    const fs = require("fs");
    const path = require("path");
    const pageSource: string = fs.readFileSync(
      path.resolve(__dirname, "../app/dashboard/deposits/page.tsx"),
      "utf8",
    );
    expect(pageSource).toContain("computeJournalTotals");
    expect(pageSource).toContain("classifyActualAction");
    expect(pageSource).toContain("v3-history-buy-total");
    expect(pageSource).toContain("v3-history-trim-sell-total");
    // No "Reserved" path that can show a negative value.
    expect(pageSource).toContain("v3-history-over-planned");
    expect(pageSource).toContain("v3-history-unallocated");
    // The old single-aggregate "actualReserve" branch must be gone.
    expect(pageSource).not.toContain("const actualReserve = totalDeposit > 0 ? totalDeposit - actualTotal : aiReserve");
  });
});

describe("buildDeployV3DecisionSnapshot — amount-aware (Stage 2.6C)", () => {
  function makeStep2AmountAware(overrides: Partial<Step2Result> = {}): Pick<Step2Result, "state" | "items" | "exact_dollar_ready" | "amount_aware" | "cash_to_deploy"> {
    return {
      state: "has_moves",
      items: [makeItem()],
      exact_dollar_ready: true,
      amount_aware: true,
      cash_to_deploy: 900,
      ...overrides,
    };
  }

  it("amount_aware field is true when step2.amount_aware is true", () => {
    const snap = buildDeployV3DecisionSnapshot(makeStep2AmountAware(), V3_META, CTX);
    expect(snap.amount_aware).toBe(true);
  });

  it("amount_aware field is false when step2.amount_aware is not set", () => {
    const step2 = makeStep2AmountAware({ amount_aware: undefined });
    const snap = buildDeployV3DecisionSnapshot(step2, V3_META, CTX);
    expect(snap.amount_aware).toBe(false);
  });

  it("amount_aware field is false when step2.amount_aware is false", () => {
    const step2 = makeStep2AmountAware({ amount_aware: false });
    const snap = buildDeployV3DecisionSnapshot(step2, V3_META, CTX);
    expect(snap.amount_aware).toBe(false);
  });

  it("when amount_aware=true: amount_awareness_note mentions planning capital not broker-verified", () => {
    const snap = buildDeployV3DecisionSnapshot(makeStep2AmountAware(), V3_META, CTX);
    const note = snap.amount_awareness_note as string;
    expect(typeof note).toBe("string");
    expect(note.toLowerCase()).toContain("planning capital");
    expect(note.toLowerCase()).toContain("not broker-verified");
  });

  it("when amount_aware=true: cash_to_deploy is recorded in snapshot", () => {
    const snap = buildDeployV3DecisionSnapshot(makeStep2AmountAware({ cash_to_deploy: 900 }), V3_META, CTX);
    expect(snap.cash_to_deploy).toBe(900);
  });

  it("when amount_aware=true: cash_to_deploy null is propagated", () => {
    const snap = buildDeployV3DecisionSnapshot(makeStep2AmountAware({ cash_to_deploy: null }), V3_META, CTX);
    expect(snap.cash_to_deploy).toBeNull();
  });

  it("when amount_aware=false: original not-amount-aware note is used and no cash_to_deploy key", () => {
    const step2 = makeStep2AmountAware({ amount_aware: false });
    const snap = buildDeployV3DecisionSnapshot(step2, V3_META, CTX);
    const note = snap.amount_awareness_note as string;
    expect(note.toLowerCase()).toContain("not amount-aware");
    expect("cash_to_deploy" in snap).toBe(false);
  });

  it("visible_step2_items still logged exactly in amount-aware mode", () => {
    const items = [
      makeItem({ ticker: "AAPL", dollar_amount: 500 }),
      makeItem({ ticker: "MSFT", action: "TRIM", dollar_amount: 200 }),
    ];
    const snap = buildDeployV3DecisionSnapshot(makeStep2AmountAware({ items }), V3_META, CTX);
    const logged = snap.visible_step2_items as Array<{ ticker: string; dollar_amount: number | null }>;
    expect(logged).toHaveLength(2);
    expect(logged[0].ticker).toBe("AAPL");
    expect(logged[0].dollar_amount).toBe(500);
    expect(logged[1].ticker).toBe("MSFT");
    expect(logged[1].dollar_amount).toBe(200);
  });

  it("entered_amount_context is always recorded regardless of amount_aware", () => {
    const snapAware = buildDeployV3DecisionSnapshot(makeStep2AmountAware(), V3_META, { entered_amount: 900 });
    expect(snapAware.entered_amount_context).toBe(900);

    const snapNotAware = buildDeployV3DecisionSnapshot(makeStep2AmountAware({ amount_aware: false }), V3_META, { entered_amount: 500 });
    expect(snapNotAware.entered_amount_context).toBe(500);
  });

  it("source is always deploy_v3 in amount-aware mode", () => {
    const snap = buildDeployV3DecisionSnapshot(makeStep2AmountAware(), V3_META, CTX);
    expect(snap.source).toBe("deploy_v3");
  });
});
