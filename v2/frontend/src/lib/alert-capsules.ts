/**
 * Pure deterministic capsule helpers for the Alert Center.
 * Stage 4G: "Why this matters" and "What missing data means" capsules.
 *
 * No IO, no side effects, no fabricated intelligence.
 * Built only from existing AlertCandidate fields:
 *   severity, action_type, ticker, source_area, status,
 *   plain_english_reason, candidate_type, timestamps.
 */

import { severityLabel, sourceAreaLabel, candidateTypeLabel } from "./alert-center";
import type { AlertCandidate } from "./api";

// ── Why this matters ──────────────────────────────────────────────────────────

export interface AlertWhyThisMatters {
  /** Short headline for the capsule. */
  headline: string;
  /** One or two sentence explanation of what this signal type means. */
  body: string;
  /** Non-null when action_type is TRIM — context that Trim ≠ bad company. */
  trimNote: string | null;
}

/**
 * Compose a deterministic "Why this matters" capsule from existing candidate fields.
 * Never invents intelligence, theses, filings, or forward-looking claims.
 */
export function buildAlertWhyThisMatters(c: AlertCandidate): AlertWhyThisMatters {
  const area = sourceAreaLabel(c.source_area);
  const typeLabel = candidateTypeLabel(c.candidate_type);
  const sev = severityLabel(c.severity);

  const headline = `${sev} — ${typeLabel}`;

  let body: string;
  if (c.candidate_type === "new_actionable_action") {
    body = `${area} has produced a new actionable signal for ${c.ticker}. `;
    if (c.severity === "high") {
      body += "High signals cross the top priority threshold and warrant prompt review.";
    } else {
      body += "Review the Intel tab for the full analysis before deciding whether to act.";
    }
  } else if (c.candidate_type === "conviction_upgrade") {
    body = `${area} has increased conviction in the existing ${c.action_type ?? "current"} recommendation for ${c.ticker}. No immediate action is required unless you are below your target position size.`;
  } else {
    body = `${area} generated a ${sev.toLowerCase()} signal for ${c.ticker}. Review the Intel analysis for context before acting.`;
  }

  const trimNote =
    c.action_type === "TRIM"
      ? "Trim means the portfolio sizing model recommends reducing your position to move toward the target allocation — not that the company's quality has declined. A Trim is a sizing action, not an exit signal."
      : null;

  return { headline, body, trimNote };
}

// ── What missing data means ───────────────────────────────────────────────────

export interface AlertMissingDataCapsule {
  headline: string;
  body: string;
}

/**
 * Compose a "What missing data means" capsule for suppressed or expired candidates.
 * Only used when the candidate status is suppressed, expired, or dismissed.
 */
export function buildMissingDataCapsule(c: AlertCandidate): AlertMissingDataCapsule | null {
  const suppressedStatuses = new Set(["suppressed", "expired", "dismissed"]);
  if (!suppressedStatuses.has(c.status)) return null;

  let body =
    "This signal was not promoted because the underlying evidence did not meet the alert policy threshold at the time it was evaluated.";
  if (c.plain_english_reason?.trim()) {
    body += ` Reason: ${c.plain_english_reason.trim()}`;
  }
  body +=
    " When evidence quality improves or the situation changes, a new signal may appear in the review queue.";

  return {
    headline: "Why this signal was not promoted",
    body,
  };
}

// ── Capsule display state ─────────────────────────────────────────────────────

export interface CandidateCapsuleState {
  whyThisMatters: AlertWhyThisMatters;
  missingData: AlertMissingDataCapsule | null;
  /** True when the capsule expansion should be available (non-trivial content). */
  isExpandable: boolean;
}

/** Compose the full capsule state for a single candidate row. */
export function buildCandidateCapsuleState(c: AlertCandidate): CandidateCapsuleState {
  const whyThisMatters = buildAlertWhyThisMatters(c);
  const missingData = buildMissingDataCapsule(c);
  return {
    whyThisMatters,
    missingData,
    isExpandable: true,
  };
}
