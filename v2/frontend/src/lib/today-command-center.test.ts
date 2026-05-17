import type {
  IntelV3Snapshot,
  IntelV3HeldCard,
  DeployV3PlanResponse,
  AlertCandidate,
} from "@/lib/api";
import {
  buildTheBrief,
  buildActToday,
  buildRiskPulse,
  buildDeployReady,
  buildWatchtowerSummary,
  buildWhyThisMatters,
  buildLearningSlotCaption,
} from "@/lib/today-command-center";

// ── Fixtures ──────────────────────────────────────────────────────────────────

function makeCard(overrides: Partial<IntelV3HeldCard> = {}): IntelV3HeldCard {
  return {
    ticker: "AAPL",
    name: "Apple Inc.",
    asset_type: "equity",
    action: "HOLD",
    conviction: "MEDIUM",
    evidence_band: "PARTIAL",
    portfolio_fit: "core",
    risk_level: "LOW",
    thesis_state: "intact",
    why_text: "Solid fundamentals.",
    risk_text: "Macro headwinds.",
    action_text: "Hold your position.",
    what_would_change_view: "",
    fit_text: "",
    evidence_text: "",
    flags: [],
    source_snapshot_id: "snap-1",
    source_run_id: "run-1",
    updated_at: "2026-05-17T10:00:00Z",
    detail_drawer_payload: {
      rationale: "Strong balance sheet.",
      why_now: "",
      why_not_now: "",
      evidence_band: "PARTIAL",
      evidence_quality: "",
      attractiveness: "",
      price_context: "",
      portfolio_fit_raw: "",
      risk_band: "",
      blockers: [],
      suppression_reasons: {},
      schema_version: "v1",
      committee: { status: "pending" },
    },
    ...overrides,
  };
}

function makeSnapshot(overrides: Partial<IntelV3Snapshot> = {}): IntelV3Snapshot {
  return {
    schema_version: "v3",
    snapshot_id: "snap-1",
    run_id: "run-1",
    generated_at: "2026-05-17T10:00:00Z",
    is_stale: false,
    source_health: { status: "ok" },
    portfolio_command_center: {
      total_holdings: 3,
      buy_count: 0,
      hold_count: 3,
      trim_count: 0,
      sell_count: 0,
      high_conviction: 1,
      thin_evidence: 0,
      source_health: { status: "ok" },
    },
    action_counts: { BUY: 0, HOLD: 3, TRIM: 0, SELL: 0 },
    evidence_band_counts: { THIN: 0, PARTIAL: 2, STRONG: 1 },
    conviction_counts: { LOW: 0, MEDIUM: 2, HIGH: 1 },
    best_buys: [],
    trim_sell_desk: [],
    current_holdings: [],
    opportunity_radar_preview: { status: "deferred" },
    what_changed: [],
    warnings: [],
    legacy_path_used: false,
    ...overrides,
  };
}

function makeDeployPlan(overrides: Partial<DeployV3PlanResponse> = {}): DeployV3PlanResponse {
  return {
    plan_status: "ok",
    snapshot_id: "snap-1",
    run_id: "run-1",
    schema_version: "v3",
    items: [],
    guardrail_summary: null,
    rollup: {
      total_items: 0,
      counts_by_final_actionability_status: {},
      counts_by_pending_guardrails_reason: {},
      actionable_count: 0,
      pending_count: 0,
      blocked_count: 0,
      informational_count: 0,
      suppressed_count: 0,
      not_ready_count: 0,
      unknown_count: 0,
      plan_readiness_status: "no_items",
      schema_version: "v1",
    },
    source: {
      intel_source: "intel_v3",
      sizing_bundle_provided: false,
      note: "",
    },
    ...overrides,
  };
}

function makeAlertCandidate(overrides: Partial<AlertCandidate> = {}): AlertCandidate {
  return {
    id: "cand-1",
    user_id: "user-1",
    ticker: "AAPL",
    source_area: "intel_v3",
    candidate_type: "action_change",
    action_type: "BUY",
    severity: "MEDIUM",
    reason_code: "action_changed_to_buy",
    plain_english_reason: "Recommendation changed to Buy.",
    policy_version: "v1",
    status: "active",
    dedupe_key: "cand-1",
    source_snapshot_id: "snap-1",
    source_run_id: "run-1",
    expires_at: null,
    cooldown_until: null,
    created_at: "2026-05-17T09:00:00Z",
    ...overrides,
  };
}

// ── buildTheBrief ─────────────────────────────────────────────────────────────

describe("buildTheBrief", () => {
  it("returns unavailable state when snapshot is undefined", () => {
    const result = buildTheBrief(undefined, undefined, undefined);
    expect(result.dataAvailable).toBe(false);
    expect(result.sentences.some(s => /not yet available/i.test(s))).toBe(true);
  });

  it("returns unavailable state when snapshot is null", () => {
    const result = buildTheBrief(null, undefined, undefined);
    expect(result.dataAvailable).toBe(false);
  });

  it("reports all Hold when no actionable items", () => {
    const snap = makeSnapshot();
    const result = buildTheBrief(snap, makeDeployPlan(), []);
    expect(result.dataAvailable).toBe(true);
    expect(result.sentences[0]).toMatch(/hold/i);
    expect(result.sentences[0]).toMatch(/no new actions today/i);
  });

  it("reports Buy/Trim/Sell counts in S1", () => {
    const snap = makeSnapshot({
      portfolio_command_center: {
        total_holdings: 5,
        buy_count: 2,
        hold_count: 1,
        trim_count: 1,
        sell_count: 1,
        high_conviction: 2,
        thin_evidence: 0,
        source_health: { status: "ok" },
      },
    });
    const result = buildTheBrief(snap, makeDeployPlan(), []);
    expect(result.sentences[0]).toMatch(/2 Buys/);
    expect(result.sentences[0]).toMatch(/1 Trim/);
    expect(result.sentences[0]).toMatch(/1 Sell/);
  });

  it("uses what_changed[0] for S2 when present", () => {
    const snap = makeSnapshot({ what_changed: ["AAPL changed from HOLD to BUY."] });
    const result = buildTheBrief(snap, makeDeployPlan(), []);
    expect(result.sentences[1]).toBe("AAPL changed from HOLD to BUY.");
  });

  it("uses no-change message for S2 when what_changed is empty", () => {
    const snap = makeSnapshot({ what_changed: [] });
    const result = buildTheBrief(snap, makeDeployPlan(), []);
    expect(result.sentences[1]).toMatch(/no changes since/i);
  });

  it("reports Deploy plan unavailable when deployPlan is undefined", () => {
    const snap = makeSnapshot();
    const result = buildTheBrief(snap, undefined, []);
    expect(result.sentences[2]).toMatch(/not yet available/i);
  });

  it("reports Deploy BUY candidates when present", () => {
    const plan = makeDeployPlan({
      items: [
        {
          ticker: "AAPL",
          intel_action: "BUY",
          actionability_status: "actionable_pending_tax",
          action_source: "intel_v3",
          intel_snapshot_id: "snap-1",
          intel_run_id: "run-1",
          plan_status: "ok",
          recommended_dollar_amount: 500,
          final_actionability_status: "actionable_pending_tax",
          pending_guardrails_reason: "tax_guardrail_not_evaluated",
          suppression_reason: null,
          schema_version: "v1",
        },
      ],
    });
    const result = buildTheBrief(makeSnapshot(), plan, []);
    expect(result.sentences[2]).toMatch(/1 Buy candidate/);
  });

  it("reports Watchtower unavailable when alertCandidates is undefined", () => {
    const result = buildTheBrief(makeSnapshot(), makeDeployPlan(), undefined);
    expect(result.sentences[3]).toMatch(/loading/i);
  });

  it("reports no Watchtower alerts when candidates array is empty", () => {
    const result = buildTheBrief(makeSnapshot(), makeDeployPlan(), []);
    expect(result.sentences[3]).toMatch(/no watchtower alerts/i);
  });

  it("reports Watchtower candidate count including high severity", () => {
    const candidates = [
      makeAlertCandidate({ severity: "HIGH" }),
      makeAlertCandidate({ id: "cand-2", severity: "MEDIUM" }),
    ];
    const result = buildTheBrief(makeSnapshot(), makeDeployPlan(), candidates);
    expect(result.sentences[3]).toMatch(/2 candidates/i);
    expect(result.sentences[3]).toMatch(/high-severity/i);
  });

  it("reports candidate count without high-severity note when none are high", () => {
    const candidates = [
      makeAlertCandidate({ severity: "MEDIUM" }),
      makeAlertCandidate({ id: "cand-2", severity: "LOW" }),
    ];
    const result = buildTheBrief(makeSnapshot(), makeDeployPlan(), candidates);
    expect(result.sentences[3]).toMatch(/2 active candidates/i);
    expect(result.sentences[3]).not.toMatch(/high-severity/i);
  });
});

// ── buildActToday ─────────────────────────────────────────────────────────────

describe("buildActToday", () => {
  it("returns empty with allHold false when snapshot is undefined", () => {
    const result = buildActToday(undefined);
    expect(result.rows).toHaveLength(0);
    expect(result.hasActionableItems).toBe(false);
    expect(result.allHold).toBe(false);
  });

  it("returns allHold true when snapshot has holdings but no BUY/TRIM/SELL", () => {
    const snap = makeSnapshot({
      portfolio_command_center: {
        total_holdings: 4,
        buy_count: 0,
        hold_count: 4,
        trim_count: 0,
        sell_count: 0,
        high_conviction: 1,
        thin_evidence: 0,
        source_health: { status: "ok" },
      },
      best_buys: [],
      trim_sell_desk: [],
    });
    const result = buildActToday(snap);
    expect(result.rows).toHaveLength(0);
    expect(result.hasActionableItems).toBe(false);
    expect(result.allHold).toBe(true);
  });

  it("returns BUY rows from best_buys", () => {
    const snap = makeSnapshot({
      best_buys: [makeCard({ ticker: "MSFT", action: "BUY", conviction: "HIGH" })],
    });
    const result = buildActToday(snap);
    expect(result.hasActionableItems).toBe(true);
    expect(result.rows).toHaveLength(1);
    expect(result.rows[0].ticker).toBe("MSFT");
    expect(result.rows[0].action).toBe("BUY");
  });

  it("returns TRIM rows from trim_sell_desk", () => {
    const snap = makeSnapshot({
      trim_sell_desk: [makeCard({ ticker: "AMZN", action: "TRIM", conviction: "MEDIUM" })],
    });
    const result = buildActToday(snap);
    expect(result.rows[0].action).toBe("TRIM");
  });

  it("sorts rows by conviction descending (HIGH before LOW)", () => {
    const snap = makeSnapshot({
      best_buys: [
        makeCard({ ticker: "LOW_CONV", action: "BUY", conviction: "LOW", evidence_band: "STRONG" }),
        makeCard({ ticker: "HIGH_CONV", action: "BUY", conviction: "HIGH", evidence_band: "THIN" }),
      ],
    });
    const result = buildActToday(snap);
    expect(result.rows[0].ticker).toBe("HIGH_CONV");
    expect(result.rows[1].ticker).toBe("LOW_CONV");
  });

  it("sorts rows by evidence band when conviction is equal", () => {
    const snap = makeSnapshot({
      best_buys: [
        makeCard({ ticker: "THIN", action: "BUY", conviction: "MEDIUM", evidence_band: "THIN" }),
        makeCard({ ticker: "STRONG", action: "BUY", conviction: "MEDIUM", evidence_band: "STRONG" }),
      ],
    });
    const result = buildActToday(snap);
    expect(result.rows[0].ticker).toBe("STRONG");
  });

  it("caps rows at 5", () => {
    const cards = ["A", "B", "C", "D", "E", "F"].map(t =>
      makeCard({ ticker: t, action: "BUY", conviction: "MEDIUM" })
    );
    const snap = makeSnapshot({ best_buys: cards });
    const result = buildActToday(snap);
    expect(result.rows).toHaveLength(5);
  });

  it("combines BUY and TRIM/SELL rows up to the cap", () => {
    const snap = makeSnapshot({
      best_buys: [makeCard({ ticker: "BUY1", action: "BUY", conviction: "HIGH" })],
      trim_sell_desk: [makeCard({ ticker: "TRIM1", action: "TRIM", conviction: "HIGH" })],
    });
    const result = buildActToday(snap);
    expect(result.rows).toHaveLength(2);
    expect(result.rows.map(r => r.ticker)).toEqual(expect.arrayContaining(["BUY1", "TRIM1"]));
  });

  it("populates whyThisMatters from why_text", () => {
    const snap = makeSnapshot({
      best_buys: [makeCard({ action: "BUY", why_text: "Strong earnings growth." })],
    });
    const result = buildActToday(snap);
    expect(result.rows[0].whyThisMatters).toBe("Strong earnings growth.");
  });
});

// ── buildRiskPulse ────────────────────────────────────────────────────────────

describe("buildRiskPulse", () => {
  it("returns empty when snapshot is undefined", () => {
    const result = buildRiskPulse(undefined);
    expect(result.rows).toHaveLength(0);
    expect(result.hasElevatedRisk).toBe(false);
  });

  it("returns empty when no holdings have elevated risk", () => {
    const snap = makeSnapshot({
      current_holdings: [
        makeCard({ ticker: "AAPL", risk_level: "LOW" }),
        makeCard({ ticker: "MSFT", risk_level: "MODERATE" }),
      ],
    });
    const result = buildRiskPulse(snap);
    expect(result.hasElevatedRisk).toBe(false);
    expect(result.rows).toHaveLength(0);
  });

  it("returns rows for ELEVATED risk", () => {
    const snap = makeSnapshot({
      current_holdings: [
        makeCard({ ticker: "RISKY", risk_level: "ELEVATED", risk_text: "High concentration." }),
      ],
    });
    const result = buildRiskPulse(snap);
    expect(result.hasElevatedRisk).toBe(true);
    expect(result.rows).toHaveLength(1);
    expect(result.rows[0].ticker).toBe("RISKY");
    expect(result.rows[0].riskText).toBe("High concentration.");
  });

  it("returns rows for ACUTE risk", () => {
    const snap = makeSnapshot({
      current_holdings: [makeCard({ ticker: "ACUTE_RISK", risk_level: "ACUTE" })],
    });
    const result = buildRiskPulse(snap);
    expect(result.hasElevatedRisk).toBe(true);
    expect(result.rows[0].ticker).toBe("ACUTE_RISK");
  });

  it("is case-insensitive for risk_level matching", () => {
    const snap = makeSnapshot({
      current_holdings: [makeCard({ ticker: "X", risk_level: "elevated" })],
    });
    const result = buildRiskPulse(snap);
    expect(result.hasElevatedRisk).toBe(true);
  });

  it("excludes non-elevated tickers from results", () => {
    const snap = makeSnapshot({
      current_holdings: [
        makeCard({ ticker: "SAFE", risk_level: "LOW" }),
        makeCard({ ticker: "RISKY", risk_level: "ELEVATED" }),
      ],
    });
    const result = buildRiskPulse(snap);
    expect(result.rows).toHaveLength(1);
    expect(result.rows[0].ticker).toBe("RISKY");
  });
});

// ── buildDeployReady ──────────────────────────────────────────────────────────

describe("buildDeployReady", () => {
  it("returns hasData false when deployPlan is undefined", () => {
    const result = buildDeployReady(undefined);
    expect(result.hasData).toBe(false);
    expect(result.planReadinessLabel).toMatch(/unavailable/i);
    expect(result.buyCount).toBe(0);
  });

  it("returns hasData false when deployPlan is null", () => {
    const result = buildDeployReady(null);
    expect(result.hasData).toBe(false);
  });

  it("maps plan readiness status to a label", () => {
    const plan = makeDeployPlan();
    const result = buildDeployReady(plan);
    expect(result.hasData).toBe(true);
    expect(result.planReadinessLabel).toBe("No actions pending");
  });

  it("maps ready_pending_guardrails status correctly", () => {
    const plan = makeDeployPlan({
      rollup: {
        ...makeDeployPlan().rollup!,
        plan_readiness_status: "ready_pending_guardrails",
      },
    });
    const result = buildDeployReady(plan);
    expect(result.planReadinessLabel).toMatch(/ready/i);
  });

  it("counts BUY items from plan items", () => {
    const plan = makeDeployPlan({
      items: [
        {
          ticker: "AAPL",
          intel_action: "BUY",
          actionability_status: "actionable_pending_tax",
          action_source: "intel_v3",
          intel_snapshot_id: "snap-1",
          intel_run_id: "run-1",
          plan_status: "ok",
          recommended_dollar_amount: 500,
          final_actionability_status: "actionable_pending_tax",
          pending_guardrails_reason: "tax_guardrail_not_evaluated",
          suppression_reason: null,
          schema_version: "v1",
        },
        {
          ticker: "MSFT",
          intel_action: "BUY",
          actionability_status: "actionable_pending_tax",
          action_source: "intel_v3",
          intel_snapshot_id: "snap-1",
          intel_run_id: "run-1",
          plan_status: "ok",
          recommended_dollar_amount: 300,
          final_actionability_status: "actionable_pending_tax",
          pending_guardrails_reason: "tax_guardrail_not_evaluated",
          suppression_reason: null,
          schema_version: "v1",
        },
      ],
    });
    const result = buildDeployReady(plan);
    expect(result.buyCount).toBe(2);
  });

  it("does not include HOLD items in buyCount", () => {
    const plan = makeDeployPlan({
      items: [
        {
          ticker: "HOLD_STOCK",
          intel_action: "HOLD",
          actionability_status: "informational",
          action_source: "intel_v3",
          intel_snapshot_id: "snap-1",
          intel_run_id: "run-1",
          plan_status: "ok",
          recommended_dollar_amount: null,
          final_actionability_status: "informational",
          pending_guardrails_reason: "",
          suppression_reason: null,
          schema_version: "v1",
        },
      ],
    });
    const result = buildDeployReady(plan);
    expect(result.buyCount).toBe(0);
  });

  it("adds cashNote when amount_aware with cash_to_deploy > 0", () => {
    const plan = makeDeployPlan({
      source: {
        intel_source: "intel_v3",
        sizing_bundle_provided: true,
        note: "",
        amount_aware: true,
        cash_to_deploy: 1500,
      },
    });
    const result = buildDeployReady(plan);
    expect(result.cashNote).not.toBeNull();
    expect(result.cashNote).toMatch(/1,500/);
  });

  it("does not add cashNote when not amount_aware", () => {
    const plan = makeDeployPlan();
    const result = buildDeployReady(plan);
    expect(result.cashNote).toBeNull();
  });
});

// ── buildWatchtowerSummary ────────────────────────────────────────────────────

describe("buildWatchtowerSummary", () => {
  it("returns hasData false when alertCandidates is undefined", () => {
    const result = buildWatchtowerSummary(undefined);
    expect(result.hasData).toBe(false);
    expect(result.summaryLine).toMatch(/not yet available/i);
  });

  it("returns hasData false when alertCandidates is null", () => {
    const result = buildWatchtowerSummary(null);
    expect(result.hasData).toBe(false);
  });

  it("reports no active alerts for empty array", () => {
    const result = buildWatchtowerSummary([]);
    expect(result.hasData).toBe(true);
    expect(result.candidateCount).toBe(0);
    expect(result.summaryLine).toMatch(/no active alerts/i);
  });

  it("reports candidate count without high-severity note when none are HIGH", () => {
    const result = buildWatchtowerSummary([
      makeAlertCandidate({ severity: "MEDIUM" }),
      makeAlertCandidate({ id: "cand-2", severity: "LOW" }),
    ]);
    expect(result.candidateCount).toBe(2);
    expect(result.highSeverityCount).toBe(0);
    expect(result.summaryLine).toMatch(/2 alert candidates/i);
    expect(result.summaryLine).not.toMatch(/high-severity/i);
  });

  it("reports high-severity count when present", () => {
    const result = buildWatchtowerSummary([
      makeAlertCandidate({ severity: "HIGH" }),
      makeAlertCandidate({ id: "cand-2", severity: "HIGH" }),
      makeAlertCandidate({ id: "cand-3", severity: "MEDIUM" }),
    ]);
    expect(result.candidateCount).toBe(3);
    expect(result.highSeverityCount).toBe(2);
    expect(result.summaryLine).toMatch(/high-severity/i);
  });

  it("is case-insensitive for severity HIGH", () => {
    const result = buildWatchtowerSummary([
      makeAlertCandidate({ severity: "high" }),
    ]);
    expect(result.highSeverityCount).toBe(1);
  });

  it("uses singular form for 1 candidate", () => {
    const result = buildWatchtowerSummary([makeAlertCandidate({ severity: "MEDIUM" })]);
    expect(result.summaryLine).toMatch(/1 alert candidate /i);
  });
});

// ── buildWhyThisMatters ───────────────────────────────────────────────────────

describe("buildWhyThisMatters", () => {
  it("returns why_text when present", () => {
    const card = makeCard({ why_text: "Strong earnings momentum." });
    expect(buildWhyThisMatters(card)).toBe("Strong earnings momentum.");
  });

  it("falls back to rationale when why_text is empty", () => {
    const card = makeCard({ why_text: "" });
    card.detail_drawer_payload.rationale = "Diversified revenue streams.";
    expect(buildWhyThisMatters(card)).toBe("Diversified revenue streams.");
  });

  it("falls back to action_text when why_text and rationale are empty", () => {
    const card = makeCard({ why_text: "", action_text: "Consider trimming." });
    card.detail_drawer_payload.rationale = "";
    expect(buildWhyThisMatters(card)).toBe("Consider trimming.");
  });

  it("returns null when all text fields are empty", () => {
    const card = makeCard({ why_text: "", action_text: "" });
    card.detail_drawer_payload.rationale = "";
    expect(buildWhyThisMatters(card)).toBeNull();
  });

  it("trims whitespace from why_text", () => {
    const card = makeCard({ why_text: "  Momentum intact.  " });
    expect(buildWhyThisMatters(card)).toBe("Momentum intact.");
  });
});

// ── buildLearningSlotCaption (Coming-Later) ───────────────────────────────────

describe("buildLearningSlotCaption", () => {
  it("returns the canonical Coming-Later caption", () => {
    const caption = buildLearningSlotCaption();
    expect(caption).toContain("being prepared");
    expect(caption).toContain("next intelligence stage");
  });

  it("is a non-empty string", () => {
    expect(typeof buildLearningSlotCaption()).toBe("string");
    expect(buildLearningSlotCaption().length).toBeGreaterThan(0);
  });
});
