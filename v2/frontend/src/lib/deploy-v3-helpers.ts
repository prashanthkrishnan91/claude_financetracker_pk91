/**
 * Pure Deploy v3 helper constants and functions.
 * No external dependencies — safe to import in tests without Supabase env vars.
 */

// ── Endpoint / query key constants ────────────────────────────────────────────

/** Canonical URL for the Deploy v3 plan endpoint. */
export const DEPLOY_V3_PLAN_ENDPOINT = "/api/v1/deploy/v3/plan";

/** Canonical URL for the Deploy v3 readiness diagnostic endpoint. */
export const DEPLOY_V3_READINESS_ENDPOINT = "/api/v1/deploy/v3/readiness";

/** React Query key for useDeployV3Plan. */
export const DEPLOY_V3_PLAN_QUERY_KEY = ["deploy_v3", "plan"] as const;

/** React Query key for useDeployV3Readiness. */
export const DEPLOY_V3_READINESS_QUERY_KEY = ["deploy_v3", "readiness"] as const;

// ── Readiness label mapping ───────────────────────────────────────────────────

export type ReadinessMeta = { label: string; cls: string };

export const READINESS_META: Record<string, ReadinessMeta> = {
  no_items: { label: "No items in plan — run Intel v3 first", cls: "text-text-muted" },
  all_informational: { label: "All positions: hold as planned", cls: "text-blue-300" },
  all_suppressed: { label: "All items suppressed — evidence gaps present", cls: "text-yellow-300" },
  ready_pending_guardrails: { label: "Ready — pending tax and guardrail review", cls: "text-emerald-300" },
  partially_ready: { label: "Partially ready — some items blocked or not sized", cls: "text-yellow-300" },
  blocked: { label: "Blocked — cash constraint limits deployment", cls: "text-red-400" },
  not_ready: { label: "Not sized yet — sizing inputs not connected", cls: "text-text-muted" },
};

/** Map a plan_readiness_status string to a plain-English label and CSS class. */
export function readinessMeta(status: string): ReadinessMeta {
  return READINESS_META[status] ?? { label: "Plan status unknown", cls: "text-text-muted" };
}

// ── Sizing disclaimer ─────────────────────────────────────────────────────────

/** Source shape subset needed for sizing disclaimer logic. */
type SizingDisclaimerSource = {
  sizing_bundle_provided?: boolean;
  exact_dollar_ready?: boolean;
};

/**
 * Returns the plain-English sizing disclaimer to show, or null if none needed.
 *
 * - null            → exact_dollar_ready is true; no disclaimer
 * - "not connected" → sizing_bundle_provided is false (no portfolio snapshot found)
 * - "not ready"     → sizing_bundle_provided is true but exact_dollar_ready is false
 *                     (bundle found but one or more gates are uncertified)
 */
export function getSizingDisclaimer(
  source: SizingDisclaimerSource | undefined | null,
): string | null {
  if (!source) return null;
  if (source.exact_dollar_ready === true) return null;
  if (source.sizing_bundle_provided) {
    return (
      "Sizing data was found, but exact dollar planning is still not ready. " +
      "Some required inputs are missing or unsupported."
    );
  }
  return (
    "Exact dollar amounts are not connected yet — no executable trade sizing available. " +
    "Dollar fields shown here are scaffold placeholders only."
  );
}

// ── Readiness diagnostic helpers ──────────────────────────────────────────────

/** Map a policy_status string to a plain-English description (no values exposed). */
export function policyStatusLabel(policyStatus: string | undefined): string {
  switch (policyStatus) {
    case "certified":
      return "Policy configured and valid.";
    case "missing_minimum_trade":
      return "Minimum trade setting is not configured.";
    case "missing_rounding_policy":
      return "Rounding policy setting is not configured.";
    case "invalid_policy_config":
      return "Both settings are present but the configuration is invalid.";
    case "unsupported_policy":
      return "Deploy policy settings are not configured.";
    default:
      return "Policy status unknown.";
  }
}

/**
 * Returns true when the error represents a "no snapshot" 404.
 *
 * Backend no-snapshot path raises HTTPException(404, detail={code:"no_snapshot",...}).
 * fetchApi does `new Error(error.detail)` where detail is an object, producing "[object Object]".
 *
 * Does NOT match the flag-off error ("Deploy v3 plan requires Intel v3 to be enabled.
 * Set INTEL_V3_VISIBLE_SNAPSHOT_ENABLED=true to enable.") — that is handled separately.
 */
export function isNoSnapshotError(error: unknown): boolean {
  if (!(error instanceof Error)) return false;
  const msg = error.message;
  if (msg === "[object Object]") return true;
  if (msg.toLowerCase().includes("no_snapshot")) return true;
  if (msg === "API error: 404") return true;
  return false;
}
