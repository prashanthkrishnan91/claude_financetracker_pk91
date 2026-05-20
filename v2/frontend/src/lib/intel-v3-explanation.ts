/**
 * Stage 7 — Plain-English Intel Explanation Layer.
 *
 * Pure deterministic translation functions. No JSX, no React, no IO.
 * All raw backend governance keys → investor-readable copy happen here.
 *
 * Invariants:
 * - No raw metric keys (fcf_margin, roic_ttm, etc.) are returned by any function.
 * - No raw internal governance codes or suppression labels are surfaced in user-facing strings.
 *   See RAW_KEYS_BANNED for the explicit blocklist.
 * - Action labels remain BUY / HOLD / TRIM / SELL only.
 * - Conviction cap, missing evidence, and blocked states are shown honestly.
 */

import type { IntelV3EvidenceExplanation, IntelV3HeldCard } from "./api";

// ── Raw-key leak guard ────────────────────────────────────────────────────────

/** Raw backend metric / diagnostic keys that must never appear in user-facing copy. */
export const RAW_KEYS_BANNED = [
  "fcf_margin", "roic_ttm", "ev_ebitda", "peg_ratio", "gross_margin_ttm",
  "revenue_growth_ttm", "debt_to_equity", "current_ratio", "quick_ratio",
  "SUPPRESSED_UNKNOWN_SOURCE", "SUPPRESSED_INCOMPLETE", "USABLE_WITH_LIMITATIONS",
  "p4b_limited_no_corroboration", "p3a", "p3b", "p4a", "p2_stale_no_usable_axes",
  "governance_inactive", "fallback", "primary_evidence_readiness",
  "auxiliary_evidence_readiness", "governance_priority_applied",
  "safe_for_visible_decision_reason", "axis_usable_counts",
];

// ── Readiness → plain label ───────────────────────────────────────────────────

export interface ReadinessDisplay {
  label: string;
  detail: string;
  isUsable: boolean;
  isBlocked: boolean;
}

/**
 * Convert a raw readiness string to plain-English display fields.
 * Input is the backend readiness value (READY, LIMITED, MISSING, etc.).
 */
export function readinessToDisplay(readiness: string): ReadinessDisplay {
  switch (readiness) {
    case "READY":
      return {
        label: "Good coverage",
        detail: "Company data is available and passes quality checks.",
        isUsable: true,
        isBlocked: false,
      };
    case "LIMITED":
    case "USABLE_WITH_LIMITATIONS":
      return {
        label: "Partial coverage",
        detail: "Some data is available but not fully complete.",
        isUsable: true,
        isBlocked: false,
      };
    case "INSUFFICIENT":
      return {
        label: "Too little to use",
        detail: "Not enough data to contribute to the view.",
        isUsable: false,
        isBlocked: false,
      };
    case "SUPPRESSED":
    case "SUPPRESSED_CONTRADICTED":
    case "SUPPRESSED_UNKNOWN_SOURCE":
    case "SUPPRESSED_INCOMPLETE":
      return {
        label: "Data quality issues found",
        detail: "Data was blocked because of quality or consistency concerns.",
        isUsable: false,
        isBlocked: true,
      };
    case "MISSING":
      return {
        label: "No data available",
        detail: "No data was found for this source.",
        isUsable: false,
        isBlocked: false,
      };
    case "NOT_APPLICABLE":
      return {
        label: "Not required",
        detail: "This data type doesn't apply to this kind of holding.",
        isUsable: false,
        isBlocked: false,
      };
    case "STALE_OR_UNKNOWN":
      return {
        label: "Stale or unknown",
        detail: "Data may be outdated or its freshness is uncertain.",
        isUsable: false,
        isBlocked: false,
      };
    default:
      return {
        label: "Status unavailable",
        detail: "Evidence status could not be determined.",
        isUsable: false,
        isBlocked: false,
      };
  }
}

// ── Governance priority → explanation ─────────────────────────────────────────

/**
 * Convert a governance priority string to a plain-English explanation of
 * why the evidence rating was assigned.
 * Returns empty string when governance was not active.
 */
export function governancePriorityToExplanation(priority: string): string {
  switch (priority) {
    case "p1":
      return "Company data had quality or consistency issues, so the view is conservative by default.";
    case "p2_stale_no_usable_axes":
      return "No usable data was found across any source — the view is conservative pending more information.";
    case "p3a":
      return "Company fundamentals look solid and are confirmed by market behavior — higher confidence.";
    case "p3b":
      return "Company fundamentals look solid, though no additional signals are corroborating yet — moderate confidence.";
    case "p4a":
      return "Some company data is available, supported by other market signals — moderate confidence.";
    case "p4b_limited_no_corroboration":
      return "Company data is partial, with no additional signals to corroborate — conviction is capped as a precaution.";
    case "p5":
      return "No company fundamental data was available — the view is conservative until fundamentals can be assessed.";
    case "fallback":
      return "The engine defaulted to a conservative view because the data situation was unclear.";
    case "governance_inactive":
    case "unknown":
    case "":
      return "";
    default:
      return "";
  }
}

// ── Conviction cap → plain label ─────────────────────────────────────────────

/** Plain-English explanation of why conviction was capped. */
export function convictionCapLabel(cap_applied: boolean, cap_reason: string | null): string {
  if (!cap_applied) return "";
  if (!cap_reason) return "Confidence is limited because supporting data is incomplete.";

  const r = cap_reason.toLowerCase();
  if (r.includes("thin") || r.includes("p1")) {
    return "Conviction is limited because the data available is very thin — this is a conservative precaution.";
  }
  if (r.includes("ok") || r.includes("single") || r.includes("limited") || r.includes("p4b")) {
    return "Conviction is capped at moderate because only partial data is available without corroboration.";
  }
  if (r.includes("suppressed")) {
    return "Conviction is limited because data quality issues were found.";
  }
  return "Confidence is limited because the available evidence is incomplete.";
}

// ── Evidence lane display model ───────────────────────────────────────────────

export interface EvidenceLaneRow {
  laneId: string;
  label: string;
  statusDisplay: ReadinessDisplay;
}

/**
 * Build the 3-lane evidence display rows for a ticker from its evidence_explanation.
 * Returns rows for: company fundamentals, market/price behavior, news & sentiment.
 */
export function buildEvidenceLaneRows(ex: IntelV3EvidenceExplanation): EvidenceLaneRow[] {
  return [
    {
      laneId: "fundamentals",
      label: "Company fundamentals",
      statusDisplay: readinessToDisplay(ex.primary_evidence_status),
    },
    {
      laneId: "technicals",
      label: "Market & price behavior",
      statusDisplay: readinessToDisplay(ex.technical_signals_status),
    },
    {
      laneId: "sentiment",
      label: "News & sentiment",
      statusDisplay: readinessToDisplay(ex.sentiment_status),
    },
  ];
}

// ── Safe-for-decision label ───────────────────────────────────────────────────

export interface SafetyDisplay {
  label: string;
  detail: string;
  tier: "stronger" | "limited" | "blocked";
}

/** Map the safe_for_visible_decision flag and reason to a display tier and label. */
export function buildSafetyDisplay(ex: IntelV3EvidenceExplanation): SafetyDisplay {
  if (ex.action_blocks && ex.action_blocks.length > 0) {
    return {
      label: "Evidence blocked",
      detail: "A quality issue was found that prevents a stronger recommendation.",
      tier: "blocked",
    };
  }
  if (!ex.safe_for_visible_decision) {
    return {
      label: "Evidence limited",
      detail: "Not enough reliable data is available to support a strong view.",
      tier: "limited",
    };
  }
  const fund = readinessToDisplay(ex.primary_evidence_status);
  if (fund.isUsable && !ex.corroboration_gap) {
    return {
      label: "Better supported",
      detail: "Company data is available and corroborated by other signals.",
      tier: "stronger",
    };
  }
  return {
    label: "Partially supported",
    detail: "Company data is available, though additional signals are not yet corroborating.",
    tier: "limited",
  };
}

// ── Portfolio-level evidence summary ─────────────────────────────────────────

export interface PortfolioEvidenceSummary {
  safeCount: number;
  limitedCount: number;
  blockedCount: number;
  convictionCappedCount: number;
  technicalUsableCount: number;
  sentimentUsableCount: number;
  fundamentalsUsableCount: number;
  cardsWithExplanation: number;
  totalCards: number;
}

/**
 * Derive a portfolio-level evidence summary from all current_holdings cards.
 * Used by IntelV3Cockpit to show the evidence health banner.
 */
export function buildPortfolioEvidenceSummary(cards: IntelV3HeldCard[]): PortfolioEvidenceSummary {
  let safeCount = 0;
  let limitedCount = 0;
  let blockedCount = 0;
  let convictionCappedCount = 0;
  let technicalUsableCount = 0;
  let sentimentUsableCount = 0;
  let fundamentalsUsableCount = 0;
  let cardsWithExplanation = 0;

  for (const card of cards) {
    const ex = card.detail_drawer_payload?.evidence_explanation;
    if (!ex) {
      limitedCount++;
      continue;
    }
    cardsWithExplanation++;
    const safety = buildSafetyDisplay(ex);
    if (safety.tier === "blocked") blockedCount++;
    else if (safety.tier === "stronger") safeCount++;
    else limitedCount++;

    if (ex.conviction_cap_applied) convictionCappedCount++;
    if (readinessToDisplay(ex.technical_signals_status).isUsable) technicalUsableCount++;
    if (readinessToDisplay(ex.sentiment_status).isUsable) sentimentUsableCount++;
    if (readinessToDisplay(ex.primary_evidence_status).isUsable) fundamentalsUsableCount++;
  }

  return {
    safeCount,
    limitedCount,
    blockedCount,
    convictionCappedCount,
    technicalUsableCount,
    sentimentUsableCount,
    fundamentalsUsableCount,
    cardsWithExplanation,
    totalCards: cards.length,
  };
}
