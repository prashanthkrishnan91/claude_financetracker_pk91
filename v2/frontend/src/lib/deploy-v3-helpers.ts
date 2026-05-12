/**
 * Pure Deploy v3 helper constants and functions.
 * No external dependencies — safe to import in tests without Supabase env vars.
 */

// ── Endpoint / query key constants ────────────────────────────────────────────

/** Canonical URL for the Deploy v3 plan endpoint. */
export const DEPLOY_V3_PLAN_ENDPOINT = "/api/v1/deploy/v3/plan";

/** React Query key for useDeployV3Plan. */
export const DEPLOY_V3_PLAN_QUERY_KEY = ["deploy_v3", "plan"] as const;

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
