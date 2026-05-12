/**
 * Pure helpers for Deploy v3 Step 3 decision logging.
 * No React, no Supabase, no external dependencies — safe to import in tests.
 *
 * Deploy v3 is NOT amount-aware. The Step 1 amount is recorded as user context
 * only, not as a sizing authority. Logged recommendations come exclusively from
 * the visible Step 2 items produced by deploy-v3-step2-mapper.ts.
 */

import type { ActualDecisionItem } from "./api";
import type { Step2Item, Step2Result } from "./deploy-v3-step2-mapper";

// ── Action default mapping ─────────────────────────────────────────────────────

/** Map a Deploy v3 visible action to a sensible actual_action default. */
export function mapActionToActualDefault(action: Step2Item["action"]): string {
  if (action === "BUY") return "BOUGHT";
  if (action === "TRIM") return "TRIMMED";
  if (action === "SELL") return "SOLD";
  return "HELD";
}

// ── Snapshot builder ──────────────────────────────────────────────────────────

export interface DeployV3SnapshotContext {
  /** Step 1 investment amount — user context only, not Deploy v3 sizing authority. */
  entered_amount: number;
}

/**
 * Build the recommendation_snapshot for a Deploy v3 decision log.
 * Source is always "deploy_v3". Entered amount is context only.
 */
export function buildDeployV3DecisionSnapshot(
  step2: Pick<Step2Result, "state" | "items" | "exact_dollar_ready">,
  v3Meta: {
    snapshot_id?: string | null;
    run_id?: string | null;
    plan_status?: string | null;
  } | null | undefined,
  context: DeployV3SnapshotContext,
): Record<string, unknown> {
  return {
    source: "deploy_v3",
    created_at_client: new Date().toISOString(),
    amount_awareness_note:
      "Deploy v3 is not amount-aware. Entered amount is user context only, not a Deploy v3 sizing input.",
    entered_amount_context: context.entered_amount,
    exact_dollar_ready: step2.exact_dollar_ready,
    intel_snapshot_id: v3Meta?.snapshot_id ?? null,
    intel_run_id: v3Meta?.run_id ?? null,
    plan_status: v3Meta?.plan_status ?? null,
    visible_step2_items: step2.items.map((item) => ({
      ticker: item.ticker,
      action: item.action,
      dollar_amount: item.dollar_amount,
      reason: item.reason,
      final_actionability_status: item.final_actionability_status,
    })),
  };
}

/**
 * Derive a stable session key for deduplication from the visible Step 2 items.
 * Stable across re-renders as long as the same plan/items are shown.
 */
export function buildDeployV3SessionKey(
  v3RunId: string | null | undefined,
  step2Items: Step2Item[],
): string {
  const itemsKey = step2Items
    .map((i) => `${i.ticker}:${i.action}:${Math.round((i.dollar_amount ?? 0) * 100) / 100}`)
    .sort()
    .join("|");
  return `deploy_v3:${v3RunId ?? "no_run"}:${itemsKey}`;
}

// ── Initial actual-decisions builder ──────────────────────────────────────────

/**
 * Build initial actual decisions that mirror the visible Deploy v3 Step 2 items.
 * Actual defaults match recommended — user edits them before saving.
 */
export function buildDeployV3InitialActualDecisions(
  items: Step2Item[],
): ActualDecisionItem[] {
  return items.map((item) => ({
    ticker: item.ticker,
    recommended_action: item.action,
    actual_action: mapActionToActualDefault(item.action),
    recommended_amount: item.dollar_amount ?? 0,
    actual_amount: item.dollar_amount ?? 0,
  }));
}
