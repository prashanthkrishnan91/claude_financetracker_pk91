/**
 * Advisor readiness model — pure helpers (no React).
 *
 * Derives the Section A readiness model for the unified Advisor view from
 * (a) the Intel v3 snapshot query state and (b) the latest distributed
 * run-session status payload (POST /intel/v3/run create response or a
 * GET /sessions/{id}/status poll). Reuses the certified-status derivation
 * from `intel-v3-banner.ts` — it never invents a new "Ready" definition.
 *
 * Hard invariant: NEVER report "Ready" unless a certified snapshot actually
 * exists (worker_certified, full coverage, not stale, evidence current).
 *
 * Run state machine (distributed workflow — the backend executes the run;
 * the browser only observes):
 *   idle → running (session created/running; browser polls)
 *        → complete (completed | completed_with_gaps)
 *        → failed (request error, session failed, create failed, not found)
 *
 * There is no "partial" state (no browser-driven continuation batches) and
 * no "queue_only" state (the backend worker always executes the session).
 * Progress copy comes from the backend's pre-sanitized `plain_status`
 * sentence — never from task tables, queue metrics, or internal codes.
 */

import type {
  IntelV3Action,
  IntelV3RunTrustContract,
  IntelV3SessionStatus,
  IntelV3Snapshot,
} from "@/lib/api";
import type { AdvisorTruthContract } from "@/lib/advisor-truth";
import { buildStatusPillState } from "@/lib/intel-v3-banner";

// ── Run state machine types ───────────────────────────────────────────────────

export type AdvisorRunState = "idle" | "running" | "complete" | "failed";

export type AdvisorRunButtonLabel =
  | "Run Intel"
  | "Running…"
  | "Retry Intel run";

/** Plain-English-safe ticker progress (holdings, not tasks or queue jobs). */
export interface AdvisorRunProgress {
  totalTickers: number;
  /** Holdings whose decision work is finished (including degraded ones). */
  decidedTickers: number;
  /** Holdings that could not be fully analyzed this run. */
  failedOrDegradedTickers: number;
}

export interface AdvisorRunModel {
  state: AdvisorRunState;
  buttonLabel: AdvisorRunButtonLabel;
  /** True while the session is being created or is still executing — button disabled + spinner. */
  buttonBusy: boolean;
  /** Truthful plain-English status for the user. Never a raw backend code. */
  nextActionSentence: string;
  progress: AdvisorRunProgress;
  /** True when the run finished and published a snapshot — the UI should
   *  invalidate ["intel_v3","snapshot"] so the fresh snapshot loads. */
  shouldRefetchSnapshot: boolean;
  /** True when the run completed but some holdings had limited evidence. */
  completedWithGaps: boolean;
  /** Compact "N lanes reused, M refreshed" line — only set on a terminal
   *  run when the backend reported real metrics; never a zero placeholder. */
  evidenceSummaryLine: string | null;
  /** Bounded, human-readable next step from a blocked financial-truth
   *  preflight (e.g. "Add or import at least one open position.") — never a
   *  raw code. Null except on a blocked-preflight not_created result whose
   *  reason isn't the pre-existing "no_active_holdings" add-positions case. */
  repairAction: string | null;
}

// ── Plain-English sentences (exported for tests and UI reuse) ─────────────────

export const CERTIFIED_CURRENT_SENTENCE = "Certified snapshot is current.";

export const ADD_POSITIONS_SENTENCE =
  "There are no active holdings to analyze. Add positions before running Intel.";

export const RUN_REQUEST_FAILED_SENTENCE =
  "The Intel run request failed. Retry when you're ready.";

export const RUN_IDLE_SENTENCE =
  "Run Intel to generate a certified snapshot for your holdings.";

/** Idle sentence when a certified snapshot already exists — never asks the
 *  user to "generate" a snapshot that is already current. */
export const RUN_IDLE_CERTIFIED_SENTENCE =
  "Certified snapshot is current. Run Intel again to refresh evidence when needed.";

export const RUN_IN_PROGRESS_SENTENCE =
  "Intel run in progress — analyzing your holdings.";

export const RUN_COMPLETED_SENTENCE =
  "Intel run finished — your recommendations are up to date.";

/** Caveat sentence for completed_with_gaps — complete, but honestly qualified. */
export const RUN_COMPLETED_WITH_GAPS_SENTENCE =
  "Intel run finished, but some holdings had limited evidence this run — results may have gaps.";

export const RUN_FAILED_SENTENCE =
  "This run could not finish. Retry when you're ready.";

export const RUN_NOT_FOUND_SENTENCE =
  "That run could no longer be found. Start a new Intel run.";

// ── Run model derivation ──────────────────────────────────────────────────────

export interface AdvisorRunInput {
  /** True from the Run Intel click until the session reaches a terminal state. */
  isRunPending: boolean;
  /** True when the run request or polling irrecoverably errored (network/HTTP). */
  isRunError: boolean;
  /** Latest session-status payload (create response or poll), if any. */
  lastRunResult: IntelV3SessionStatus | null | undefined;
}

/** Session statuses that mean the run reached its end state. */
export function isTerminalSessionStatus(
  result: IntelV3SessionStatus | null | undefined,
): boolean {
  if (!result) return false;
  if (result.terminal === true) return true;
  const status = result.session_status;
  return (
    status === "completed" ||
    status === "completed_with_gaps" ||
    status === "failed" ||
    status === "not_created" ||
    status === "not_found"
  );
}

export function deriveRunProgress(
  result: IntelV3SessionStatus | null | undefined,
): AdvisorRunProgress {
  return {
    totalTickers: result?.total_tickers ?? 0,
    decidedTickers: result?.decision_complete_tickers ?? 0,
    failedOrDegradedTickers: result?.failed_or_degraded_tickers ?? 0,
  };
}

/** The backend's pre-sanitized plain-English sentence, if it sent one. */
function plainStatusSentence(
  result: IntelV3SessionStatus | null | undefined,
): string | null {
  const sentence = result?.plain_status?.trim();
  return sentence ? sentence : null;
}

export function deriveRunModel(input: AdvisorRunInput): AdvisorRunModel {
  const { isRunPending, isRunError, lastRunResult } = input;
  const progress = deriveRunProgress(lastRunResult);
  const base = {
    progress,
    shouldRefetchSnapshot: false,
    completedWithGaps: false,
    evidenceSummaryLine: null as string | null,
    repairAction: null as string | null,
  };

  // 1. Session creating or executing — the browser is polling; button busy.
  if (isRunPending) {
    return {
      ...base,
      state: "running",
      buttonLabel: "Running…",
      buttonBusy: true,
      nextActionSentence:
        plainStatusSentence(lastRunResult) ?? RUN_IN_PROGRESS_SENTENCE,
    };
  }

  // 2. Request-level failure (network/HTTP/auth).
  if (isRunError) {
    return {
      ...base,
      state: "failed",
      buttonLabel: "Retry Intel run",
      buttonBusy: false,
      nextActionSentence: RUN_REQUEST_FAILED_SENTENCE,
    };
  }

  // 3. Nothing has run yet.
  if (!lastRunResult) {
    return {
      ...base,
      state: "idle",
      buttonLabel: "Run Intel",
      buttonBusy: false,
      nextActionSentence: RUN_IDLE_SENTENCE,
    };
  }

  const status = lastRunResult.session_status;
  const plain = plainStatusSentence(lastRunResult);

  // 4. Completed — snapshot published; caveat sentence when gaps remain.
  if (status === "completed" || status === "completed_with_gaps") {
    const withGaps = status === "completed_with_gaps";
    return {
      ...base,
      state: "complete",
      buttonLabel: "Run Intel",
      buttonBusy: false,
      nextActionSentence:
        plain ??
        (withGaps ? RUN_COMPLETED_WITH_GAPS_SENTENCE : RUN_COMPLETED_SENTENCE),
      shouldRefetchSnapshot: Boolean(lastRunResult.completed_snapshot_id),
      completedWithGaps: withGaps,
      evidenceSummaryLine: lastRunResult.evidence_summary_line?.trim() || null,
    };
  }

  // 5. Session failed on the backend.
  if (status === "failed") {
    return {
      ...base,
      state: "failed",
      buttonLabel: "Retry Intel run",
      buttonBusy: false,
      nextActionSentence: plain ?? RUN_FAILED_SENTENCE,
    };
  }

  // 6. Session could not be created.
  if (status === "not_created") {
    if (lastRunResult.reason === "no_active_holdings") {
      return {
        ...base,
        state: "idle",
        buttonLabel: "Run Intel",
        buttonBusy: false,
        nextActionSentence: ADD_POSITIONS_SENTENCE,
      };
    }
    return {
      ...base,
      state: "failed",
      buttonLabel: "Retry Intel run",
      buttonBusy: false,
      nextActionSentence: plain ?? RUN_REQUEST_FAILED_SENTENCE,
      repairAction: lastRunResult.repair_action?.trim() || null,
    };
  }

  // 7. Session vanished (deleted/expired server-side).
  if (status === "not_found") {
    return {
      ...base,
      state: "failed",
      buttonLabel: "Retry Intel run",
      buttonBusy: false,
      nextActionSentence: RUN_NOT_FOUND_SENTENCE,
    };
  }

  // 8. Non-terminal status without an in-flight poll (transient render gap) —
  //    still honestly "running"; the backend keeps working regardless.
  if (status === "created" || status === "running") {
    return {
      ...base,
      state: "running",
      buttonLabel: "Running…",
      buttonBusy: true,
      nextActionSentence: plain ?? RUN_IN_PROGRESS_SENTENCE,
    };
  }

  // 9. Honest fallback — a run happened, its outcome vocabulary is unknown.
  return {
    ...base,
    state: "idle",
    buttonLabel: "Run Intel",
    buttonBusy: false,
    nextActionSentence: plain ?? RUN_IDLE_SENTENCE,
  };
}

// ── Snapshot-side readiness ───────────────────────────────────────────────────

export type AdvisorTruthStatus = "ok" | "pending" | "blocked" | "unavailable";

/**
 * Six-dimension vocabulary. Intel-layer facts (from the Intel snapshot) are
 * labeled as Intel facts; financial-truth facts come ONLY from the
 * /api/advisor/readiness truth endpoint (never from snapshot fields). The
 * sixth dimension, Cash-plan trust, lives in the cash-plan/trust surfaces
 * (numeric_plan_trusted) — not in these rows.
 */
export interface AdvisorTruthRow {
  key:
    | "intel_certification"
    | "intel_evidence_freshness"
    | "snapshot_source_health"
    | "portfolio_financial_truth"
    | "current_price_truth"
    | "books_reconciliation";
  label: string;
  status: AdvisorTruthStatus;
  detail: string;
}

export type AdvisorSnapshotState =
  | "loading"
  | "missing"
  | "error"
  | "stale"
  | "uncertified"
  /** Valid-but-caveated: certified over the decided subset, some holdings
   *  couldn't be analyzed in the last run. Amber, never blocked/unavailable. */
  | "certified_with_gaps"
  | "certified";

export interface AdvisorReadinessModel {
  snapshotState: AdvisorSnapshotState;
  /** True ONLY when a certified, non-stale snapshot exists. */
  ready: boolean;
  /** Compact user-facing pill from the shared banner helper ("Ready" only when certified). */
  statusPillLabel: string;
  statusLine: string;
  generatedAt: string | null;
  snapshotAgeLabel: string | null;
  evidenceFreshnessLabel: string | null;
  certifiedCount: number | null;
  totalCount: number | null;
  /** Derived by counting current_holdings card actions — never from a summary field alone. */
  actionCounts: Record<IntelV3Action, number>;
  truthRows: AdvisorTruthRow[];
  run: AdvisorRunModel;
  /** Independent Run Intel analysis-trust summary (run_trust_contract_v1).
   * Null for legacy/non-distributed snapshots. Never conflated with `ready`
   * — a session can be fully decided (session coverage complete) and still
   * report analysis trust "blocked" (failed reviews, no source lineage). */
  runTrust: AdvisorAnalysisTrustSummary | null;
}

export interface AdvisorSnapshotQueryInput {
  snapshot: IntelV3Snapshot | null | undefined;
  isLoading: boolean;
  isError: boolean;
  errorMessage?: string | null;
}

/**
 * True when the snapshot read failure means "no snapshot exists yet" rather
 * than a transient error. fetchApi collapses the backend's 404 detail object
 * ({code:"no_snapshot",…}) to the string "[object Object]", so that marker is
 * accepted alongside the explicit 404 / no_snapshot texts.
 */
export function isSnapshotMissingError(errorMessage: string | null | undefined): boolean {
  if (!errorMessage) return false;
  return (
    errorMessage.includes("404") ||
    errorMessage.includes("no_snapshot") ||
    errorMessage.includes("No Intel v3 snapshot") ||
    errorMessage.includes("not enabled") ||
    errorMessage === "[object Object]"
  );
}

export function deriveActionCounts(
  snapshot: IntelV3Snapshot | null | undefined,
): Record<IntelV3Action, number> {
  const counts: Record<IntelV3Action, number> = { BUY: 0, HOLD: 0, TRIM: 0, SELL: 0 };
  for (const card of snapshot?.current_holdings ?? []) {
    if (card.action in counts) counts[card.action] += 1;
  }
  return counts;
}

export function formatSnapshotAge(
  generatedAt: string | null | undefined,
  now: Date = new Date(),
): string | null {
  if (!generatedAt) return null;
  const ts = new Date(generatedAt).getTime();
  if (Number.isNaN(ts)) return null;
  const diffMs = now.getTime() - ts;
  if (diffMs < 0) return "just now";
  const minutes = Math.floor(diffMs / 60_000);
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  return `${days}d ago`;
}

const EVIDENCE_FRESHNESS_LABELS: Record<string, string> = {
  certified_current: "Evidence current",
  republish_pending: "Evidence republish pending",
  certification_blocked: "Evidence certification blocked",
  rebuilt_and_published: "Evidence rebuilt and published",
  no_snapshot_exists: "No evidence snapshot yet",
};

export function evidenceFreshnessLabel(
  state: string | null | undefined,
): string | null {
  if (!state) return null;
  return EVIDENCE_FRESHNESS_LABELS[state] ?? "Evidence freshness unknown";
}

function isCertifiedSnapshot(snapshot: IntelV3Snapshot): boolean {
  return (
    snapshot.snapshot_source === "worker_certified" &&
    typeof snapshot.certified_holding_count === "number" &&
    typeof snapshot.total_holding_count === "number" &&
    snapshot.total_holding_count > 0 &&
    snapshot.certified_holding_count === snapshot.total_holding_count
  );
}

/**
 * Distributed publication with gaps — certified over the decided subset; some
 * holdings could not be analyzed in the last run. A valid-but-caveated state:
 * NOT certification_failed, NOT blocked, NOT unavailable — but never "Ready".
 */
function isCertifiedWithGapsSnapshot(snapshot: IntelV3Snapshot): boolean {
  return (
    snapshot.snapshot_source === "worker_certified_with_gaps" &&
    typeof snapshot.certified_holding_count === "number" &&
    typeof snapshot.total_holding_count === "number" &&
    snapshot.total_holding_count > 0
  );
}

/**
 * Intel-layer source-health row. This is an Intel snapshot fact — it must
 * never be labeled price/portfolio truth (those come from the truth endpoint).
 */
function deriveSnapshotSourceHealthRow(snapshot: IntelV3Snapshot | null): AdvisorTruthRow {
  if (!snapshot) {
    return {
      key: "snapshot_source_health",
      label: "Snapshot source health",
      status: "unavailable",
      detail: "Unknown — no snapshot to report source health.",
    };
  }
  const raw = snapshot.source_health?.status ?? null;
  if (raw === "ok" || raw === "healthy" || raw === "green") {
    return {
      key: "snapshot_source_health",
      label: "Snapshot source health",
      status: "ok",
      detail: "Source health reported healthy by the latest snapshot.",
    };
  }
  if (raw === "degraded" || raw === "stale" || raw === "partial" || raw === "limited") {
    return {
      key: "snapshot_source_health",
      label: "Snapshot source health",
      status: "pending",
      detail: `Source health reported "${raw}" by the latest snapshot.`,
    };
  }
  if (raw === "blocked" || raw === "failed" || raw === "error") {
    return {
      key: "snapshot_source_health",
      label: "Snapshot source health",
      status: "blocked",
      detail: `Source health reported "${raw}" by the latest snapshot.`,
    };
  }
  if (raw === "not_assessed") {
    return {
      key: "snapshot_source_health",
      label: "Snapshot source health",
      status: "unavailable",
      detail: "Source health not assessed — lineage has not been evaluated for this snapshot.",
    };
  }
  if (raw === "not_applicable") {
    return {
      key: "snapshot_source_health",
      label: "Snapshot source health",
      status: "unavailable",
      detail: "Source health not applicable — no holdings to evaluate.",
    };
  }
  if (raw === "unknown") {
    // "unknown" covers two different meanings — a successful read that
    // genuinely found zero specialist outputs, or a fail-closed read/
    // reverification failure. Never guess which one it is: use the
    // backend's plain-English reason when present, and fall back to an
    // honest "could not be verified" (never the old hardcoded claim that
    // assumed the zero-outputs case) when it isn't.
    const reason = snapshot.source_health?.reason;
    return {
      key: "snapshot_source_health",
      label: "Snapshot source health",
      status: "unavailable",
      detail:
        reason ??
        "Source health unknown because verification failed — trust status could not be re-verified.",
    };
  }
  return {
    key: "snapshot_source_health",
    label: "Snapshot source health",
    status: "unavailable",
    detail: raw
      ? `Source health reported "${raw}" — not a recognized status.`
      : "Source health not assessed — the snapshot did not report it.",
  };
}

// ── Run Intel trust-contract summary (run_trust_contract_v1) ─────────────────

export type AnalysisTrustStatus = "healthy" | "limited" | "blocked" | "not_applicable" | "unknown";

/** Plain-English summary of the six-dimension run trust contract, for the
 * Advisor readiness panel. Independent of `ready`/`statusPillLabel` — a
 * session can have full session coverage (every holding decided) and still
 * be analysis-trust "blocked" (failed required reviews, no source lineage).
 * Returned only when the snapshot carries a run_trust_contract (distributed
 * session, published or read-time enriched); null otherwise (legacy
 * snapshot — analysis trust is simply not applicable to it). */
export interface AdvisorAnalysisTrustSummary {
  overallStatus: AnalysisTrustStatus;
  /** e.g. "31 of 31 holdings decided — 0 no-call, 0 failed." */
  sessionCoverageLine: string;
  /** e.g. "Technical 31/31 · Sentiment 31/31 · Fundamentals 19/19 · ETF exposure 12/12" */
  axisCoverageLine: string;
  /** e.g. "2 of 7 required conflict reviews succeeded — 5 failed." */
  conflictReviewLine: string;
  /** e.g. "Source lineage missing — 0 of 93 specialist outputs carry a source reference." */
  sourceLineageLine: string;
  blockingReasons: string[];
  warnings: string[];
}

const AXIS_DISPLAY_LABELS: Record<string, string> = {
  technical: "Technical",
  sentiment: "Sentiment",
  fundamental: "Fundamentals",
  etf_exposure: "ETF exposure",
  crypto_market: "Crypto",
  risk_filing: "Risk filing",
};

function formatAxisCoverageLine(
  axisCoverage: IntelV3RunTrustContract["axis_coverage"],
): string {
  const parts: string[] = [];
  for (const axis of ["technical", "sentiment", "fundamental", "etf_exposure", "crypto_market"]) {
    const counts = axisCoverage[axis];
    if (!counts || counts.expected_count === 0) continue; // not applicable to this portfolio
    parts.push(`${AXIS_DISPLAY_LABELS[axis] ?? axis} ${counts.succeeded_count}/${counts.expected_count}`);
  }
  return parts.length > 0 ? parts.join(" · ") : "No specialist axes applied to any holding this run.";
}

export function deriveRunTrustSummary(
  snapshot: IntelV3Snapshot | null,
): AdvisorAnalysisTrustSummary | null {
  const contract = snapshot?.run_trust_contract;
  if (!contract) return null;

  // Fail-closed "unknown" (durable state could not be read/re-verified) —
  // every field below is a placeholder zero/empty, NOT a verified fact.
  // Never print "0 of 0 holdings decided", "no conflict reviews were
  // required", "no specialist axes applied" or "no outputs recorded" here —
  // those are true claims about an ESTABLISHED empty state, not this one.
  if (contract.overall_status === "unknown") {
    const reason = contract.source_health?.reason ?? contract.blocking_reasons[0] ?? null;
    return {
      overallStatus: "unknown",
      sessionCoverageLine: reason
        ? `Session coverage could not be re-verified — ${reason}`
        : "Session coverage could not be re-verified for this run.",
      axisCoverageLine: "Specialist-axis coverage could not be re-verified for this run.",
      conflictReviewLine: "Conflict-review coverage could not be re-verified for this run.",
      sourceLineageLine: "Source lineage could not be re-verified for this run.",
      blockingReasons: contract.blocking_reasons,
      warnings: contract.warnings,
    };
  }

  const cov = contract.session_coverage;
  const sessionCoverageLine =
    `${cov.decided_count} of ${cov.frozen_holding_count} holdings decided — ` +
    `${cov.no_call_count} no-call, ${cov.failed_count} failed` +
    (cov.publication_complete ? "." : " (publication incomplete).");

  // The status vocabulary is shared by historical LLM-reviewed runs and new
  // deterministically-resolved runs, so this line is method-neutral —
  // e.g. "7 specialist conflict or low-confidence cases — 2 completed, 5 failed."
  const rc = contract.conflict_review_coverage;
  const conflictReviewLine =
    rc.required_count === 0
      ? "No specialist conflict or low-confidence cases were detected this run."
      : `${rc.required_count} specialist conflict or low-confidence case${rc.required_count === 1 ? "" : "s"} ` +
        `— ${rc.succeeded_count} completed` +
        (rc.failed_count > 0 ? `, ${rc.failed_count} failed` : "") +
        (rc.pending_count > 0 ? `, ${rc.pending_count} still pending` : "") +
        ".";

  const sl = contract.source_lineage;
  const totalOutputs = sl.outputs_with_source_refs + sl.outputs_missing_source_refs;
  const sourceLineageLine =
    totalOutputs === 0
      ? "Source lineage not assessed — no specialist outputs recorded this run."
      : sl.outputs_with_source_refs === 0
        ? `Source lineage missing — 0 of ${totalOutputs} specialist outputs carry a source reference.`
        : sl.outputs_missing_source_refs === 0
          ? `Source lineage established — ${sl.outputs_with_source_refs} of ${totalOutputs} specialist outputs carry a source reference.`
          : `Source lineage partial — ${sl.outputs_with_source_refs} of ${totalOutputs} specialist outputs carry a source reference.`;

  return {
    overallStatus: contract.overall_status,
    sessionCoverageLine,
    axisCoverageLine: formatAxisCoverageLine(contract.axis_coverage),
    conflictReviewLine,
    sourceLineageLine,
    blockingReasons: contract.blocking_reasons,
    warnings: contract.warnings,
  };
}

/** Intel-layer evidence freshness row (from evidence_freshness_state). */
function deriveEvidenceFreshnessRow(snapshot: IntelV3Snapshot | null): AdvisorTruthRow {
  const state = snapshot?.evidence_freshness_state ?? null;
  const label = evidenceFreshnessLabel(state);
  if (!state || !label) {
    return {
      key: "intel_evidence_freshness",
      label: "Intel evidence freshness",
      status: "unavailable",
      detail: "Unknown — the snapshot did not report evidence freshness.",
    };
  }
  const status: AdvisorTruthStatus =
    state === "certified_current" || state === "rebuilt_and_published"
      ? "ok"
      : state === "republish_pending"
        ? "pending"
        : state === "certification_blocked"
          ? "blocked"
          : "unavailable";
  return {
    key: "intel_evidence_freshness",
    label: "Intel evidence freshness",
    status,
    detail: `${label}.`,
  };
}

// ── Financial-truth rows (fed ONLY by the /api/advisor/readiness endpoint) ────

const TRUTH_UNKNOWN_DETAIL =
  "Unknown — the financial truth check has not run yet or is unavailable.";

function derivePortfolioFinancialTruthRow(
  truth: AdvisorTruthContract | null,
): AdvisorTruthRow {
  const base = {
    key: "portfolio_financial_truth" as const,
    label: "Portfolio financial truth",
  };
  switch (truth?.portfolio_truth) {
    case "certified":
      return {
        ...base,
        status: "ok",
        detail: "The financial truth baseline certified portfolio value and cost basis.",
      };
    case "degraded":
      return {
        ...base,
        status: "pending",
        detail: "The financial truth baseline reports degraded portfolio truth — repair needed.",
      };
    case "blocked":
      return {
        ...base,
        status: "blocked",
        detail: "The financial truth baseline reports blocked portfolio truth — values cannot be trusted.",
      };
    default:
      return { ...base, status: "unavailable", detail: TRUTH_UNKNOWN_DETAIL };
  }
}

function deriveCurrentPriceTruthRow(truth: AdvisorTruthContract | null): AdvisorTruthRow {
  const base = { key: "current_price_truth" as const, label: "Current-price truth" };
  switch (truth?.price_truth) {
    case "ok":
      return {
        ...base,
        status: "ok",
        detail: "All open holdings have recent prices per the financial truth baseline.",
      };
    case "stale":
      return {
        ...base,
        status: "pending",
        detail: "Some holdings have stale prices per the financial truth baseline.",
      };
    case "missing":
      return {
        ...base,
        status: "blocked",
        detail: "Some holdings are missing prices per the financial truth baseline.",
      };
    default:
      return { ...base, status: "unavailable", detail: TRUTH_UNKNOWN_DETAIL };
  }
}

function deriveBooksReconciliationRow(truth: AdvisorTruthContract | null): AdvisorTruthRow {
  const base = { key: "books_reconciliation" as const, label: "Books reconciliation" };
  const values =
    truth && truth.snapshot_value !== null && truth.position_derived_value !== null
      ? ` Snapshot value ${truth.snapshot_value.toFixed(2)} vs position-derived ${truth.position_derived_value.toFixed(2)}.`
      : "";
  switch (truth?.reconciliation) {
    case "pass":
      return {
        ...base,
        status: "ok",
        detail: `Snapshot and position-derived values agree within tolerance.${values}`,
      };
    case "degraded":
      return {
        ...base,
        status: "pending",
        detail: `Snapshot and position-derived values disagree beyond the certified threshold.${values}`,
      };
    case "blocked":
      return {
        ...base,
        status: "blocked",
        detail: `Snapshot and position-derived values diverge beyond tolerance.${values}`,
      };
    default:
      return { ...base, status: "unavailable", detail: TRUTH_UNKNOWN_DETAIL };
  }
}

export function deriveTruthRows(
  snapshot: IntelV3Snapshot | null,
  snapshotState: AdvisorSnapshotState,
  truth: AdvisorTruthContract | null = null,
): AdvisorTruthRow[] {
  // Intel certification — an Intel-layer fact (worker certification coverage).
  // It must NEVER be labeled portfolio truth: a worker-certified Intel
  // snapshot says nothing about whether the books are financially true.
  let intelCertification: AdvisorTruthRow;
  if (!snapshot) {
    intelCertification = {
      key: "intel_certification",
      label: "Intel certification",
      status: "unavailable",
      detail:
        snapshotState === "loading"
          ? "Loading the latest snapshot…"
          : "Unknown — no certified snapshot exists yet.",
    };
  } else if (isCertifiedSnapshot(snapshot)) {
    intelCertification = {
      key: "intel_certification",
      label: "Intel certification",
      status: snapshot.is_stale ? "pending" : "ok",
      detail: snapshot.is_stale
        ? `Certified snapshot covers ${snapshot.certified_holding_count}/${snapshot.total_holding_count} holdings but is marked stale.`
        : `Certified snapshot covers ${snapshot.certified_holding_count}/${snapshot.total_holding_count} holdings.`,
    };
  } else if (isCertifiedWithGapsSnapshot(snapshot)) {
    // Valid-but-caveated — NOT certification_failed, NOT blocked.
    const analyzed = snapshot.certified_holding_count!;
    const total = snapshot.total_holding_count!;
    intelCertification = {
      key: "intel_certification",
      label: "Intel certification",
      status: "pending",
      detail: `Recommendations are current for ${analyzed} of ${total} holdings — the rest couldn't be analyzed in the last run.`,
    };
  } else if (snapshot.snapshot_source === "certification_failed") {
    intelCertification = {
      key: "intel_certification",
      label: "Intel certification",
      status: "blocked",
      detail: "Snapshot certification failed — holdings could not all be certified.",
    };
  } else {
    intelCertification = {
      key: "intel_certification",
      label: "Intel certification",
      status: "pending",
      detail: "A snapshot exists but it has not passed certification yet.",
    };
  }

  return [
    intelCertification,
    deriveEvidenceFreshnessRow(snapshot),
    deriveSnapshotSourceHealthRow(snapshot),
    derivePortfolioFinancialTruthRow(truth),
    deriveCurrentPriceTruthRow(truth),
    deriveBooksReconciliationRow(truth),
  ];
}

// ── Main derivation ───────────────────────────────────────────────────────────

export function deriveAdvisorReadiness(
  query: AdvisorSnapshotQueryInput,
  runInput: AdvisorRunInput,
  truth: AdvisorTruthContract | null = null,
  now: Date = new Date(),
): AdvisorReadinessModel {
  let run = deriveRunModel(runInput);
  const snapshot = query.snapshot ?? null;

  let snapshotState: AdvisorSnapshotState;
  if (query.isLoading) {
    snapshotState = "loading";
  } else if (!snapshot && query.isError) {
    snapshotState = isSnapshotMissingError(query.errorMessage) ? "missing" : "error";
  } else if (!snapshot) {
    snapshotState = "missing";
  } else if (!isCertifiedSnapshot(snapshot) && !isCertifiedWithGapsSnapshot(snapshot)) {
    snapshotState = "uncertified";
  } else if (
    snapshot.is_stale ||
    snapshot.evidence_freshness_state === "republish_pending" ||
    snapshot.evidence_freshness_state === "certification_blocked"
  ) {
    snapshotState = "stale";
  } else if (isCertifiedWithGapsSnapshot(snapshot)) {
    snapshotState = "certified_with_gaps";
  } else {
    snapshotState = "certified";
  }

  // Ready requires an actually-certified, non-stale snapshot. Nothing else.
  const ready = snapshotState === "certified";

  // Snapshot-aware idle sentence: with a certified snapshot present, never
  // tell the user to "generate" one — only offer a refresh. (Other idle
  // sentences, e.g. add-positions, are left untouched.)
  if (
    run.state === "idle" &&
    snapshotState === "certified" &&
    run.nextActionSentence === RUN_IDLE_SENTENCE
  ) {
    run = { ...run, nextActionSentence: RUN_IDLE_CERTIFIED_SENTENCE };
  }

  const isRefreshing = run.state === "running";
  // Shared status derivation (the pill can only say Ready for certified snapshots).
  const pill = buildStatusPillState(snapshot, isRefreshing, runInput.lastRunResult ?? null);
  // Enforce the hard invariant even if upstream copy would say Ready:
  const statusPillLabel = ready ? pill.pill : pill.pill === "Ready" ? "Needs Research" : pill.pill;
  const statusLine = ready
    ? pill.line
    : pill.pill === "Ready"
      ? "Snapshot exists but is stale — run Intel to refresh."
      : pill.line;

  return {
    snapshotState,
    ready,
    statusPillLabel,
    statusLine,
    generatedAt: snapshot?.generated_at ?? null,
    snapshotAgeLabel: formatSnapshotAge(snapshot?.generated_at, now),
    evidenceFreshnessLabel: evidenceFreshnessLabel(snapshot?.evidence_freshness_state),
    certifiedCount:
      snapshot?.certified_holding_count ??
      snapshot?.certification_summary?.certified_holding_count ??
      null,
    totalCount:
      snapshot?.total_holding_count ??
      snapshot?.certification_summary?.total_holding_count ??
      null,
    actionCounts: deriveActionCounts(snapshot),
    truthRows: deriveTruthRows(snapshot, snapshotState, truth),
    run,
    runTrust: deriveRunTrustSummary(snapshot),
  };
}
