/**
 * Pure Deploy Ledger mapper helpers — Stage 4E.
 * No React, no Supabase, no external deps — safe for tests.
 *
 * Maps Deploy v3 data to the Capital Allocation Ledger display format.
 * Deterministic backend policy (Intel v3 → Deploy v3) owns Buy/Hold/Trim/Sell.
 * This module shapes display only — it never invents numbers, statuses, or intelligence.
 */

import type { DeployV3PlanItem, DeployV3PlanRollup } from "./api";
import { normalizeAction } from "./deploy-v3-step2-mapper";

// ── Ledger status groups ──────────────────────────────────────────────────────

export type LedgerStatusGroup =
  | "actionable"       // reserved — no fully-actionable status exists yet
  | "pending"          // actionable_pending_tax, pending_guardrails
  | "blocked"          // blocked_cash
  | "suppressed"       // suppressed
  | "informational"    // informational_hold
  | "not_ready"        // not_ready
  | "not_evaluated_yet"
  | "unknown";

export interface LedgerItemStatus {
  group: LedgerStatusGroup;
  label: string;
  detail: string;
}

export function mapFinalStatusToLedger(
  status: string,
  suppressionReason?: string | null,
): LedgerItemStatus {
  switch (status) {
    case "actionable_pending_tax":
      return {
        group: "pending",
        label: "Pending tax review",
        detail: "Intel recommends this action — awaiting tax guardrail check before confirmation.",
      };
    case "pending_guardrails":
    case "ready_pending_guardrails":
      return {
        group: "pending",
        label: "Pending guardrail review",
        detail: "Recommended action is sized — awaiting final guardrail checks before confirmation.",
      };
    case "informational_hold":
      return {
        group: "informational",
        label: "Hold — no move needed",
        detail: "Intel recommends holding. No cash deployment action is warranted right now.",
      };
    case "suppressed": {
      const reasonText = suppressionReason
        ? suppressionReason.toLowerCase().replace(/_/g, " ")
        : "evidence gaps";
      return {
        group: "suppressed",
        label: "Suppressed",
        detail: `Action suppressed due to ${reasonText}. Evidence quality is insufficient to safely size a move.`,
      };
    }
    case "blocked_cash":
      return {
        group: "blocked",
        label: "Blocked — cash constraint",
        detail: "This action requires more cash than available in the plan. Increase planning capital or hold.",
      };
    case "not_ready":
      return {
        group: "not_ready",
        label: "Not sized yet",
        detail: "Sizing inputs are not connected. Dollar amounts cannot be computed until setup is complete.",
      };
    case "not_evaluated_yet":
      return {
        group: "not_evaluated_yet",
        label: "Not evaluated yet",
        detail: "The guardrail pipeline has not evaluated this item yet. Run Intel and refresh the plan.",
      };
    default:
      return {
        group: "unknown",
        label: "Status unknown",
        detail: "This item has an unrecognised status. No action should be taken.",
      };
  }
}

// ── Ledger items ──────────────────────────────────────────────────────────────

export interface LedgerItem {
  ticker: string;
  action: "BUY" | "TRIM" | "SELL" | "HOLD";
  dollarAmount: number | null;
  ledgerStatus: LedgerItemStatus;
  rationale: string;
  /**
   * Always false — current DeployV3PlanItem contract does not carry
   * current_weight or after_weight. Portfolio shape section uses Coming Later.
   */
  hasPortfolioShape: false;
}

function buildItemRationale(item: DeployV3PlanItem): string {
  if (
    item.selection_reason &&
    item.selection_reason !== "none" &&
    item.intel_action.toUpperCase() === "BUY"
  ) {
    return item.selection_reason;
  }
  if (item.pending_guardrails_reason && item.pending_guardrails_reason !== "") {
    return item.pending_guardrails_reason.toLowerCase().replace(/_/g, " ");
  }
  if (item.final_actionability_status === "suppressed" && item.suppression_reason) {
    return `Suppressed: ${item.suppression_reason.toLowerCase().replace(/_/g, " ")}`;
  }
  switch (item.intel_action.toUpperCase()) {
    case "BUY":  return "Intel Buy recommendation — see Intel tab for full rationale.";
    case "TRIM": return "Intel Trim recommendation — sized to move toward target allocation.";
    case "SELL": return "Intel Sell recommendation — see Intel tab for exit rationale.";
    default:     return "Intel Hold recommendation — no action warranted.";
  }
}

export function buildLedgerItems(items: DeployV3PlanItem[]): LedgerItem[] {
  return items.map((item) => ({
    ticker: item.ticker,
    action: normalizeAction(item.intel_action),
    dollarAmount: item.recommended_dollar_amount ?? null,
    ledgerStatus: mapFinalStatusToLedger(item.final_actionability_status, item.suppression_reason),
    rationale: buildItemRationale(item),
    hasPortfolioShape: false,
  }));
}

// ── Ledger plan state (header) ────────────────────────────────────────────────

export type LedgerPlanSeverity = "ok" | "pending" | "caution" | "blocked" | "unavailable";

export interface LedgerPlanState {
  headline: string;
  sub: string;
  severity: LedgerPlanSeverity;
}

export function buildLedgerPlanState(
  readinessStatus: string | null | undefined,
  rollup: DeployV3PlanRollup | null,
): LedgerPlanState {
  if (!readinessStatus) {
    return {
      headline: "Plan not built yet",
      sub: "Run Intel first to generate a capital allocation plan.",
      severity: "unavailable",
    };
  }

  const pending      = rollup?.pending_count ?? 0;
  const suppressed   = rollup?.suppressed_count ?? 0;
  const blocked      = rollup?.blocked_count ?? 0;
  const informational = rollup?.informational_count ?? 0;

  switch (readinessStatus) {
    case "no_items":
      return {
        headline: "No items in plan",
        sub: "Run Intel v3 to generate a capital allocation plan.",
        severity: "unavailable",
      };
    case "all_informational":
      return {
        headline: "Hold as planned",
        sub: `${informational} position${informational !== 1 ? "s" : ""} — Intel recommends no cash deployment right now.`,
        severity: "ok",
      };
    case "all_suppressed":
      return {
        headline: "Plan suppressed",
        sub: `${suppressed} item${suppressed !== 1 ? "s" : ""} suppressed due to evidence gaps. Review Intel evidence quality.`,
        severity: "caution",
      };
    case "ready_pending_guardrails":
      return {
        headline: "Pending guardrail review",
        sub: `${pending} action${pending !== 1 ? "s" : ""} sized — pending tax and guardrail checks before confirmation.`,
        severity: "pending",
      };
    case "partially_ready": {
      const parts: string[] = [];
      if (blocked > 0) parts.push(`${blocked} blocked by cash constraint`);
      if (suppressed > 0) parts.push(`${suppressed} suppressed — evidence gaps`);
      return {
        headline: "Partially ready",
        sub: parts.length > 0 ? parts.join(". ") + "." : "Some items are pending or blocked.",
        severity: "caution",
      };
    }
    case "blocked":
      return {
        headline: "Blocked — cash constraint",
        sub: "Planned cash is insufficient to cover recommended actions. Increase planning capital or hold.",
        severity: "blocked",
      };
    case "not_ready":
      return {
        headline: "Setup incomplete",
        sub: "Target allocations or deploy policy settings are missing. Complete setup before sizing.",
        severity: "unavailable",
      };
    default:
      return {
        headline: "Plan status unknown",
        sub: "Unable to determine plan state. Refresh or check Intel v3 status.",
        severity: "unavailable",
      };
  }
}

// ── Guardrail groups ──────────────────────────────────────────────────────────

export interface GuardrailGroup {
  group: LedgerStatusGroup;
  displayLabel: string;
  explanation: string;
  items: LedgerItem[];
}

const GUARDRAIL_GROUP_ORDER: LedgerStatusGroup[] = [
  "actionable",
  "pending",
  "informational",
  "blocked",
  "suppressed",
  "not_ready",
  "not_evaluated_yet",
  "unknown",
];

const GUARDRAIL_GROUP_META: Record<LedgerStatusGroup, { displayLabel: string; explanation: string }> = {
  actionable: {
    displayLabel: "Ready",
    explanation: "These items are fully cleared. (Reserved — no fully-actionable status exists yet in this build.)",
  },
  pending: {
    displayLabel: "Pending review",
    explanation: "Intel has recommended these actions and they have been sized, but tax and guardrail checks are still pending. Do not execute until checks are confirmed.",
  },
  informational: {
    displayLabel: "Hold — no action",
    explanation: "Intel recommends holding these positions. No cash needs to move right now.",
  },
  blocked: {
    displayLabel: "Blocked",
    explanation: "These actions are blocked because the plan's cash is insufficient. Increase your planning capital or hold.",
  },
  suppressed: {
    displayLabel: "Suppressed",
    explanation: "Intel has a view on these positions, but evidence quality is insufficient to safely size a move. Review evidence in the Intel tab.",
  },
  not_ready: {
    displayLabel: "Not sized",
    explanation: "Sizing inputs are not fully connected. These items cannot be planned until setup is complete.",
  },
  not_evaluated_yet: {
    displayLabel: "Not evaluated",
    explanation: "The guardrail pipeline has not evaluated these items. Run Intel and refresh the plan.",
  },
  unknown: {
    displayLabel: "Unknown status",
    explanation: "These items have an unrecognised status. No action should be taken.",
  },
};

export function buildGuardrailGroups(items: LedgerItem[]): GuardrailGroup[] {
  const byGroup = new Map<LedgerStatusGroup, LedgerItem[]>();
  for (const item of items) {
    const group = item.ledgerStatus.group;
    const list = byGroup.get(group) ?? [];
    list.push(item);
    byGroup.set(group, list);
  }
  return GUARDRAIL_GROUP_ORDER
    .filter((g) => byGroup.has(g))
    .map((g) => ({
      group: g,
      displayLabel: GUARDRAIL_GROUP_META[g].displayLabel,
      explanation: GUARDRAIL_GROUP_META[g].explanation,
      items: byGroup.get(g)!,
    }));
}

// ── Portfolio shape ───────────────────────────────────────────────────────────

/**
 * Returns false always — DeployV3PlanItem does not carry current_weight/after_weight.
 * Portfolio shape section must render honest Coming Later state.
 */
export function hasPortfolioShapeData(_items: LedgerItem[]): false {
  return false;
}
