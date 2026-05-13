/**
 * Pure helpers for Deploy v3 Step 3 decision logging.
 * No React, no Supabase, no external dependencies — safe to import in tests.
 *
 * When Deploy v3 is amount-aware (source.amount_aware === true), the snapshot
 * records that amount-aware sizing was used and includes cash_to_deploy.
 * When not amount-aware, the entered amount is recorded as user context only.
 * Logged recommendations come exclusively from visible Step 2 items.
 */

import type { ActualDecisionItem, DecisionMemoryLog } from "./api";
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
 * Source is always "deploy_v3".
 * When step2.amount_aware is true, the snapshot records amount-aware sizing was used.
 * When false, entered amount is recorded as user context only (not a sizing input).
 */
export function buildDeployV3DecisionSnapshot(
  step2: Pick<Step2Result, "state" | "items" | "exact_dollar_ready" | "amount_aware" | "cash_to_deploy">,
  v3Meta: {
    snapshot_id?: string | null;
    run_id?: string | null;
    plan_status?: string | null;
  } | null | undefined,
  context: DeployV3SnapshotContext,
): Record<string, unknown> {
  const isAmountAware = step2.amount_aware === true;
  return {
    source: "deploy_v3",
    created_at_client: new Date().toISOString(),
    amount_aware: isAmountAware,
    ...(isAmountAware
      ? {
          amount_awareness_note:
            "Deploy v3 sized this plan using user-entered planning capital. Not broker-verified cash.",
          cash_to_deploy: step2.cash_to_deploy ?? null,
        }
      : {
          amount_awareness_note:
            "Deploy v3 is not amount-aware. Entered amount is user context only, not a Deploy v3 sizing input.",
        }),
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
    is_manual: false,
  }));
}

// ── Manual (user-added) row helpers ───────────────────────────────────────────

/**
 * Build a manual (user-added) actual decision row for a ticker that was NOT in
 * the visible Step 2 recommendations. Manual rows preserve no recommended
 * action/amount and are flagged with is_manual=true so consumers can label
 * them clearly as user-added rather than model-recommended.
 */
export function buildDeployV3ManualRow(
  ticker: string,
  action: "BUY" | "TRIM" | "SELL",
  amount: number,
  note?: string,
): ActualDecisionItem {
  return {
    ticker: ticker.trim().toUpperCase(),
    actual_action: mapActionToActualDefault(action),
    actual_amount: Number.isFinite(amount) && amount > 0 ? amount : 0,
    is_manual: true,
    ...(note && note.trim() ? { reason: note.trim() } : {}),
  };
}

/** True when this row was added by the user, not recommended by Deploy v3. */
export function isManualDecisionRow(row: ActualDecisionItem): boolean {
  if (row.is_manual === true) return true;
  // Back-compat heuristic: absence of recommended_action AND recommended_amount
  // marks a row that did not originate from a Step 2 recommendation.
  return !row.recommended_action && (row.recommended_amount == null || row.recommended_amount === 0);
}

// ── Session-key reconciliation helpers ────────────────────────────────────────

/**
 * Read the Deploy v3 session_key from a decision memory log, checking both the
 * top-level snapshot field and the mirrored decision_context location.
 */
export function getDeployV3LogSessionKey(log: DecisionMemoryLog | null | undefined): string | null {
  if (!log) return null;
  const snap = log.recommendation_snapshot as
    | { session_key?: unknown; decision_context?: { session_key?: unknown } }
    | undefined;
  const top = snap?.session_key;
  if (typeof top === "string" && top.trim()) return top;
  const ctx = snap?.decision_context?.session_key;
  if (typeof ctx === "string" && ctx.trim()) return ctx;
  return null;
}

/**
 * True when an existing log should be PATCHed instead of creating a new log:
 * the candidate log belongs to the same active sessionKey the user is editing.
 * Guards against updating a previous-session log after the plan/amount changes.
 */
export function shouldUpdateExistingLog(
  candidate: DecisionMemoryLog | null | undefined,
  currentSessionKey: string,
): boolean {
  if (!candidate) return false;
  const key = getDeployV3LogSessionKey(candidate);
  return key !== null && key === currentSessionKey;
}

/** True when the active Deploy v3 sessionKey transitioned to a new plan/session. */
export function isSessionKeyChanged(previous: string | null, next: string): boolean {
  return previous !== null && previous !== next;
}
