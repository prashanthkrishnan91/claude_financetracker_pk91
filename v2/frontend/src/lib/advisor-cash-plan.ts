/**
 * Advisor cash-plan presentation helpers — pure (no React, no fetch).
 *
 * Wraps the evolved paycheck-plan preview contract
 * (POST /api/advisor/paycheck-plan/preview via the local route handler),
 * including the additive `generated_at` + `explanations` keys, for the
 * Section C cash-plan UI. This module contains NO allocation math — it only
 * validates requests and maps backend fields to plain-English presentation.
 *
 * Vocabulary rule: this surface always says "plan" — never order/execute.
 * Raw backend codes are exposed only via `technicalDetail` fields, never in
 * visible strings.
 */

import type { PaycheckPlanPreviewResponse } from "@/lib/paycheck-plan-helpers";

// ── Evolved response types (additive keys over the Stage 12D contract) ────────

export interface CashPlanEvidenceSummary {
  action: string;
  evidence_band: string;
}

export interface CashPlanSelectedEntry {
  ticker: string;
  asset_type: string;
  amount: number;
  percent_of_deployable_cash: number | null;
  reasons: string[];
  evidence: CashPlanEvidenceSummary | null;
  policy_role: string | null;
  raw_codes: string[];
}

export interface CashPlanBlockedEntry {
  ticker: string;
  bucket: string;
  plain_english: string;
  raw_codes: string[];
}

export interface CashPlanExplanations {
  selected: CashPlanSelectedEntry[];
  not_selected: CashPlanBlockedEntry[];
  plan_notes: string[];
}

export interface AdvisorCashPlanResponse extends PaycheckPlanPreviewResponse {
  generated_at?: string | null;
  explanations?: CashPlanExplanations | null;
}

// ── Request validation (mirrors backend Field bounds) ─────────────────────────

/** Backend PaycheckPlanPreviewRequest bounds: cash gt 0, min_trade ge 1, max_positions ge 1 le 20. */
export const CASH_PLAN_LIMITS = {
  minTradeMin: 1,
  maxPositionsMin: 1,
  maxPositionsMax: 20,
} as const;

export interface CashPlanRequestInput {
  cash: string | number;
  minTrade?: string | number | null;
  maxPositions?: string | number | null;
}

export interface CashPlanRequestBody {
  cash_to_deploy: number;
  min_trade_amount?: number;
  max_positions?: number;
}

export type CashPlanValidation =
  | { ok: true; request: CashPlanRequestBody }
  | { ok: false; error: string };

function isBlank(value: string | number | null | undefined): boolean {
  return value === null || value === undefined || String(value).trim() === "";
}

export function validateCashPlanRequest(input: CashPlanRequestInput): CashPlanValidation {
  const cash = Number(input.cash);
  if (isBlank(input.cash) || !Number.isFinite(cash) || cash <= 0) {
    return { ok: false, error: "Enter a cash amount greater than 0." };
  }

  const request: CashPlanRequestBody = { cash_to_deploy: cash };

  if (!isBlank(input.minTrade)) {
    const minTrade = Number(input.minTrade);
    if (!Number.isFinite(minTrade) || minTrade < CASH_PLAN_LIMITS.minTradeMin) {
      return {
        ok: false,
        error: `Minimum trade must be at least $${CASH_PLAN_LIMITS.minTradeMin}.`,
      };
    }
    request.min_trade_amount = minTrade;
  }

  if (!isBlank(input.maxPositions)) {
    const maxPositions = Number(input.maxPositions);
    if (
      !Number.isInteger(maxPositions) ||
      maxPositions < CASH_PLAN_LIMITS.maxPositionsMin ||
      maxPositions > CASH_PLAN_LIMITS.maxPositionsMax
    ) {
      return {
        ok: false,
        error: `Max positions must be a whole number between ${CASH_PLAN_LIMITS.maxPositionsMin} and ${CASH_PLAN_LIMITS.maxPositionsMax}.`,
      };
    }
    request.max_positions = maxPositions;
  }

  return { ok: true, request };
}

// ── Not-selected bucket grouping ──────────────────────────────────────────────

export const BUCKET_TITLES: Record<string, string> = {
  evidence_eligible_policy_blocked: "Passed evidence, blocked by policy",
  evidence_blocked: "Blocked by evidence",
  concentration_blocked: "Concentration cap",
  group_cap_blocked: "Group cap",
  stale_price_blocked: "Stale price",
  missing_truth_blocked: "Missing price truth",
  below_minimum_trade: "Below minimum trade",
  max_positions_reached: "Max positions reached",
};

export const BUCKET_ORDER: string[] = [
  "evidence_eligible_policy_blocked",
  "evidence_blocked",
  "concentration_blocked",
  "group_cap_blocked",
  "stale_price_blocked",
  "missing_truth_blocked",
  "below_minimum_trade",
  "max_positions_reached",
];

export interface CashPlanBucketEntry {
  ticker: string;
  /** Plain-English visible line (never raw codes). */
  text: string;
  /** Raw backend codes, only ever shown behind an explicit "Technical detail" expander. */
  technicalDetail: string | null;
}

export interface CashPlanBucketGroup {
  bucket: string;
  title: string;
  entries: CashPlanBucketEntry[];
}

export function groupNotSelected(
  explanations: CashPlanExplanations | null | undefined,
): CashPlanBucketGroup[] {
  const entries = explanations?.not_selected ?? [];
  if (entries.length === 0) return [];

  const byBucket = new Map<string, CashPlanBucketEntry[]>();
  for (const entry of entries) {
    const bucket = entry.bucket || "other";
    const list = byBucket.get(bucket) ?? [];
    const codes = entry.raw_codes ?? [];
    const knownBucket = bucket in BUCKET_TITLES;
    list.push({
      ticker: entry.ticker,
      text: entry.plain_english,
      technicalDetail:
        codes.length > 0
          ? codes.join(", ")
          : knownBucket
            ? null
            : bucket,
    });
    byBucket.set(bucket, list);
  }

  const orderedBuckets = [
    ...BUCKET_ORDER.filter((b) => byBucket.has(b)),
    ...Array.from(byBucket.keys()).filter((b) => !BUCKET_ORDER.includes(b)),
  ];

  return orderedBuckets.map((bucket) => ({
    bucket,
    title: BUCKET_TITLES[bucket] ?? "Not selected",
    entries: byBucket.get(bucket) ?? [],
  }));
}

// ── Totals + formatting ───────────────────────────────────────────────────────

export interface CashPlanTotals {
  allocated: number;
  unallocated: number;
  count: number;
}

export function allocationTotals(
  response: Pick<AdvisorCashPlanResponse, "allocation_summary"> | null | undefined,
): CashPlanTotals {
  return {
    allocated: response?.allocation_summary?.allocated_cash ?? 0,
    unallocated: response?.allocation_summary?.unallocated_cash ?? 0,
    count: response?.allocation_summary?.allocation_count ?? 0,
  };
}

/** "12.3%" for 12.3; "—" for null/undefined/non-finite. */
export function formatPercentOfCash(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return "—";
  return `${value.toFixed(1)}%`;
}

// ── Trust derivation ──────────────────────────────────────────────────────────

export interface TranslatedFix {
  /** Plain-English blocker sentence for the visible UI. */
  plain: string;
  /** Exact backend next_required_fix string for the technical-detail expander. */
  technical: string;
}

/**
 * Translate the backend's next_required_fix into plain English. The backend
 * strings are operator-facing sentences (e.g. mention "Stage 11B",
 * "price_history"); the visible UI gets a user-facing translation and the
 * exact original is preserved as technical detail.
 */
export function translateNextRequiredFix(
  fix: string | null | undefined,
): TranslatedFix | null {
  if (!fix || !fix.trim()) return null;
  const lower = fix.toLowerCase();

  if (lower.includes("reconcil")) {
    return {
      plain:
        "Portfolio values disagree beyond tolerance — a new portfolio snapshot is required before the numbers can be trusted.",
      technical: fix,
    };
  }
  if (lower.includes("price")) {
    if (lower.includes("missing")) {
      return {
        plain:
          "A current-price repair is required — some holdings are missing recent prices.",
        technical: fix,
      };
    }
    if (lower.includes("stale")) {
      return {
        plain:
          "A current-price repair is required — some holdings have stale prices.",
        technical: fix,
      };
    }
    return { plain: "A current-price repair is required.", technical: fix };
  }
  if (lower.startsWith("resolve blockers")) {
    return {
      plain: "Policy checks are blocked — see the technical detail for the exact blockers.",
      technical: fix,
    };
  }
  if (lower.includes("no immediate fix required")) {
    return { plain: "No immediate fix required.", technical: fix };
  }
  // Backend fixes are already sentences; pass through unknown ones honestly.
  return { plain: fix, technical: fix };
}

export interface CashPlanTrust {
  trusted: boolean;
  /** "Numeric plan trusted: Yes" / "Numeric plan trusted: No" */
  label: string;
  /** Plain-English blocker (only when not trusted). */
  blocker: string | null;
  /** Exact next_required_fix string (only when not trusted). */
  blockerTechnicalDetail: string | null;
}

export function deriveCashPlanTrust(
  response: Pick<AdvisorCashPlanResponse, "trusted" | "next_required_fix"> | null | undefined,
): CashPlanTrust {
  const trusted = response?.trusted === true;
  if (trusted) {
    return {
      trusted: true,
      label: "Numeric plan trusted: Yes",
      blocker: null,
      blockerTechnicalDetail: null,
    };
  }
  const fix = translateNextRequiredFix(response?.next_required_fix ?? null);
  return {
    trusted: false,
    label: "Numeric plan trusted: No",
    blocker:
      fix?.plain ??
      "Underlying portfolio data needs a full refresh before these numbers can be trusted.",
    blockerTechnicalDetail: fix?.technical ?? null,
  };
}

// ── Repair action classification (used by the trust drawer) ───────────────────

export type CashPlanRepairAction =
  | "new portfolio snapshot required"
  | "current-price repair required"
  | "Run Intel required"
  | "another bounded batch required"
  | null;

export function repairActionFromFix(fix: string | null | undefined): CashPlanRepairAction {
  if (!fix || !fix.trim()) return null;
  const lower = fix.toLowerCase();
  if (lower.includes("no immediate fix required")) return null;
  if (lower.includes("reconcil") || lower.includes("snapshot")) {
    return "new portfolio snapshot required";
  }
  if (lower.includes("price")) return "current-price repair required";
  if (lower.includes("evidence") || lower.includes("intel")) return "Run Intel required";
  return null;
}

// ── Plan state derivation (10 states) ─────────────────────────────────────────

export type AdvisorCashPlanState =
  | "trusted-with-etfs"
  | "trusted-with-stock"
  | "etf-only-explained"
  | "degraded-truth"
  | "missing-snapshot"
  | "stale-evidence"
  | "partial-run-intel"
  | "no-candidate-above-min-trade"
  | "backend-error"
  | "auth-error";

export interface CashPlanStateInput {
  response?: AdvisorCashPlanResponse | null;
  /** HTTP status of a failed request (401 → auth-error). */
  errorStatus?: number | null;
  /** True when the request failed (network or HTTP). */
  hadError?: boolean;
  /** Advisor run state from the readiness model ("partial" marks a half-finished Intel run). */
  runState?: string | null;
}

const ETF_ONLY_NOTE_PREFIX = "This plan is ETF-only";

export function deriveCashPlanState(input: CashPlanStateInput): AdvisorCashPlanState {
  if (input.hadError || (input.errorStatus !== null && input.errorStatus !== undefined)) {
    if (input.errorStatus === 401) return "auth-error";
    return "backend-error";
  }

  const response = input.response;
  if (!response) return "backend-error";

  const explanations = response.explanations ?? null;
  const planNotes = explanations?.plan_notes ?? [];
  const isReadyTrusted = response.status === "ready" && response.trusted === true;

  if (isReadyTrusted) {
    if ((response.planned_buys ?? []).length === 0) {
      return "no-candidate-above-min-trade";
    }
    const selected = explanations?.selected ?? [];
    if (selected.some((entry) => entry.asset_type === "equity")) {
      return "trusted-with-stock";
    }
    if (planNotes.some((note) => note.startsWith(ETF_ONLY_NOTE_PREFIX))) {
      return "etf-only-explained";
    }
    return "trusted-with-etfs";
  }

  // Non-ready / untrusted: classify the dominant blocker.
  if (input.runState === "partial") return "partial-run-intel";

  const blockedCodes = (explanations?.not_selected ?? []).flatMap(
    (entry) => entry.raw_codes ?? [],
  );
  if (blockedCodes.includes("evidence_stale")) return "stale-evidence";
  if (blockedCodes.includes("evidence_missing_for_ticker")) return "missing-snapshot";

  return "degraded-truth";
}

export type CashPlanTone = "positive" | "caution" | "negative" | "neutral";

export interface CashPlanStateCopy {
  headline: string;
  tone: CashPlanTone;
}

const STATE_COPY: Record<AdvisorCashPlanState, CashPlanStateCopy> = {
  "trusted-with-etfs": { headline: "Plan ready — ETF allocations", tone: "positive" },
  "trusted-with-stock": {
    headline: "Plan ready — includes an individual stock",
    tone: "positive",
  },
  "etf-only-explained": { headline: "Plan ready — ETF-only this time", tone: "positive" },
  "degraded-truth": {
    headline: "Plan degraded — portfolio or price truth needs repair",
    tone: "caution",
  },
  "missing-snapshot": {
    headline: "Plan limited — no certified Intel evidence yet",
    tone: "caution",
  },
  "stale-evidence": {
    headline: "Plan limited — Intel evidence is stale",
    tone: "caution",
  },
  "partial-run-intel": {
    headline: "Plan limited — the Intel run is only partially complete",
    tone: "caution",
  },
  "no-candidate-above-min-trade": {
    headline: "No buys planned — no candidate cleared the minimum trade size",
    tone: "neutral",
  },
  "backend-error": {
    headline: "Cash planning is unavailable right now",
    tone: "negative",
  },
  "auth-error": { headline: "Sign in again to plan your cash", tone: "negative" },
};

export function cashPlanStateCopy(state: AdvisorCashPlanState): CashPlanStateCopy {
  return STATE_COPY[state];
}

/** Error message for a failed request, by HTTP status (route handler contract). */
export function cashPlanErrorMessage(errorStatus: number | null | undefined): string {
  if (errorStatus === 401) {
    return "Your session has expired. Sign in again to plan your cash.";
  }
  if (errorStatus === 503) {
    return "Cash planning is not configured on the server — the runtime certification secret is missing.";
  }
  return "The cash plan service did not respond. Try again.";
}
