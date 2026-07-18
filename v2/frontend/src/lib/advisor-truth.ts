/**
 * Advisor financial-truth contract — mapping + fetch + hook.
 *
 * The backend's operator diagnostic (financial truth baseline) is proxied by
 * the server-only route handler at /api/advisor/readiness, which maps the raw
 * diagnostic to the SMALL frontend-safe contract below (the raw diagnostic is
 * never passed through to the browser). This module owns:
 *   - the frontend-safe contract types
 *   - the server-side mapping function (imported by the route handler)
 *   - the typed client fetch + useAdvisorTruth() React Query hook
 *   - the trust-panel health derivation (pure, testable)
 *
 * Honesty invariant: when the endpoint is unavailable (503, network failure,
 * malformed payload) every dimension reads "unknown" — never "ok". Unknown is
 * a neutral state, not a healthy one.
 */

import { useQuery } from "@tanstack/react-query";
import { translateNextRequiredFix } from "@/lib/advisor-cash-plan";

// ── Frontend-safe contract ────────────────────────────────────────────────────

export type PortfolioTruthStatus = "certified" | "degraded" | "blocked" | "unknown";
export type PriceTruthStatus = "ok" | "stale" | "missing" | "unknown";
export type BooksReconciliationStatus = "pass" | "degraded" | "blocked" | "unknown";

export interface AdvisorTruthContract {
  portfolio_truth: PortfolioTruthStatus;
  price_truth: PriceTruthStatus;
  reconciliation: BooksReconciliationStatus;
  snapshot_value: number | null;
  position_derived_value: number | null;
  snapshot_stale: boolean | null;
  next_required_repair: string | null;
  as_of: string | null;
}

export const UNKNOWN_ADVISOR_TRUTH: AdvisorTruthContract = {
  portfolio_truth: "unknown",
  price_truth: "unknown",
  reconciliation: "unknown",
  snapshot_value: null,
  position_derived_value: null,
  snapshot_stale: null,
  next_required_repair: null,
  as_of: null,
};

// ── Server-side mapping (used by the /api/advisor/readiness route handler) ────

function asRecord(value: unknown): Record<string, unknown> {
  return typeof value === "object" && value !== null
    ? (value as Record<string, unknown>)
    : {};
}

function asNumberOrNull(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function asBooleanOrNull(value: unknown): boolean | null {
  return typeof value === "boolean" ? value : null;
}

function asStringOrNull(value: unknown): string | null {
  return typeof value === "string" && value.trim() !== "" ? value : null;
}

function mapPortfolioTruth(verdict: Record<string, unknown>): PortfolioTruthStatus {
  const raw = verdict.truth_status;
  if (raw === "certified" || raw === "degraded" || raw === "blocked") return raw;
  return "unknown";
}

function mapPriceTruth(priceSection: Record<string, unknown>): PriceTruthStatus {
  if (priceSection.status !== "ok") return "unknown";
  const missing = Array.isArray(priceSection.missing_price_tickers)
    ? priceSection.missing_price_tickers.length
    : 0;
  const stale = Array.isArray(priceSection.stale_price_tickers)
    ? priceSection.stale_price_tickers.length
    : 0;
  if (missing > 0) return "missing";
  if (stale > 0) return "stale";
  return "ok";
}

function mapReconciliation(reconSection: Record<string, unknown>): BooksReconciliationStatus {
  const raw = reconSection.reconciliation_status;
  if (raw === "pass" || raw === "degraded" || raw === "blocked") return raw;
  // "unavailable" (or anything unrecognized) → honest unknown.
  return "unknown";
}

/**
 * Map the raw financial-truth-baseline diagnostic to the frontend-safe
 * contract. All mapping happens server-side in the route handler — the raw
 * diagnostic never reaches the browser.
 */
export function mapFinancialTruthBaseline(raw: unknown): AdvisorTruthContract {
  const diagnostic = asRecord(raw);
  const verdict = asRecord(diagnostic.verdict);
  const snapshotTruth = asRecord(diagnostic.snapshot_truth);
  const positionTruth = asRecord(diagnostic.position_derived_truth);
  const priceTruth = asRecord(diagnostic.price_truth);
  const reconciliation = asRecord(diagnostic.reconciliation);

  return {
    portfolio_truth: mapPortfolioTruth(verdict),
    price_truth: mapPriceTruth(priceTruth),
    reconciliation: mapReconciliation(reconciliation),
    snapshot_value: asNumberOrNull(snapshotTruth.latest_portfolio_value),
    position_derived_value: asNumberOrNull(positionTruth.market_value_sum),
    snapshot_stale: asBooleanOrNull(snapshotTruth.snapshot_is_stale),
    next_required_repair: asStringOrNull(verdict.next_required_fix),
    as_of: asStringOrNull(diagnostic.generated_at),
  };
}

/** Client-side belt-and-braces: coerce anything malformed back to unknown. */
export function sanitizeAdvisorTruth(payload: unknown): AdvisorTruthContract {
  const raw = asRecord(payload);
  const portfolio = raw.portfolio_truth;
  const price = raw.price_truth;
  const recon = raw.reconciliation;
  return {
    portfolio_truth:
      portfolio === "certified" || portfolio === "degraded" || portfolio === "blocked"
        ? portfolio
        : "unknown",
    price_truth:
      price === "ok" || price === "stale" || price === "missing" ? price : "unknown",
    reconciliation:
      recon === "pass" || recon === "degraded" || recon === "blocked" ? recon : "unknown",
    snapshot_value: asNumberOrNull(raw.snapshot_value),
    position_derived_value: asNumberOrNull(raw.position_derived_value),
    snapshot_stale: asBooleanOrNull(raw.snapshot_stale),
    next_required_repair: asStringOrNull(raw.next_required_repair),
    as_of: asStringOrNull(raw.as_of),
  };
}

// ── Typed client fetch + hook ─────────────────────────────────────────────────

export const ADVISOR_TRUTH_ENDPOINT = "/api/advisor/readiness";

/**
 * Fetch the frontend-safe truth contract. Never throws: 503, upstream
 * failure, malformed payload, or network errors all resolve to the honest
 * all-unknown contract.
 */
export async function fetchAdvisorTruth(): Promise<AdvisorTruthContract> {
  try {
    // Dynamic import keeps this module importable in Node test environments
    // (the supabase client requires browser env vars at module scope).
    const { supabase } = await import("@/lib/supabase");
    const {
      data: { session },
    } = await supabase.auth.getSession();

    const res = await fetch(ADVISOR_TRUTH_ENDPOINT, {
      method: "GET",
      headers: {
        ...(session?.access_token ? { Authorization: `Bearer ${session.access_token}` } : {}),
      },
      cache: "no-store",
    });
    if (!res.ok) return UNKNOWN_ADVISOR_TRUTH;
    const payload = await res.json().catch(() => null);
    if (!payload) return UNKNOWN_ADVISOR_TRUTH;
    return sanitizeAdvisorTruth(payload);
  } catch {
    return UNKNOWN_ADVISOR_TRUTH;
  }
}

/**
 * React Query hook for the truth contract. No polling; 5-minute staleTime;
 * no retries (the fetch already degrades to all-unknown on 4xx/503/network).
 */
export function useAdvisorTruth() {
  return useQuery<AdvisorTruthContract>({
    queryKey: ["advisor", "truth"],
    queryFn: fetchAdvisorTruth,
    staleTime: 5 * 60_000,
    retry: false,
    refetchInterval: false,
  });
}

// ── Trust-panel health derivation (pure) ──────────────────────────────────────

export const TRUTH_DIMENSION_LABELS = {
  portfolio_truth: "Portfolio financial truth",
  price_truth: "Current-price truth",
  reconciliation: "Books reconciliation",
} as const;

export type TrustHealthState = "healthy" | "unknown-checks" | "degraded";

export interface TrustHealthInput {
  /** True ONLY when a certified, current Intel snapshot exists (model.ready). */
  intelCertifiedCurrent: boolean;
  /** Truth contract from useAdvisorTruth(); null reads as all-unknown. */
  truth: AdvisorTruthContract | null | undefined;
  /** True when a cash plan has been requested this session. */
  planRequested: boolean;
  /** numeric_plan_trusted for the last requested plan; null when no plan requested. */
  numericPlanTrusted: boolean | null;
}

export interface TrustHealthResult {
  state: TrustHealthState;
  /** True ONLY under the full rule: Intel certified+current AND
   *  portfolio_truth certified AND price_truth ok AND reconciliation pass AND
   *  (no plan requested OR the plan's numbers were trusted). */
  healthy: boolean;
  /** Human labels of dimensions that could not be checked yet. */
  unknownDimensions: string[];
  /** Plain-English problems from degraded/blocked truth dimensions. */
  truthProblems: string[];
  /** Plain-English repair action from next_required_repair (translated). */
  repairPlain: string | null;
  /** Exact raw next_required_repair string for the technical-detail expander. */
  repairTechnical: string | null;
}

export function deriveTrustHealth(input: TrustHealthInput): TrustHealthResult {
  const truth = input.truth ?? UNKNOWN_ADVISOR_TRUTH;

  const unknownDimensions: string[] = [];
  if (truth.portfolio_truth === "unknown") {
    unknownDimensions.push(TRUTH_DIMENSION_LABELS.portfolio_truth);
  }
  if (truth.price_truth === "unknown") {
    unknownDimensions.push(TRUTH_DIMENSION_LABELS.price_truth);
  }
  if (truth.reconciliation === "unknown") {
    unknownDimensions.push(TRUTH_DIMENSION_LABELS.reconciliation);
  }

  const truthProblems: string[] = [];
  if (truth.portfolio_truth === "degraded") {
    truthProblems.push("Portfolio financial truth is degraded — some inputs need repair.");
  }
  if (truth.portfolio_truth === "blocked") {
    truthProblems.push("Portfolio financial truth is blocked — no portfolio value can be trusted.");
  }
  if (truth.price_truth === "stale") {
    truthProblems.push("Some holdings have stale current prices.");
  }
  if (truth.price_truth === "missing") {
    truthProblems.push("Some holdings are missing current prices.");
  }
  if (truth.reconciliation === "degraded") {
    truthProblems.push(
      "Books reconciliation is degraded — snapshot and position-derived values disagree.",
    );
  }
  if (truth.reconciliation === "blocked") {
    truthProblems.push(
      "Books reconciliation is blocked — portfolio values diverge beyond tolerance.",
    );
  }

  const planOk = !input.planRequested || input.numericPlanTrusted === true;
  const healthy =
    input.intelCertifiedCurrent &&
    truth.portfolio_truth === "certified" &&
    truth.price_truth === "ok" &&
    truth.reconciliation === "pass" &&
    planOk;

  const degraded =
    truthProblems.length > 0 || !input.intelCertifiedCurrent || !planOk;

  const state: TrustHealthState = healthy
    ? "healthy"
    : degraded
      ? "degraded"
      : "unknown-checks";

  const fix = translateNextRequiredFix(truth.next_required_repair);
  const isNoOpFix = (truth.next_required_repair ?? "")
    .toLowerCase()
    .includes("no immediate fix required");

  return {
    state,
    healthy,
    unknownDimensions,
    truthProblems,
    repairPlain: healthy || isNoOpFix ? null : (fix?.plain ?? null),
    repairTechnical: healthy || isNoOpFix ? null : (fix?.technical ?? null),
  };
}
