/**
 * Tests for the Advisor financial-truth contract:
 *  - server-side mapping from the raw financial-truth-baseline diagnostic
 *  - the honest all-unknown fallback (503 / upstream failure / malformed)
 *  - trust-panel health derivation (unknown is never healthy)
 *
 * Node environment — pure helpers only, no React, no fetch.
 */

import {
  UNKNOWN_ADVISOR_TRUTH,
  deriveTrustHealth,
  mapFinancialTruthBaseline,
  sanitizeAdvisorTruth,
  type AdvisorTruthContract,
} from "@/lib/advisor-truth";

// ── Fixtures ──────────────────────────────────────────────────────────────────

function makeDiagnostic(overrides: {
  truth_status?: string;
  next_required_fix?: string | null;
  price_status?: string;
  missing_price_tickers?: unknown[];
  stale_price_tickers?: unknown[];
  reconciliation_status?: string;
  snapshot_value?: number | null;
  position_mv?: number | null;
  snapshot_is_stale?: boolean | null;
  generated_at?: string | null;
} = {}): unknown {
  return {
    diagnostic_version: "v1",
    generated_at: overrides.generated_at ?? "2026-07-18T10:00:00+00:00",
    user_id: "user_1",
    snapshot_truth: {
      status: "ok",
      latest_portfolio_value: overrides.snapshot_value ?? 100_000,
      snapshot_is_stale: overrides.snapshot_is_stale ?? false,
    },
    position_derived_truth: {
      status: "ok",
      market_value_sum: overrides.position_mv ?? 100_050,
    },
    transaction_derived_truth: { status: "ok" },
    price_truth: {
      status: overrides.price_status ?? "ok",
      missing_price_tickers: overrides.missing_price_tickers ?? [],
      stale_price_tickers: overrides.stale_price_tickers ?? [],
    },
    intelligence_layer: {},
    reconciliation: {
      reconciliation_status: overrides.reconciliation_status ?? "pass",
    },
    verdict: {
      truth_status: overrides.truth_status ?? "certified",
      next_required_fix:
        overrides.next_required_fix === undefined
          ? "No immediate fix required — financial truth is certified"
          : overrides.next_required_fix,
      recommendations_trusted: false,
    },
  };
}

// ── Contract mapping ──────────────────────────────────────────────────────────

describe("mapFinancialTruthBaseline — verdict → portfolio_truth", () => {
  it("maps certified / degraded / blocked verbatim", () => {
    expect(mapFinancialTruthBaseline(makeDiagnostic({ truth_status: "certified" })).portfolio_truth).toBe("certified");
    expect(mapFinancialTruthBaseline(makeDiagnostic({ truth_status: "degraded" })).portfolio_truth).toBe("degraded");
    expect(mapFinancialTruthBaseline(makeDiagnostic({ truth_status: "blocked" })).portfolio_truth).toBe("blocked");
  });

  it("unrecognized or missing verdict → unknown", () => {
    expect(mapFinancialTruthBaseline(makeDiagnostic({ truth_status: "weird" })).portfolio_truth).toBe("unknown");
    expect(mapFinancialTruthBaseline({}).portfolio_truth).toBe("unknown");
    expect(mapFinancialTruthBaseline(null).portfolio_truth).toBe("unknown");
  });
});

describe("mapFinancialTruthBaseline — price_truth section → price_truth", () => {
  it("ok when checked and nothing missing or stale", () => {
    expect(mapFinancialTruthBaseline(makeDiagnostic()).price_truth).toBe("ok");
  });

  it("stale when stale tickers exist", () => {
    const mapped = mapFinancialTruthBaseline(
      makeDiagnostic({ stale_price_tickers: [{ ticker: "VTI" }] }),
    );
    expect(mapped.price_truth).toBe("stale");
  });

  it("missing wins over stale when both exist", () => {
    const mapped = mapFinancialTruthBaseline(
      makeDiagnostic({
        missing_price_tickers: ["AAPL"],
        stale_price_tickers: [{ ticker: "VTI" }],
      }),
    );
    expect(mapped.price_truth).toBe("missing");
  });

  it("section unavailable (no open positions / query failed) → unknown", () => {
    const mapped = mapFinancialTruthBaseline(
      makeDiagnostic({ price_status: "unavailable" }),
    );
    expect(mapped.price_truth).toBe("unknown");
  });
});

describe("mapFinancialTruthBaseline — reconciliation → reconciliation", () => {
  it("maps pass / degraded / blocked verbatim", () => {
    expect(mapFinancialTruthBaseline(makeDiagnostic({ reconciliation_status: "pass" })).reconciliation).toBe("pass");
    expect(mapFinancialTruthBaseline(makeDiagnostic({ reconciliation_status: "degraded" })).reconciliation).toBe("degraded");
    expect(mapFinancialTruthBaseline(makeDiagnostic({ reconciliation_status: "blocked" })).reconciliation).toBe("blocked");
  });

  it('"unavailable" and unrecognized statuses → unknown', () => {
    expect(mapFinancialTruthBaseline(makeDiagnostic({ reconciliation_status: "unavailable" })).reconciliation).toBe("unknown");
    expect(mapFinancialTruthBaseline(makeDiagnostic({ reconciliation_status: "??" })).reconciliation).toBe("unknown");
  });
});

describe("mapFinancialTruthBaseline — values, staleness, repair, as_of", () => {
  it("passes through numeric values, staleness, repair, and timestamp", () => {
    const mapped = mapFinancialTruthBaseline(
      makeDiagnostic({
        snapshot_value: 123_456.78,
        position_mv: 120_000.5,
        snapshot_is_stale: true,
        next_required_fix: "Backfill price_history for missing/stale tickers",
        generated_at: "2026-07-18T09:30:00+00:00",
      }),
    );
    expect(mapped.snapshot_value).toBe(123_456.78);
    expect(mapped.position_derived_value).toBe(120_000.5);
    expect(mapped.snapshot_stale).toBe(true);
    expect(mapped.next_required_repair).toBe(
      "Backfill price_history for missing/stale tickers",
    );
    expect(mapped.as_of).toBe("2026-07-18T09:30:00+00:00");
  });

  it("nulls stay null and non-numeric values collapse to null", () => {
    const mapped = mapFinancialTruthBaseline({
      snapshot_truth: { latest_portfolio_value: "not-a-number", snapshot_is_stale: null },
      position_derived_truth: { market_value_sum: null },
      verdict: { truth_status: "blocked", next_required_fix: null },
    });
    expect(mapped.snapshot_value).toBeNull();
    expect(mapped.position_derived_value).toBeNull();
    expect(mapped.snapshot_stale).toBeNull();
    expect(mapped.next_required_repair).toBeNull();
    expect(mapped.as_of).toBeNull();
  });
});

// ── Unavailable endpoint → all unknown ────────────────────────────────────────

describe("unavailable endpoint contract", () => {
  it("UNKNOWN_ADVISOR_TRUTH is honest: every dimension unknown, every value null", () => {
    expect(UNKNOWN_ADVISOR_TRUTH.portfolio_truth).toBe("unknown");
    expect(UNKNOWN_ADVISOR_TRUTH.price_truth).toBe("unknown");
    expect(UNKNOWN_ADVISOR_TRUTH.reconciliation).toBe("unknown");
    expect(UNKNOWN_ADVISOR_TRUTH.snapshot_value).toBeNull();
    expect(UNKNOWN_ADVISOR_TRUTH.position_derived_value).toBeNull();
    expect(UNKNOWN_ADVISOR_TRUTH.snapshot_stale).toBeNull();
    expect(UNKNOWN_ADVISOR_TRUTH.next_required_repair).toBeNull();
    expect(UNKNOWN_ADVISOR_TRUTH.as_of).toBeNull();
  });

  it("sanitizeAdvisorTruth coerces malformed payloads to unknown, never ok", () => {
    expect(sanitizeAdvisorTruth(null)).toEqual(UNKNOWN_ADVISOR_TRUTH);
    expect(sanitizeAdvisorTruth("garbage")).toEqual(UNKNOWN_ADVISOR_TRUTH);
    expect(sanitizeAdvisorTruth({ portfolio_truth: "certified!!", price_truth: 1 })).toEqual(
      UNKNOWN_ADVISOR_TRUTH,
    );
    // Valid contract passes through unchanged.
    const valid: AdvisorTruthContract = {
      portfolio_truth: "degraded",
      price_truth: "stale",
      reconciliation: "pass",
      snapshot_value: 10,
      position_derived_value: 11,
      snapshot_stale: false,
      next_required_repair: "Backfill price_history for missing/stale tickers",
      as_of: "2026-07-18T09:30:00+00:00",
    };
    expect(sanitizeAdvisorTruth(valid)).toEqual(valid);
  });
});

// ── Trust health derivation ───────────────────────────────────────────────────

const CERTIFIED_TRUTH: AdvisorTruthContract = {
  portfolio_truth: "certified",
  price_truth: "ok",
  reconciliation: "pass",
  snapshot_value: 100_000,
  position_derived_value: 100_050,
  snapshot_stale: false,
  next_required_repair: "No immediate fix required — financial truth is certified",
  as_of: "2026-07-18T10:00:00+00:00",
};

describe("deriveTrustHealth — healthy only under the full rule", () => {
  it("healthy: Intel certified+current AND certified/ok/pass AND no cash plan requested", () => {
    const health = deriveTrustHealth({
      intelCertifiedCurrent: true,
      truth: CERTIFIED_TRUTH,
      planRequested: false,
      numericPlanTrusted: null,
    });
    expect(health.healthy).toBe(true);
    expect(health.state).toBe("healthy");
    expect(health.unknownDimensions).toEqual([]);
    expect(health.truthProblems).toEqual([]);
    expect(health.repairPlain).toBeNull();
  });

  it("healthy with a trusted cash plan; NOT healthy with an untrusted one", () => {
    const trusted = deriveTrustHealth({
      intelCertifiedCurrent: true,
      truth: CERTIFIED_TRUTH,
      planRequested: true,
      numericPlanTrusted: true,
    });
    expect(trusted.healthy).toBe(true);

    const untrusted = deriveTrustHealth({
      intelCertifiedCurrent: true,
      truth: CERTIFIED_TRUTH,
      planRequested: true,
      numericPlanTrusted: false,
    });
    expect(untrusted.healthy).toBe(false);
    expect(untrusted.state).toBe("degraded");
  });

  it("Intel not certified blocks healthy even when financial truth is all green", () => {
    const health = deriveTrustHealth({
      intelCertifiedCurrent: false,
      truth: CERTIFIED_TRUTH,
      planRequested: false,
      numericPlanTrusted: null,
    });
    expect(health.healthy).toBe(false);
    expect(health.state).toBe("degraded");
  });

  it("unknown is never healthy: all-unknown truth → unknown-checks state", () => {
    const health = deriveTrustHealth({
      intelCertifiedCurrent: true,
      truth: UNKNOWN_ADVISOR_TRUTH,
      planRequested: false,
      numericPlanTrusted: null,
    });
    expect(health.healthy).toBe(false);
    expect(health.state).toBe("unknown-checks");
    expect(health.unknownDimensions).toEqual([
      "Portfolio financial truth",
      "Current-price truth",
      "Books reconciliation",
    ]);
  });

  it("null truth (endpoint never fetched) reads exactly like all-unknown", () => {
    const health = deriveTrustHealth({
      intelCertifiedCurrent: true,
      truth: null,
      planRequested: false,
      numericPlanTrusted: null,
    });
    expect(health.healthy).toBe(false);
    expect(health.state).toBe("unknown-checks");
    expect(health.unknownDimensions).toHaveLength(3);
  });

  it("reconciliation unknown alone blocks healthy and is listed by name", () => {
    const health = deriveTrustHealth({
      intelCertifiedCurrent: true,
      truth: { ...CERTIFIED_TRUTH, reconciliation: "unknown" },
      planRequested: false,
      numericPlanTrusted: null,
    });
    expect(health.healthy).toBe(false);
    expect(health.state).toBe("unknown-checks");
    expect(health.unknownDimensions).toEqual(["Books reconciliation"]);
  });

  it("price degraded (stale) blocks healthy with a plain-English problem", () => {
    const health = deriveTrustHealth({
      intelCertifiedCurrent: true,
      truth: { ...CERTIFIED_TRUTH, price_truth: "stale" },
      planRequested: false,
      numericPlanTrusted: null,
    });
    expect(health.healthy).toBe(false);
    expect(health.state).toBe("degraded");
    expect(health.truthProblems.join(" ")).toContain("stale current prices");
  });

  it("price missing blocks healthy", () => {
    const health = deriveTrustHealth({
      intelCertifiedCurrent: true,
      truth: { ...CERTIFIED_TRUTH, price_truth: "missing" },
      planRequested: false,
      numericPlanTrusted: null,
    });
    expect(health.healthy).toBe(false);
    expect(health.truthProblems.join(" ")).toContain("missing current prices");
  });
});

describe("deriveTrustHealth — reconciliation failure surfaces the real repair", () => {
  const RAW_RECON_FIX =
    "Investigate 12.4% divergence between snapshot value and position-derived value; " +
    "reconcile position shares or refresh portfolio_snapshots";

  it("blocked reconciliation exposes the translated repair + exact raw string", () => {
    const health = deriveTrustHealth({
      intelCertifiedCurrent: true,
      truth: {
        ...CERTIFIED_TRUTH,
        portfolio_truth: "blocked",
        reconciliation: "blocked",
        next_required_repair: RAW_RECON_FIX,
      },
      planRequested: false,
      numericPlanTrusted: null,
    });
    expect(health.healthy).toBe(false);
    expect(health.state).toBe("degraded");
    expect(health.truthProblems.join(" ")).toContain("diverge beyond tolerance");
    // Plain English — never the raw operator sentence in the visible string.
    expect(health.repairPlain).toContain("new portfolio snapshot is required");
    expect(health.repairPlain).not.toContain("portfolio_snapshots");
    // Exact raw string preserved for the technical-detail expander.
    expect(health.repairTechnical).toBe(RAW_RECON_FIX);
  });

  it("degraded price repair is translated with technical detail preserved", () => {
    const raw = "Backfill price_history for missing/stale tickers";
    const health = deriveTrustHealth({
      intelCertifiedCurrent: true,
      truth: {
        ...CERTIFIED_TRUTH,
        portfolio_truth: "degraded",
        price_truth: "stale",
        next_required_repair: raw,
      },
      planRequested: false,
      numericPlanTrusted: null,
    });
    expect(health.repairPlain).toContain("current-price repair is required");
    expect(health.repairTechnical).toBe(raw);
  });

  it('the no-op fix ("No immediate fix required") never renders as a repair', () => {
    const health = deriveTrustHealth({
      intelCertifiedCurrent: false, // not healthy for another reason
      truth: CERTIFIED_TRUTH,
      planRequested: false,
      numericPlanTrusted: null,
    });
    expect(health.repairPlain).toBeNull();
    expect(health.repairTechnical).toBeNull();
  });
});
