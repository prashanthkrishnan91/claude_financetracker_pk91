/**
 * Pure mapper from Deploy v3 plan → Step 2 display shape.
 * No React, no Supabase, no external dependencies — safe to import in tests.
 *
 * Intel v3 owns Buy/Hold/Trim/Sell. This mapper only shapes the display data.
 * Dollar amounts come from the Deploy v3 exact-dollar pipeline; they are null
 * when exact_dollar_ready is false.
 */

import type { DeployV3PlanResponse, DeployV3PlanItem } from "./api";

// ── Step 2 output shapes ───────────────────────────────────────────────────────

export type Step2ItemAction = "BUY" | "TRIM" | "SELL" | "HOLD";

export interface Step2Item {
  ticker: string;
  action: Step2ItemAction;
  /** Dollar amount from exact-dollar pipeline; null when not ready. */
  dollar_amount: number | null;
  /** Short plain-English reason derived from actionability/suppression. */
  reason: string;
  /** Raw final_actionability_status from Deploy v3. */
  final_actionability_status: string;
}

export type Step2State =
  | "not_available"      // Deploy v3 plan unavailable / flag off
  | "setup_incomplete"   // exact_dollar_ready is false
  | "no_moves"           // ready but no BUY/TRIM/SELL dollar moves (all HOLD or suppressed)
  | "has_moves";         // ready with one or more dollar moves

export interface Step2Result {
  state: Step2State;
  /** Items to show in Step 2 — BUY/TRIM/SELL sorted by dollar_amount desc. */
  items: Step2Item[];
  /** True when source.exact_dollar_ready is true. */
  exact_dollar_ready: boolean;
  /** True when this result came from the Deploy v3 pipeline (not legacy). */
  is_deploy_v3: boolean;
}

// ── Action helpers ────────────────────────────────────────────────────────────

export function normalizeAction(raw: string): Step2ItemAction {
  const upper = (raw ?? "").toUpperCase();
  if (upper === "BUY") return "BUY";
  if (upper === "TRIM") return "TRIM";
  if (upper === "SELL") return "SELL";
  return "HOLD";
}

export function isActionableMove(item: DeployV3PlanItem): boolean {
  const action = normalizeAction(item.intel_action);
  if (action === "HOLD") return false;
  // Must have a positive dollar amount — null/zero means not sized.
  if (!(item.recommended_dollar_amount != null && item.recommended_dollar_amount > 0)) return false;
  const status = item.final_actionability_status;
  return (
    status === "actionable_pending_tax" ||
    status === "pending_guardrails" ||
    status === "pending"
  );
}

export function derivePlainReason(item: DeployV3PlanItem): string {
  const status = item.final_actionability_status;
  if (status === "suppressed") {
    return item.suppression_reason
      ? `Suppressed: ${item.suppression_reason.toLowerCase().replace(/_/g, " ")}`
      : "Suppressed — evidence gaps";
  }
  if (status === "blocked_cash") return "Blocked — insufficient cash";
  if (status === "informational_hold") return "Hold — no action needed";
  if (status === "not_ready") return "Not sized yet";
  if (item.pending_guardrails_reason) {
    return item.pending_guardrails_reason.toLowerCase().replace(/_/g, " ");
  }
  return item.intel_action.charAt(0) + item.intel_action.slice(1).toLowerCase();
}

// ── Main mapper ───────────────────────────────────────────────────────────────

/**
 * Map a Deploy v3 plan response to the Step 2 display shape.
 * Returns `state: "not_available"` when plan is null/undefined.
 */
export function mapDeployV3ToStep2(
  plan: DeployV3PlanResponse | null | undefined,
): Step2Result {
  if (!plan) {
    return { state: "not_available", items: [], exact_dollar_ready: false, is_deploy_v3: true };
  }

  const exactReady = plan.source?.exact_dollar_ready === true;

  if (!exactReady) {
    return { state: "setup_incomplete", items: [], exact_dollar_ready: false, is_deploy_v3: true };
  }

  // Map all items; only surface actionable moves in Step 2
  const moveItems: Step2Item[] = plan.items
    .filter(isActionableMove)
    .map((item) => ({
      ticker: item.ticker,
      action: normalizeAction(item.intel_action),
      dollar_amount: item.recommended_dollar_amount,
      reason: derivePlainReason(item),
      final_actionability_status: item.final_actionability_status,
    }))
    .sort((a, b) => (b.dollar_amount ?? 0) - (a.dollar_amount ?? 0));

  if (moveItems.length === 0) {
    return { state: "no_moves", items: [], exact_dollar_ready: true, is_deploy_v3: true };
  }

  return { state: "has_moves", items: moveItems, exact_dollar_ready: true, is_deploy_v3: true };
}

/** Returns the "not_available" sentinel when Deploy v3 is not usable. */
export const STEP2_NOT_AVAILABLE: Step2Result = {
  state: "not_available",
  items: [],
  exact_dollar_ready: false,
  is_deploy_v3: true,
};
