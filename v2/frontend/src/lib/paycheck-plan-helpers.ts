/**
 * Pure Paycheck Plan Preview helper constants and functions (Stage 12E).
 * No external dependencies — safe to import in tests without Supabase env vars.
 *
 * Wraps the Stage 12D read-only preview contract
 * (POST /api/v1/advisor/paycheck-plan/preview) for UI consumption. This
 * module contains no allocation math — it only maps backend reason codes
 * and status values to plain-English UI copy.
 */

// ── Endpoint / query key constants ────────────────────────────────────────────

/** Client-facing proxy route — never called directly from the browser. */
export const PAYCHECK_PLAN_PREVIEW_ENDPOINT = "/api/advisor/paycheck-plan/preview";

/** React Query key for usePaycheckPlanPreview. */
export const PAYCHECK_PLAN_PREVIEW_QUERY_KEY = ["paycheck_plan", "preview"] as const;

export const PAYCHECK_PLAN_PREVIEW_SAMPLE_CASH = 2737.5;

// ── Types ──────────────────────────────────────────────────────────────────────

export type PaycheckPlanPreviewStatus = "ready" | "degraded" | "blocked";

export type PaycheckPlannedBuy = {
  ticker: string;
  amount: number;
  reason: string;
  reason_codes: string[];
};

export type PaycheckPlanPreviewResponse = {
  preview_version: string;
  cash_to_deploy: number;
  trusted: boolean;
  status: PaycheckPlanPreviewStatus | string;
  planned_buys: PaycheckPlannedBuy[];
  allocation_summary: {
    allocated_cash: number;
    unallocated_cash: number;
    allocation_count: number;
  };
  data_freshness_status: string;
  caveats: string[];
  next_required_fix: string | null;
  recommendations_trusted: boolean;
  source_diagnostic_version: string;
};

// ── Reason code → plain-English UI copy ───────────────────────────────────────
// Deliberately does not reuse the backend's semicolon-joined `reason` string —
// that copy is written for diagnostics, not the product surface. VTI/SPY
// wording is chosen so SPY never reads as more preferred than VTI.

const REASON_CODE_COPY: Record<string, string> = {
  etf_floor_not_met: "ETF allocation is below the conservative policy floor.",
  broad_index_etf_group_underweight: "Broad-market ETFs are underweight versus policy.",
  core_etf_preference: "Core ETF preference applied.",
  preferred_vti_over_spy: "VTI is prioritized ahead of SPY by policy.",
};

const MAX_REASON_BULLETS = 2;

/** Map a single backend reason code to concise UI copy. Falls back to a generic label for unmapped codes. */
export function reasonCodeCopy(code: string): string {
  if (code in REASON_CODE_COPY) return REASON_CODE_COPY[code];
  if (code.endsWith("_group_underweight")) {
    return "This asset group is underweight versus policy.";
  }
  return "Below its target allocation weight.";
}

/** Map a planned buy's reason_codes to at most MAX_REASON_BULLETS concise UI bullets. */
export function planBuyReasonBullets(buy: Pick<PaycheckPlannedBuy, "reason_codes">): string[] {
  const codes = buy.reason_codes ?? [];
  const bullets = codes.map(reasonCodeCopy);
  return Array.from(new Set(bullets)).slice(0, MAX_REASON_BULLETS);
}

// ── Status → plain-English badge copy ─────────────────────────────────────────

export type PreviewStatusMeta = { label: string; cls: string; actionable: boolean };

export const PREVIEW_STATUS_META: Record<string, PreviewStatusMeta> = {
  ready: { label: "Plan ready", cls: "text-action-buy", actionable: true },
  degraded: { label: "Plan degraded — treat as directional only", cls: "text-action-hold", actionable: false },
  blocked: { label: "Plan blocked", cls: "text-action-sell", actionable: false },
};

export function previewStatusMeta(status: string): PreviewStatusMeta {
  return (
    PREVIEW_STATUS_META[status] ?? {
      label: "Plan status unknown",
      cls: "text-text-muted",
      actionable: false,
    }
  );
}

/**
 * Whether the plan can be presented as actionable in the UI.
 * Requires both a "ready" status and trusted=true — either alone is not enough.
 */
export function isPlanActionable(preview: Pick<PaycheckPlanPreviewResponse, "status" | "trusted">): boolean {
  return preview.status === "ready" && preview.trusted === true;
}

/** Sort planned buys so VTI always appears before SPY when both are present, preserving backend order otherwise. */
export function sortPlannedBuys(buys: PaycheckPlannedBuy[]): PaycheckPlannedBuy[] {
  const priority = (ticker: string) => (ticker === "VTI" ? 0 : ticker === "SPY" ? 1 : 2);
  return [...buys].sort((a, b) => priority(a.ticker) - priority(b.ticker));
}
