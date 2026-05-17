/**
 * Pure Journal Ledger helpers — Stage 4G.
 * No React, no IO, no external deps — safe for tests.
 *
 * Converts existing DecisionMemoryLog rows into the Journal timeline format:
 *   chapter-numeral timeline, entry anatomy, evaluation-window state.
 *
 * Never invents intelligence, performance claims, or pattern-detected lessons.
 * Missing data renders Coming-Later / unavailable — never fabricated.
 */

import type { DecisionMemoryLog, ActualDecisionItem } from "./api";

// ── Roman numeral ─────────────────────────────────────────────────────────────

export function toRomanNumeral(n: number): string {
  if (!Number.isFinite(n) || n < 1) return "I";
  const vals = [1000, 900, 500, 400, 100, 90, 50, 40, 10, 9, 5, 4, 1];
  const syms = ["M", "CM", "D", "CD", "C", "XC", "L", "XL", "X", "IX", "V", "IV", "I"];
  let result = "";
  let remaining = n;
  for (let i = 0; i < vals.length; i++) {
    while (remaining >= vals[i]) {
      result += syms[i];
      remaining -= vals[i];
    }
  }
  return result;
}

// ── Decision row ──────────────────────────────────────────────────────────────

export interface JournalDecisionRow {
  ticker: string;
  actualAction: string;
  /** Formatted dollar amount or null when zero/absent. */
  actualAmountFormatted: string | null;
  recommendedAction: string | null;
  isManual: boolean;
}

function buildDecisionRow(item: ActualDecisionItem): JournalDecisionRow | null {
  const ticker = item.ticker?.trim().toUpperCase();
  if (!ticker) return null;
  const actualAction = (item.actual_action ?? "").trim() || "—";
  const amt = Number(item.actual_amount);
  const actualAmountFormatted =
    Number.isFinite(amt) && amt > 0
      ? new Intl.NumberFormat("en-US", {
          style: "currency",
          currency: "USD",
          maximumFractionDigits: 0,
        }).format(amt)
      : null;
  return {
    ticker,
    actualAction,
    actualAmountFormatted,
    recommendedAction: item.recommended_action?.trim() || null,
    isManual: item.is_manual === true,
  };
}

// ── Evaluation state ──────────────────────────────────────────────────────────

export type JournalEvaluationKind =
  | "pending"
  | "window_open"
  | "ready"
  | "unavailable";

export interface JournalEvaluationState {
  kind: JournalEvaluationKind;
  label: string;
  detail: string;
}

export function buildEvaluationState(log: DecisionMemoryLog): JournalEvaluationState {
  const ps = log.performance_snapshot;

  if (!ps) {
    return {
      kind: "pending",
      label: "Evaluation pending",
      detail:
        "The outcome evaluation window has not opened yet. Evaluation begins once baseline price data is captured.",
    };
  }

  switch (ps.status) {
    case "baseline_captured":
      return {
        kind: "window_open",
        label: "Window open",
        detail:
          "Baseline price captured. Outcome evaluation is in progress — results appear after the observation window closes.",
      };
    case "pending":
      return {
        kind: "window_open",
        label: "Window open",
        detail:
          "Outcome evaluation is in progress. Results appear after the observation window closes.",
      };
    case "ready":
      return {
        kind: "ready",
        label: "Evaluated",
        detail: "Outcome evaluation is complete.",
      };
    case "partial_data":
      return {
        kind: "unavailable",
        label: "Partial data",
        detail:
          "Some tickers could not be evaluated due to missing price data. Results are incomplete.",
      };
    case "missing_price":
      return {
        kind: "unavailable",
        label: "Price data missing",
        detail:
          "Outcome evaluation requires price data that is not yet available for this entry.",
      };
    case "insufficient_data":
      return {
        kind: "unavailable",
        label: "Insufficient data",
        detail:
          "Not enough data to evaluate this entry. Evaluation may improve as more price history becomes available.",
      };
    default:
      return {
        kind: "unavailable",
        label: "Unavailable",
        detail:
          "Outcome evaluation is not available for this entry.",
      };
  }
}

// ── Source label ──────────────────────────────────────────────────────────────

export function buildSourceLabel(source: string): string {
  switch (source) {
    case "deploy_v3":
      return "Deploy v3";
    case "deploy":
      return "Deploy";
    default:
      return source || "Unknown";
  }
}

// ── Status label ──────────────────────────────────────────────────────────────

export function buildStatusLabel(status: string): string {
  switch (status.toUpperCase()) {
    case "FULLY_EXECUTED":
      return "Fully executed";
    case "PARTIALLY_EXECUTED":
      return "Partially executed";
    case "SKIPPED":
      return "Skipped";
    case "DRAFT":
      return "Draft";
    default:
      return status;
  }
}

// ── Cash deployed ─────────────────────────────────────────────────────────────

export function computeCashDeployed(actualDecisions: ActualDecisionItem[]): number {
  return actualDecisions.reduce((sum, row) => {
    const action = (row.actual_action ?? "").toUpperCase();
    // Only count BUY-type actions as deployed capital
    if (action === "BOUGHT" || action === "PARTIAL" || action === "REPLACED") {
      return sum + (Number(row.actual_amount) || 0);
    }
    return sum;
  }, 0);
}

// ── Journal entry ─────────────────────────────────────────────────────────────

export interface JournalEntry {
  id: string;
  /** Roman numeral — I, II, III, ... (most recent = highest numeral) */
  chapterNumeral: string;
  createdAt: string;
  updatedAt: string;
  source: string;
  sourceLabel: string;
  status: string;
  statusLabel: string;
  notes: string | null;
  decisions: JournalDecisionRow[];
  cashDeployed: number;
  cashDeployedFormatted: string | null;
  evaluationState: JournalEvaluationState;
}

/**
 * Build the Journal timeline from existing decision logs.
 * Entries are returned newest-first with Roman numerals assigned oldest-first
 * (so the first decision = Chapter I, the most recent = Chapter N).
 *
 * Never invents outcomes, performance scores, or lesson content.
 */
export function buildJournalEntries(logs: DecisionMemoryLog[]): JournalEntry[] {
  if (!logs || logs.length === 0) return [];

  // Sort by created_at ascending to assign chapter numerals oldest-first
  const sorted = [...logs].sort((a, b) => {
    const ta = new Date(a.created_at).getTime();
    const tb = new Date(b.created_at).getTime();
    return ta - tb;
  });

  const entries: JournalEntry[] = sorted.map((log, idx) => {
    const decisions = (log.actual_decisions ?? [])
      .map(buildDecisionRow)
      .filter((r): r is JournalDecisionRow => r !== null);

    const cashDeployed = computeCashDeployed(log.actual_decisions ?? []);
    const cashDeployedFormatted =
      cashDeployed > 0
        ? new Intl.NumberFormat("en-US", {
            style: "currency",
            currency: "USD",
            maximumFractionDigits: 0,
          }).format(cashDeployed)
        : null;

    return {
      id: log.id,
      chapterNumeral: toRomanNumeral(idx + 1),
      createdAt: log.created_at,
      updatedAt: log.updated_at,
      source: log.source,
      sourceLabel: buildSourceLabel(log.source),
      status: log.status,
      statusLabel: buildStatusLabel(log.status),
      notes: log.notes ?? null,
      decisions,
      cashDeployed,
      cashDeployedFormatted,
      evaluationState: buildEvaluationState(log),
    };
  });

  // Return newest-first for display
  return entries.reverse();
}

// ── Capsule content (Coming-Later) ────────────────────────────────────────────

/** Canonical caption for Journal's "Lessons" surface — activates in Stage 6F. */
export const JOURNAL_LESSONS_CAPTION =
  "This learning surface is being prepared. The next intelligence stage will surface it here.";

/** Canonical caption for "What I learned today" archive — activates in Stage 6F. */
export const JOURNAL_WHAT_I_LEARNED_CAPTION =
  "This learning surface is being prepared. The next intelligence stage will surface it here.";
