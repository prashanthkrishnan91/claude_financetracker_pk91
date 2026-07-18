/**
 * Tests for the Advisor cash-plan presentation helpers (Section C).
 * Node environment — pure helpers only, no React, no fetch.
 */

import {
  BUCKET_ORDER,
  BUCKET_TITLES,
  allocationTotals,
  cashPlanErrorMessage,
  cashPlanStateCopy,
  deriveCashPlanState,
  deriveCashPlanTrust,
  formatPercentOfCash,
  groupNotSelected,
  repairActionFromFix,
  translateNextRequiredFix,
  validateCashPlanRequest,
  type AdvisorCashPlanResponse,
  type CashPlanBlockedEntry,
  type CashPlanExplanations,
  type CashPlanSelectedEntry,
} from "@/lib/advisor-cash-plan";

// ── Fixtures ──────────────────────────────────────────────────────────────────

function makeSelected(overrides: Partial<CashPlanSelectedEntry> = {}): CashPlanSelectedEntry {
  return {
    ticker: "VTI",
    asset_type: "etf",
    amount: 500,
    percent_of_deployable_cash: 50,
    reasons: ["Overall ETF allocation floor is not yet met"],
    evidence: null,
    policy_role: "Fills the 40% ETF allocation floor",
    raw_codes: ["etf_floor_not_met"],
    ...overrides,
  };
}

function makeBlocked(overrides: Partial<CashPlanBlockedEntry> = {}): CashPlanBlockedEntry {
  return {
    ticker: "AAPL",
    bucket: "evidence_blocked",
    plain_english:
      "AAPL is not eligible: Its Intel evidence is older than the 24-hour freshness window.",
    raw_codes: ["evidence_stale"],
    ...overrides,
  };
}

function makeExplanations(overrides: Partial<CashPlanExplanations> = {}): CashPlanExplanations {
  return { selected: [], not_selected: [], plan_notes: [], ...overrides };
}

function makeResponse(overrides: Partial<AdvisorCashPlanResponse> = {}): AdvisorCashPlanResponse {
  return {
    preview_version: "paycheck_plan_preview_v1",
    cash_to_deploy: 1000,
    generated_at: "2026-07-18T10:00:00Z",
    trusted: true,
    status: "ready",
    planned_buys: [
      { ticker: "VTI", amount: 500, reason: "x", reason_codes: ["etf_floor_not_met"] },
    ],
    explanations: makeExplanations({ selected: [makeSelected()] }),
    allocation_summary: { allocated_cash: 500, unallocated_cash: 500, allocation_count: 1 },
    data_freshness_status: "full",
    caveats: [],
    next_required_fix: null,
    recommendations_trusted: false,
    source_diagnostic_version: "v1",
    ...overrides,
  };
}

// ── Request validation ────────────────────────────────────────────────────────

describe("validateCashPlanRequest", () => {
  it("accepts a positive cash amount and omits blank optional fields", () => {
    const result = validateCashPlanRequest({ cash: "1000", minTrade: "", maxPositions: null });
    expect(result).toEqual({ ok: true, request: { cash_to_deploy: 1000 } });
  });

  it("rejects zero, negative, blank, and non-numeric cash", () => {
    for (const cash of ["0", "-5", "", "abc"]) {
      const result = validateCashPlanRequest({ cash });
      expect(result.ok).toBe(false);
      if (!result.ok) expect(result.error).toBe("Enter a cash amount greater than 0.");
    }
  });

  it("enforces backend min_trade_amount bound (ge 1)", () => {
    expect(validateCashPlanRequest({ cash: 100, minTrade: 0.5 }).ok).toBe(false);
    const ok = validateCashPlanRequest({ cash: 100, minTrade: 25 });
    expect(ok).toEqual({
      ok: true,
      request: { cash_to_deploy: 100, min_trade_amount: 25 },
    });
  });

  it("enforces backend max_positions bounds (integer, 1..20)", () => {
    expect(validateCashPlanRequest({ cash: 100, maxPositions: 0 }).ok).toBe(false);
    expect(validateCashPlanRequest({ cash: 100, maxPositions: 21 }).ok).toBe(false);
    expect(validateCashPlanRequest({ cash: 100, maxPositions: 2.5 }).ok).toBe(false);
    const ok = validateCashPlanRequest({ cash: 100, maxPositions: 5 });
    expect(ok).toEqual({ ok: true, request: { cash_to_deploy: 100, max_positions: 5 } });
  });
});

// ── Bucket grouping ───────────────────────────────────────────────────────────

describe("groupNotSelected", () => {
  it("groups entries by bucket with plain-English titles in a stable order", () => {
    const groups = groupNotSelected(
      makeExplanations({
        not_selected: [
          makeBlocked({ ticker: "BTC", bucket: "stale_price_blocked", raw_codes: ["stale_price"], plain_english: "BTC has stale price data, so it cannot receive new cash." }),
          makeBlocked({ ticker: "AAPL", bucket: "evidence_blocked", raw_codes: ["evidence_stale"] }),
          makeBlocked({ ticker: "MSFT", bucket: "evidence_blocked", raw_codes: ["evidence_missing_for_ticker"], plain_english: "MSFT is not eligible: No certified Intel evidence exists for this ticker yet." }),
          makeBlocked({ ticker: "NVDA", bucket: "evidence_eligible_policy_blocked", raw_codes: ["at_or_above_position_cap_20pct"], plain_english: "NVDA passed Intel evidence but is blocked by policy: This position is already at or above its per-position concentration cap." }),
        ],
      }),
    );
    expect(groups.map((g) => g.bucket)).toEqual([
      "evidence_eligible_policy_blocked",
      "evidence_blocked",
      "stale_price_blocked",
    ]);
    expect(groups[0].title).toBe("Passed evidence, blocked by policy");
    expect(groups[1].title).toBe("Blocked by evidence");
    expect(groups[1].entries.map((e) => e.ticker)).toEqual(["AAPL", "MSFT"]);
    expect(groups[2].title).toBe("Stale price");
  });

  it("covers every spec bucket with a plain-English title (no underscores)", () => {
    for (const bucket of BUCKET_ORDER) {
      expect(BUCKET_TITLES[bucket]).toBeTruthy();
      expect(BUCKET_TITLES[bucket]).not.toContain("_");
    }
    expect(BUCKET_TITLES["group_cap_blocked"]).toBe("Group cap");
    expect(BUCKET_TITLES["concentration_blocked"]).toBe("Concentration cap");
    expect(BUCKET_TITLES["missing_truth_blocked"]).toBe("Missing price truth");
    expect(BUCKET_TITLES["below_minimum_trade"]).toBe("Below minimum trade");
    expect(BUCKET_TITLES["max_positions_reached"]).toBe("Max positions reached");
  });

  it("keeps raw codes only under technicalDetail — visible text has no raw codes", () => {
    const groups = groupNotSelected(
      makeExplanations({
        not_selected: [
          makeBlocked({ raw_codes: ["evidence_stale", "evidence_confidence_insufficient"] }),
        ],
      }),
    );
    const entry = groups[0].entries[0];
    expect(entry.technicalDetail).toBe("evidence_stale, evidence_confidence_insufficient");
    expect(entry.text).not.toContain("evidence_stale");
    expect(entry.text).not.toContain("_");
    expect(groups[0].title).not.toContain("_");
  });

  it("unknown buckets get a safe title with the bucket key only in technicalDetail", () => {
    const groups = groupNotSelected(
      makeExplanations({
        not_selected: [
          makeBlocked({ bucket: "future_new_bucket", raw_codes: [], plain_english: "X is excluded." }),
        ],
      }),
    );
    expect(groups[0].title).toBe("Not selected");
    expect(groups[0].title).not.toContain("_");
    expect(groups[0].entries[0].technicalDetail).toBe("future_new_bucket");
  });

  it("returns empty array for missing explanations", () => {
    expect(groupNotSelected(null)).toEqual([]);
    expect(groupNotSelected(makeExplanations())).toEqual([]);
  });
});

// ── Totals + formatting ───────────────────────────────────────────────────────

describe("totals and formatting", () => {
  it("allocationTotals reads the allocation summary", () => {
    expect(allocationTotals(makeResponse())).toEqual({
      allocated: 500,
      unallocated: 500,
      count: 1,
    });
    expect(allocationTotals(null)).toEqual({ allocated: 0, unallocated: 0, count: 0 });
  });

  it("formatPercentOfCash formats one decimal or an em dash", () => {
    expect(formatPercentOfCash(50)).toBe("50.0%");
    expect(formatPercentOfCash(33.333)).toBe("33.3%");
    expect(formatPercentOfCash(null)).toBe("—");
    expect(formatPercentOfCash(undefined)).toBe("—");
    expect(formatPercentOfCash(NaN)).toBe("—");
  });
});

// ── Trust derivation ──────────────────────────────────────────────────────────

describe("deriveCashPlanTrust", () => {
  it("trusted plan: Yes, no blocker", () => {
    expect(deriveCashPlanTrust(makeResponse())).toEqual({
      trusted: true,
      label: "Numeric plan trusted: Yes",
      blocker: null,
      blockerTechnicalDetail: null,
    });
  });

  it("untrusted plan: No + plain-English blocker with the exact fix as technical detail", () => {
    const fix = "Run Stage 11B current-price-truth-repair to fill missing price_history rows";
    const trust = deriveCashPlanTrust(makeResponse({ trusted: false, next_required_fix: fix }));
    expect(trust.trusted).toBe(false);
    expect(trust.label).toBe("Numeric plan trusted: No");
    expect(trust.blocker).toBe(
      "A current-price repair is required — some holdings are missing recent prices.",
    );
    expect(trust.blocker).not.toContain("price_history");
    expect(trust.blocker).not.toContain("Stage 11B");
    expect(trust.blockerTechnicalDetail).toBe(fix);
  });

  it("untrusted with no fix still explains honestly", () => {
    const trust = deriveCashPlanTrust(makeResponse({ trusted: false, next_required_fix: null }));
    expect(trust.blocker).toContain("full refresh");
    expect(trust.blockerTechnicalDetail).toBeNull();
  });
});

describe("translateNextRequiredFix", () => {
  it("translates reconciliation fixes", () => {
    const t = translateNextRequiredFix(
      "Reconcile portfolio values — snapshot vs position-derived diverges beyond tolerance",
    );
    expect(t?.plain).toContain("new portfolio snapshot");
    expect(t?.technical).toContain("Reconcile portfolio values");
  });

  it("translates stale-price fixes", () => {
    const t = translateNextRequiredFix(
      "Run Stage 11B current-price-truth-repair to refresh stale price_history rows",
    );
    expect(t?.plain).toBe(
      "A current-price repair is required — some holdings have stale prices.",
    );
  });

  it("keeps 'Resolve blockers' details behind technical detail", () => {
    const fix = "Resolve blockers: reconciliation_diverged; something_else";
    const t = translateNextRequiredFix(fix);
    // "Resolve blockers:" contains "reconcil" in its blocker list — reconciliation wins.
    expect(t?.technical).toBe(fix);
  });

  it("returns null for empty", () => {
    expect(translateNextRequiredFix(null)).toBeNull();
    expect(translateNextRequiredFix("  ")).toBeNull();
  });
});

describe("repairActionFromFix", () => {
  it("classifies fixes into the four plain repair actions", () => {
    expect(
      repairActionFromFix(
        "Reconcile portfolio values — snapshot vs position-derived diverges beyond tolerance",
      ),
    ).toBe("new portfolio snapshot required");
    expect(
      repairActionFromFix(
        "Run Stage 11B current-price-truth-repair to fill missing price_history rows",
      ),
    ).toBe("current-price repair required");
    expect(repairActionFromFix("Refresh Intel evidence for held stocks")).toBe(
      "Run Intel required",
    );
    expect(repairActionFromFix("No immediate fix required — policy is ready")).toBeNull();
    expect(repairActionFromFix(null)).toBeNull();
  });
});

// ── Plan states (all 10) ──────────────────────────────────────────────────────

describe("deriveCashPlanState — 10 states", () => {
  it("trusted-with-etfs", () => {
    const state = deriveCashPlanState({
      response: makeResponse({
        explanations: makeExplanations({ selected: [makeSelected()] }),
      }),
    });
    expect(state).toBe("trusted-with-etfs");
    expect(cashPlanStateCopy(state).tone).toBe("positive");
  });

  it("trusted-with-stock", () => {
    const state = deriveCashPlanState({
      response: makeResponse({
        planned_buys: [
          { ticker: "VTI", amount: 400, reason: "x", reason_codes: [] },
          { ticker: "NVDA", amount: 100, reason: "y", reason_codes: [] },
        ],
        explanations: makeExplanations({
          selected: [
            makeSelected(),
            makeSelected({
              ticker: "NVDA",
              asset_type: "equity",
              policy_role: null,
              evidence: { action: "BUY", evidence_band: "STRONG" },
            }),
          ],
        }),
      }),
    });
    expect(state).toBe("trusted-with-stock");
  });

  it("etf-only-explained via plan_notes", () => {
    const state = deriveCashPlanState({
      response: makeResponse({
        explanations: makeExplanations({
          selected: [makeSelected()],
          plan_notes: [
            "This plan is ETF-only because no individual stock passed Intel evidence freshness and confidence checks.",
          ],
        }),
      }),
    });
    expect(state).toBe("etf-only-explained");
  });

  it("degraded-truth", () => {
    const state = deriveCashPlanState({
      response: makeResponse({
        trusted: false,
        status: "degraded",
        planned_buys: [],
        explanations: makeExplanations(),
        next_required_fix:
          "Run Stage 11B current-price-truth-repair to refresh stale price_history rows",
      }),
    });
    expect(state).toBe("degraded-truth");
    expect(cashPlanStateCopy(state).tone).toBe("caution");
  });

  it("missing-snapshot (no certified Intel evidence for stocks)", () => {
    const state = deriveCashPlanState({
      response: makeResponse({
        trusted: false,
        status: "degraded",
        planned_buys: [],
        explanations: makeExplanations({
          not_selected: [
            makeBlocked({
              raw_codes: ["evidence_missing_for_ticker"],
              plain_english:
                "AAPL is not eligible: No certified Intel evidence exists for this ticker yet.",
            }),
          ],
        }),
      }),
    });
    expect(state).toBe("missing-snapshot");
  });

  it("stale-evidence", () => {
    const state = deriveCashPlanState({
      response: makeResponse({
        trusted: false,
        status: "degraded",
        planned_buys: [],
        explanations: makeExplanations({
          not_selected: [makeBlocked({ raw_codes: ["evidence_stale"] })],
        }),
      }),
    });
    expect(state).toBe("stale-evidence");
  });

  it("partial-run-intel when the readiness run state is partial", () => {
    const state = deriveCashPlanState({
      response: makeResponse({ trusted: false, status: "degraded", planned_buys: [] }),
      runState: "partial",
    });
    expect(state).toBe("partial-run-intel");
  });

  it("no-candidate-above-min-trade for a trusted ready plan with zero buys", () => {
    const state = deriveCashPlanState({
      response: makeResponse({
        planned_buys: [],
        explanations: makeExplanations({
          plan_notes: [
            "No candidate could receive at least the minimum trade amount, so no buys are planned.",
          ],
        }),
        allocation_summary: { allocated_cash: 0, unallocated_cash: 1000, allocation_count: 0 },
      }),
    });
    expect(state).toBe("no-candidate-above-min-trade");
  });

  it("backend-error", () => {
    expect(deriveCashPlanState({ hadError: true, errorStatus: 502 })).toBe("backend-error");
    expect(deriveCashPlanState({ hadError: true })).toBe("backend-error");
    expect(deriveCashPlanState({ response: null })).toBe("backend-error");
  });

  it("auth-error on 401", () => {
    const state = deriveCashPlanState({ hadError: true, errorStatus: 401 });
    expect(state).toBe("auth-error");
    expect(cashPlanErrorMessage(401)).toContain("Sign in again");
  });

  it("503 maps to a cert-not-configured message", () => {
    expect(deriveCashPlanState({ hadError: true, errorStatus: 503 })).toBe("backend-error");
    expect(cashPlanErrorMessage(503)).toContain("not configured");
  });

  it("every state has copy and no raw codes or execution language in headlines", () => {
    const states = [
      "trusted-with-etfs",
      "trusted-with-stock",
      "etf-only-explained",
      "degraded-truth",
      "missing-snapshot",
      "stale-evidence",
      "partial-run-intel",
      "no-candidate-above-min-trade",
      "backend-error",
      "auth-error",
    ] as const;
    for (const state of states) {
      const copy = cashPlanStateCopy(state);
      expect(copy.headline.length).toBeGreaterThan(0);
      expect(copy.headline).not.toContain("_");
      expect(copy.headline.toLowerCase()).not.toContain("order");
      expect(copy.headline.toLowerCase()).not.toContain("execute");
    }
  });
});
