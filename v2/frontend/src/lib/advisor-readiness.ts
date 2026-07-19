/**
 * Advisor readiness model — pure helpers (no React).
 *
 * Derives the Section A readiness model for the unified Advisor view from
 * (a) the Intel v3 snapshot query state and (b) the last POST /intel/v3/run
 * mutation result. Reuses the certified-status derivation from
 * `intel-v3-banner.ts` — it never invents a new "Ready" definition.
 *
 * Hard invariant: NEVER report "Ready" unless a certified snapshot actually
 * exists (worker_certified, full coverage, not stale, evidence current).
 *
 * Run state machine:
 *   idle → running → partial (another bounded batch required)
 *        → complete (snapshot_available_after_run)
 *        → failed (jobs failed and none succeeded, the request errored,
 *                  enqueue or deterministic recertification failed, or
 *                  snapshot writes are disabled by the cost guard)
 *        → queue_only (on-demand processing disabled on the server)
 */

import type {
  IntelV3Action,
  IntelV3RunResult,
  IntelV3Snapshot,
} from "@/lib/api";
import type { AdvisorTruthContract } from "@/lib/advisor-truth";
import { buildStatusPillState } from "@/lib/intel-v3-banner";

// ── Run state machine types ───────────────────────────────────────────────────

export type AdvisorRunState =
  | "idle"
  | "running"
  | "partial"
  | "complete"
  | "failed"
  | "queue_only";

export type AdvisorRunButtonLabel =
  | "Run Intel"
  | "Continue Intel run"
  | "Retry Intel run";

export interface AdvisorRunJobs {
  queued: number;
  attempted: number;
  succeeded: number;
  failed: number;
  /** Jobs still waiting after this bounded batch: queued − succeeded − failed (never negative). */
  remaining: number;
}

export interface AdvisorRunModel {
  state: AdvisorRunState;
  buttonLabel: AdvisorRunButtonLabel;
  /** True while the run request is in flight — button disabled + spinner. */
  buttonBusy: boolean;
  /** Truthful plain-English next step for the user. Never a raw backend code. */
  nextActionSentence: string;
  jobs: AdvisorRunJobs;
  /** True when the run reported snapshot_available_after_run — the UI should
   *  invalidate ["intel_v3","snapshot"] so the fresh snapshot loads. */
  shouldRefetchSnapshot: boolean;
  /** Why the bounded batch stopped where it did (partial/queue_only), else null. */
  boundedStopReason: string | null;
}

// ── Plain-English sentences (exported for tests and UI reuse) ─────────────────

export const QUEUE_ONLY_SENTENCE =
  "Analysis is paused on the server right now. Your holdings were queued, but nothing " +
  "will process them until on-demand processing is switched back on.";

/** Operator detail for QUEUE_ONLY (server flag name) — technical-detail only. */
export const QUEUE_ONLY_TECHNICAL_DETAIL =
  "Server flag INTEL_V3_ON_DEMAND_REFRESH_ENABLED is off; enable it or run the analyst " +
  "refresh worker entrypoint separately.";

export const SNAPSHOT_WRITES_DISABLED_SENTENCE =
  "Analysis finished, but the result could not be saved because snapshot updates are " +
  "temporarily paused on the server.";

/** Operator detail for the writes-paused state (server flag name) — technical-detail only. */
export const SNAPSHOT_WRITES_DISABLED_TECHNICAL_DETAIL =
  "Cost-guard flag INTEL_V3_SNAPSHOT_WRITES_ENABLED is off; new snapshots are not persisted.";

export const CERTIFIED_CURRENT_SENTENCE = "Certified snapshot is current.";

export const NO_STALE_EVIDENCE_SENTENCE =
  "All analyst evidence is already current — nothing needed refreshing.";

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
  "Intel run in progress — refreshing analyst evidence for your holdings.";

export function continueSentence(succeeded: number, queued: number): string {
  return `This run refreshed ${succeeded} of ${queued} holdings. Continue to process the rest.`;
}

export function jobsFailedSentence(failed: number): string {
  return `${failed} job${failed === 1 ? "" : "s"} failed and none succeeded this run. Retry the Intel run.`;
}

// ── Durable-job state actions (product-recovery Blocker 3) ────────────────────
// Backend next_required_action values for the two new durable-job states plus
// the active-ticker lookup failure. Deliberately NOT "reclick_"-prefixed —
// they must read as a stopped "failed" state (Retry control), never the
// generic "reclick_" -> partial/auto-continue bucket below.

export const ANALYST_JOBS_BACKOFF_ACTION = "analyst_jobs_in_backoff_retry_after_window";
export const ANALYST_JOBS_TERMINAL_ACTION = "analyst_jobs_retry_budget_exhausted";
export const ACTIVE_TICKERS_LOOKUP_FAILED_ACTION = "active_tickers_lookup_failed_retry";

export const ANALYST_JOBS_TERMINAL_SENTENCE =
  "Some holdings exhausted their analyst-refresh retry budget. Retry the Intel run to try again.";

export function analystJobsBackoffSentence(earliestRetryAt: string | null | undefined): string {
  if (!earliestRetryAt) {
    return "Some holdings are waiting on a retry cooldown before they can refresh again. Retry shortly.";
  }
  const ts = new Date(earliestRetryAt).getTime();
  if (Number.isNaN(ts)) {
    return "Some holdings are waiting on a retry cooldown before they can refresh again. Retry shortly.";
  }
  const minutes = Math.max(1, Math.ceil((ts - Date.now()) / 60_000));
  return `Some holdings are waiting on a retry cooldown (about ${minutes} minute${minutes === 1 ? "" : "s"}) before they can refresh again.`;
}

// ── Run model derivation ──────────────────────────────────────────────────────

export interface AdvisorRunInput {
  /** True while the POST /intel/v3/run request is in flight. */
  isRunPending: boolean;
  /** True when the last run request itself errored (network/HTTP). */
  isRunError: boolean;
  /** Last successful 202 result from POST /intel/v3/run, if any. */
  lastRunResult: IntelV3RunResult | null | undefined;
}

export function deriveRunJobs(result: IntelV3RunResult | null | undefined): AdvisorRunJobs {
  const queued = result?.queued_ticker_count ?? 0;
  const attempted = result?.on_demand_jobs_attempted ?? 0;
  const succeeded = result?.on_demand_jobs_succeeded ?? 0;
  const failed = result?.on_demand_jobs_failed ?? 0;
  return {
    queued,
    attempted,
    succeeded,
    failed,
    remaining: Math.max(0, queued - succeeded - failed),
  };
}

// ── Bounded automatic continuation (Part A3) ──────────────────────────────────
//
// One Run Intel click may need several bounded server-side quanta to drain a
// full portfolio (each POST /intel/v3/run request is capped to a small,
// production-safe wall-clock bound — see analyst_refresh_on_demand_drain_v1).
// Rather than requiring the user to repeatedly click "Continue Intel run",
// the Run Intel hook in hooks.ts calls the endpoint again automatically
// while `shouldAutoContinueRun` says so. This function is pure/no-timers so
// the cap logic is unit-testable without mounting React or faking timers.

/** Hard ceiling on automatic continuation requests for one Run Intel click. */
export const RUN_INTEL_MAX_CONTINUATIONS = 20;

/** Hard ceiling on total elapsed wall-clock time for one Run Intel click. */
export const RUN_INTEL_MAX_ELAPSED_MS = 120_000;

/**
 * Whether the hook should automatically fire another continuation request
 * without the user clicking again. True only while the run state machine
 * reads "partial" (a bounded batch stopped short with resumable work) and
 * neither the attempt cap nor the elapsed-time cap has been reached.
 */
export function shouldAutoContinueRun(
  result: IntelV3RunResult | null | undefined,
  attemptsSoFar: number,
  elapsedMs: number,
): boolean {
  if (attemptsSoFar >= RUN_INTEL_MAX_CONTINUATIONS) return false;
  if (elapsedMs >= RUN_INTEL_MAX_ELAPSED_MS) return false;
  const { state } = deriveRunModel({
    isRunPending: false,
    isRunError: false,
    lastRunResult: result,
  });
  return state === "partial";
}

export function deriveRunModel(input: AdvisorRunInput): AdvisorRunModel {
  const { isRunPending, isRunError, lastRunResult } = input;
  const jobs = deriveRunJobs(lastRunResult);
  const base = {
    jobs,
    shouldRefetchSnapshot: lastRunResult?.snapshot_available_after_run === true,
    boundedStopReason: null as string | null,
  };

  // 1. A request in flight always reads as running.
  if (isRunPending) {
    return {
      ...base,
      state: "running",
      buttonLabel: "Run Intel",
      buttonBusy: true,
      nextActionSentence: RUN_IN_PROGRESS_SENTENCE,
      shouldRefetchSnapshot: false,
    };
  }

  // 2. Request-level failure.
  if (isRunError) {
    return {
      ...base,
      state: "failed",
      buttonLabel: "Retry Intel run",
      buttonBusy: false,
      nextActionSentence: RUN_REQUEST_FAILED_SENTENCE,
      shouldRefetchSnapshot: false,
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
      shouldRefetchSnapshot: false,
    };
  }

  const next = lastRunResult.next_required_action ?? "";
  const status = lastRunResult.status;

  // 4. Backend-reported enqueue/run failure, including a deterministic
  //    recertification failure (mapping/Stage 7/8E/8F prewarm attempted and
  //    failed) — no existing status/action value truthfully means "retry"
  //    for these, so match the suffix rather than inventing a new one.
  if (
    status === "enqueue_failed" ||
    status === "failed" ||
    status.endsWith("_recertification_failed")
  ) {
    return {
      ...base,
      state: "failed",
      buttonLabel: "Retry Intel run",
      buttonBusy: false,
      nextActionSentence:
        lastRunResult.message?.trim() || RUN_REQUEST_FAILED_SENTENCE,
    };
  }

  // 5. Queue-only: on-demand processing disabled — jobs queued but nothing drains them.
  if (next.startsWith("queue_only")) {
    return {
      ...base,
      state: "queue_only",
      buttonLabel: "Run Intel",
      buttonBusy: false,
      nextActionSentence: QUEUE_ONLY_SENTENCE,
      boundedStopReason:
        "Jobs were queued but on-demand processing is disabled — this batch did not process anything.",
    };
  }

  // 5.5. Durable-job backoff/terminal/lookup-failure — must read as a
  //      stopped "failed" state (Retry control usable again), never as
  //      "partial" (which would auto-continue), and must outrank the
  //      "complete" check below so a historical snapshot can never mask a
  //      backlog of durable work still waiting on backoff or permanently
  //      blocked by an exhausted retry budget.
  if (next === ANALYST_JOBS_BACKOFF_ACTION) {
    return {
      ...base,
      state: "failed",
      buttonLabel: "Retry Intel run",
      buttonBusy: false,
      nextActionSentence: analystJobsBackoffSentence(lastRunResult.earliest_retry_at),
      boundedStopReason: "Durable analyst-refresh jobs remain but are in backoff — none are due to retry yet.",
    };
  }
  if (next === ANALYST_JOBS_TERMINAL_ACTION) {
    return {
      ...base,
      state: "failed",
      buttonLabel: "Retry Intel run",
      buttonBusy: false,
      nextActionSentence: ANALYST_JOBS_TERMINAL_SENTENCE,
      boundedStopReason: "At least one durable analyst-refresh job exhausted its retry budget.",
    };
  }
  if (next === ACTIVE_TICKERS_LOOKUP_FAILED_ACTION) {
    return {
      ...base,
      state: "failed",
      buttonLabel: "Retry Intel run",
      buttonBusy: false,
      nextActionSentence: RUN_REQUEST_FAILED_SENTENCE,
    };
  }

  // 6. Certified snapshot available after this run — complete.
  if (lastRunResult.snapshot_available_after_run === true) {
    return {
      ...base,
      state: "complete",
      buttonLabel: "Run Intel",
      buttonBusy: false,
      nextActionSentence: CERTIFIED_CURRENT_SENTENCE,
    };
  }

  // 7. Cost guard: analysis ran but snapshot writes are disabled — nothing usable produced.
  if (next.endsWith("snapshot_writes_enabled_is_false")) {
    return {
      ...base,
      state: "failed",
      buttonLabel: "Retry Intel run",
      buttonBusy: false,
      nextActionSentence: SNAPSHOT_WRITES_DISABLED_SENTENCE,
      boundedStopReason:
        "The bounded batch finished, but the snapshot could not be written (cost guard).",
    };
  }

  // 8. All attempted jobs failed and none succeeded — failed, retry.
  if (jobs.failed > 0 && jobs.succeeded === 0) {
    return {
      ...base,
      state: "failed",
      buttonLabel: "Retry Intel run",
      buttonBusy: false,
      nextActionSentence: jobsFailedSentence(jobs.failed),
    };
  }

  // 9. Bounded batch made progress but another batch is required — partial.
  if (next.startsWith("reclick_") || jobs.remaining > 0) {
    return {
      ...base,
      state: "partial",
      buttonLabel: "Continue Intel run",
      buttonBusy: false,
      nextActionSentence: continueSentence(jobs.succeeded, jobs.queued),
      boundedStopReason: `Bounded batch stopped after ${jobs.attempted} job${
        jobs.attempted === 1 ? "" : "s"
      } — ${jobs.remaining} holding${jobs.remaining === 1 ? "" : "s"} still waiting.`,
    };
  }

  // 10. Nothing was stale to refresh.
  if (
    next === "none_no_stale_evidence_to_refresh" ||
    status === "analyst_evidence_current"
  ) {
    return {
      ...base,
      state: "complete",
      buttonLabel: "Run Intel",
      buttonBusy: false,
      nextActionSentence: NO_STALE_EVIDENCE_SENTENCE,
    };
  }

  // 11. No holdings.
  if (next === "add_positions_before_running_intel" || status === "no_active_holdings") {
    return {
      ...base,
      state: "idle",
      buttonLabel: "Run Intel",
      buttonBusy: false,
      nextActionSentence: ADD_POSITIONS_SENTENCE,
    };
  }

  // 12. Honest fallback — a run happened, its outcome vocabulary is unknown.
  return {
    ...base,
    state: "idle",
    buttonLabel: "Run Intel",
    buttonBusy: false,
    nextActionSentence: RUN_IDLE_SENTENCE,
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
  if (raw === "degraded" || raw === "stale" || raw === "partial") {
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
  return {
    key: "snapshot_source_health",
    label: "Snapshot source health",
    status: "unavailable",
    detail: raw
      ? `Source health reported "${raw}" — not a recognized status.`
      : "Unknown — the snapshot did not report source health.",
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
  } else if (!isCertifiedSnapshot(snapshot)) {
    snapshotState = "uncertified";
  } else if (
    snapshot.is_stale ||
    snapshot.evidence_freshness_state === "republish_pending" ||
    snapshot.evidence_freshness_state === "certification_blocked"
  ) {
    snapshotState = "stale";
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
  };
}
