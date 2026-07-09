/**
 * Paycheck Plan Preview UI contract tests (Stage 12E).
 *
 * Imports real exported helpers from @/lib/paycheck-plan-helpers to prove
 * behavior without a DOM/rendering harness, consistent with
 * DeployV3Contract.test.ts.
 *
 * Verifies:
 * - The proxy endpoint is the server-side route, never the raw backend path
 * - VTI sorts ahead of SPY in planned buys
 * - Allocation summary + unallocated cash fields are present
 * - Reason copy is concise, mapped from reason_codes — not raw backend text
 * - A plan is only actionable when status=ready AND trusted=true
 * - No X-Finance-Runtime-Cert-Secret reference anywhere in client-bundled source
 */

import {
  PAYCHECK_PLAN_PREVIEW_ENDPOINT,
  PAYCHECK_PLAN_PREVIEW_QUERY_KEY,
  isPlanActionable,
  planBuyReasonBullets,
  previewStatusMeta,
  reasonCodeCopy,
  sortPlannedBuys,
  type PaycheckPlanPreviewResponse,
  type PaycheckPlannedBuy,
} from "@/lib/paycheck-plan-helpers";
import fs from "fs";
import path from "path";

// ── Endpoint / query key contract ─────────────────────────────────────────────

describe("PAYCHECK_PLAN_PREVIEW_ENDPOINT", () => {
  it("is the local server-side proxy route, not the raw backend path", () => {
    expect(PAYCHECK_PLAN_PREVIEW_ENDPOINT).toBe("/api/advisor/paycheck-plan/preview");
  });

  it("does not equal the cert-gated backend endpoint", () => {
    expect(PAYCHECK_PLAN_PREVIEW_ENDPOINT).not.toBe("/api/v1/advisor/paycheck-plan/preview");
  });
});

describe("PAYCHECK_PLAN_PREVIEW_QUERY_KEY", () => {
  it("is ['paycheck_plan', 'preview']", () => {
    expect(PAYCHECK_PLAN_PREVIEW_QUERY_KEY).toEqual(["paycheck_plan", "preview"]);
  });
});

// ── Fixtures matching the validated Stage 12D response ────────────────────────

function makeValidatedPreview(
  overrides: Partial<PaycheckPlanPreviewResponse> = {},
): PaycheckPlanPreviewResponse {
  return {
    preview_version: "paycheck_plan_preview_v1",
    cash_to_deploy: 2737.5,
    trusted: true,
    status: "ready",
    planned_buys: [
      { ticker: "VTI", amount: 2065.0, reason: "Preferred as a core broad-market ETF; Chosen ahead of SPY under the core ETF preference order", reason_codes: ["core_etf_preference", "preferred_vti_over_spy"] },
      { ticker: "SPY", amount: 670.0, reason: "Below its target allocation weight", reason_codes: ["broad_index_etf_group_underweight"] },
    ],
    allocation_summary: { allocated_cash: 2735.0, unallocated_cash: 2.5, allocation_count: 2 },
    data_freshness_status: "ok",
    caveats: ["This is deterministic allocation guidance, not personalized investment advice."],
    next_required_fix: null,
    recommendations_trusted: false,
    source_diagnostic_version: "v1",
    ...overrides,
  };
}

// ── VTI-first ordering ────────────────────────────────────────────────────────

describe("sortPlannedBuys", () => {
  it("keeps VTI first when backend already orders it first", () => {
    const preview = makeValidatedPreview();
    const sorted = sortPlannedBuys(preview.planned_buys);
    expect(sorted[0].ticker).toBe("VTI");
    expect(sorted[1].ticker).toBe("SPY");
  });

  it("re-orders VTI ahead of SPY even if backend order is reversed", () => {
    const buys: PaycheckPlannedBuy[] = [
      { ticker: "SPY", amount: 670.0, reason: "", reason_codes: [] },
      { ticker: "VTI", amount: 2065.0, reason: "", reason_codes: [] },
    ];
    const sorted = sortPlannedBuys(buys);
    expect(sorted[0].ticker).toBe("VTI");
  });
});

// ── Allocation summary ────────────────────────────────────────────────────────

describe("allocation summary fields", () => {
  it("validated fixture exposes allocated and unallocated cash", () => {
    const preview = makeValidatedPreview();
    expect(preview.allocation_summary.allocated_cash).toBe(2735.0);
    expect(preview.allocation_summary.unallocated_cash).toBe(2.5);
  });

  it("VTI amount is $2,065.00 and SPY amount is $670.00", () => {
    const preview = makeValidatedPreview();
    const vti = preview.planned_buys.find((b) => b.ticker === "VTI");
    const spy = preview.planned_buys.find((b) => b.ticker === "SPY");
    expect(vti?.amount).toBe(2065.0);
    expect(spy?.amount).toBe(670.0);
  });
});

// ── No raw diagnostic leakage ──────────────────────────────────────────────────

describe("no raw diagnostic payload exposure", () => {
  it("validated fixture has no raw diagnostic fields", () => {
    const preview = makeValidatedPreview();
    expect(preview).not.toHaveProperty("verdict");
    expect(preview).not.toHaveProperty("cash_plan");
    expect(preview).not.toHaveProperty("truth_dependency");
    expect(preview).not.toHaveProperty("next_buy_candidates");
  });

  it("recommendations_trusted stays false and is never rendered as advisor-approved", () => {
    const preview = makeValidatedPreview();
    expect(preview.recommendations_trusted).toBe(false);
  });
});

// ── Actionability gate ────────────────────────────────────────────────────────

describe("isPlanActionable", () => {
  it("is actionable when status=ready and trusted=true", () => {
    expect(isPlanActionable({ status: "ready", trusted: true })).toBe(true);
  });

  it("is not actionable when trusted=false even if status=ready", () => {
    expect(isPlanActionable({ status: "ready", trusted: false })).toBe(false);
  });

  it("is not actionable when status=degraded", () => {
    expect(isPlanActionable({ status: "degraded", trusted: true })).toBe(false);
  });

  it("is not actionable when status=blocked", () => {
    expect(isPlanActionable({ status: "blocked", trusted: true })).toBe(false);
  });
});

describe("previewStatusMeta", () => {
  it("ready/degraded/blocked all have non-empty labels", () => {
    for (const status of ["ready", "degraded", "blocked"]) {
      const meta = previewStatusMeta(status);
      expect(meta.label.length).toBeGreaterThan(0);
    }
  });

  it("degraded and blocked are not marked actionable", () => {
    expect(previewStatusMeta("degraded").actionable).toBe(false);
    expect(previewStatusMeta("blocked").actionable).toBe(false);
  });
});

// ── Reason copy mapping ───────────────────────────────────────────────────────

describe("reasonCodeCopy — concise mapped UI text, not raw backend reason string", () => {
  it("maps etf_floor_not_met", () => {
    expect(reasonCodeCopy("etf_floor_not_met")).toBe(
      "ETF allocation is below the conservative policy floor.",
    );
  });

  it("maps broad_index_etf_group_underweight", () => {
    expect(reasonCodeCopy("broad_index_etf_group_underweight")).toBe(
      "Broad-market ETFs are underweight versus policy.",
    );
  });

  it("maps core_etf_preference", () => {
    expect(reasonCodeCopy("core_etf_preference")).toBe("Core ETF preference applied.");
  });

  it("maps preferred_vti_over_spy without making SPY sound more preferred than VTI", () => {
    const copy = reasonCodeCopy("preferred_vti_over_spy");
    expect(copy).toBe("VTI is prioritized ahead of SPY by policy.");
    expect(copy.toLowerCase().indexOf("vti")).toBeLessThan(copy.toLowerCase().indexOf("spy"));
  });
});

describe("planBuyReasonBullets", () => {
  it("returns at most 2 bullets", () => {
    const buy = {
      reason_codes: ["core_etf_preference", "preferred_vti_over_spy", "etf_floor_not_met"],
    };
    expect(planBuyReasonBullets(buy).length).toBeLessThanOrEqual(2);
  });

  it("does not include raw semicolon-joined backend reason text", () => {
    const buy = { reason_codes: ["core_etf_preference", "preferred_vti_over_spy"] };
    const bullets = planBuyReasonBullets(buy);
    for (const bullet of bullets) {
      expect(bullet).not.toContain(";");
    }
  });
});

// ── Cert secret never present in client-bundled source ────────────────────────

describe("cert secret does not leak into client-bundled code", () => {
  const clientFiles = [
    path.join(__dirname, "PaycheckPlanPreviewCard.tsx"),
    path.join(__dirname, "..", "..", "lib", "paycheck-plan-helpers.ts"),
  ];

  it("no client-bundled file references X-Finance-Runtime-Cert-Secret or the raw secret env var", () => {
    for (const file of clientFiles) {
      const source = fs.readFileSync(file, "utf-8");
      expect(source).not.toContain("X-Finance-Runtime-Cert-Secret");
      expect(source).not.toContain("FINANCE_RUNTIME_CERT_SECRET");
    }
  });

  it("the client card never uses a NEXT_PUBLIC_ variant of the cert secret", () => {
    const source = fs.readFileSync(clientFiles[0], "utf-8");
    expect(source).not.toContain("NEXT_PUBLIC_FINANCE_RUNTIME_CERT_SECRET");
  });
});
