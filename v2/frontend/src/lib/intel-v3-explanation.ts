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

// ── Text de-duplication ───────────────────────────────────────────────────────

/**
 * Remove duplicate strings from an array.
 * Comparison is case-insensitive and trims whitespace. Nullish/empty strings excluded.
 */
export function deduplicateTexts(texts: (string | null | undefined)[]): string[] {
  const seen = new Set<string>();
  const result: string[] = [];
  for (const t of texts) {
    if (!t?.trim()) continue;
    const key = t.trim().toLowerCase();
    if (!seen.has(key)) {
      seen.add(key);
      result.push(t.trim());
    }
  }
  return result;
}

// ── Action explanation ────────────────────────────────────────────────────────

/**
 * Build a plain-English "why this action" explanation from the action + evidence state.
 * Used when rationale/why_text is absent or to generate a decision-specific fallback.
 */
export function buildWhyActionExplanation(
  action: string,
  ex: IntelV3EvidenceExplanation | null | undefined
): string {
  const safety = ex ? buildSafetyDisplay(ex) : null;

  switch (action) {
    case "BUY":
      if (safety?.tier === "stronger") {
        return "The engine sees positive business evidence, corroborated by other signals — enough to justify adding.";
      }
      if (safety?.tier === "blocked") {
        return "Some positive signals are present, but data quality issues prevent a high-confidence recommendation.";
      }
      return "The engine sees enough positive business evidence to justify adding, but not enough corroboration for high conviction.";

    case "HOLD":
      if (safety?.tier === "stronger") {
        return "Business fundamentals are intact and well-supported. No reason to add or reduce at this time.";
      }
      if (safety?.tier === "blocked") {
        return "Data quality or completeness concerns prevent a stronger view. Holding conservatively.";
      }
      return "The thesis remains intact, but evidence is incomplete. Holding at current weight pending more data.";

    case "TRIM":
      return "The engine sees signals that warrant reducing exposure — risk is elevated or the valuation is stretched relative to the evidence.";

    case "SELL":
      if (safety?.tier === "blocked") {
        return "Evidence quality issues and risk signals together indicate this position should be exited.";
      }
      return "The engine sees enough negative signals to recommend exiting this position.";

    default:
      return "";
  }
}

// ── Supporting / incomplete evidence sentences ────────────────────────────────

/**
 * Return plain-English sentences for usable evidence lanes.
 * Used in the "Evidence supporting this" drawer section.
 */
export function buildSupportingEvidenceSentences(ex: IntelV3EvidenceExplanation): string[] {
  const sentences: string[] = [];
  const fund = readinessToDisplay(ex.primary_evidence_status);
  const tech = readinessToDisplay(ex.technical_signals_status);
  const sent = readinessToDisplay(ex.sentiment_status);

  if (fund.isUsable) {
    sentences.push(
      ex.primary_evidence_status === "READY"
        ? "Company fundamentals are available and pass quality checks."
        : "Company fundamentals are partially available."
    );
  }
  if (tech.isUsable) {
    sentences.push(
      ex.technical_signals_status === "READY"
        ? "Market and price behavior data is available."
        : "Some market and price behavior data is available."
    );
  }
  if (sent.isUsable) {
    sentences.push(
      ex.sentiment_status === "READY"
        ? "News and sentiment data is available."
        : "Some news and sentiment data is available."
    );
  }
  return sentences;
}

/**
 * Return plain-English sentences for incomplete or missing evidence lanes.
 * Used in the "What is still incomplete" drawer section.
 */
export function buildIncompleteEvidenceSentences(ex: IntelV3EvidenceExplanation): string[] {
  const sentences: string[] = [];
  const fund = readinessToDisplay(ex.primary_evidence_status);
  const tech = readinessToDisplay(ex.technical_signals_status);
  const sent = readinessToDisplay(ex.sentiment_status);

  if (!fund.isUsable) {
    sentences.push(
      fund.isBlocked
        ? "Company fundamentals were blocked due to data quality issues."
        : "Company fundamentals are not available or insufficient."
    );
  }
  if (!tech.isUsable) {
    if (tech.isBlocked) {
      sentences.push("Market behavior data was suppressed due to data quality issues.");
    } else if (
      ex.technical_signals_status === "INSUFFICIENT" ||
      ex.technical_signals_status === "STALE_OR_UNKNOWN" ||
      ex.technical_signals_status === "NOT_EVALUABLE"
    ) {
      sentences.push("Market and price behavior data is available but not yet strong enough to influence the decision.");
    } else {
      sentences.push("Market and price behavior data is not yet available for this ticker.");
    }
  }
  if (!sent.isUsable) {
    if (sent.isBlocked) {
      sentences.push("News and sentiment data was suppressed due to data quality issues.");
    } else if (
      ex.sentiment_status === "INSUFFICIENT" ||
      ex.sentiment_status === "STALE_OR_UNKNOWN" ||
      ex.sentiment_status === "NOT_EVALUABLE"
    ) {
      sentences.push("News and sentiment data is available but not yet strong enough to influence the decision.");
    } else {
      sentences.push("News and sentiment data is not yet available for this ticker.");
    }
  }
  return sentences;
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
