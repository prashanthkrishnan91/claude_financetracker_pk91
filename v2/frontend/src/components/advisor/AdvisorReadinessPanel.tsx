"use client";

/**
 * AdvisorReadinessPanel — Section A of the unified Advisor view.
 *
 * Presentational over the pure readiness model from `advisor-readiness.ts`.
 * Shows trust rows, snapshot age / evidence freshness / certified coverage,
 * Buy/Hold/Trim/Sell chips, the Run Intel button (label driven by the run
 * state machine), and an aria-live progress line rendering the backend's
 * pre-sanitized plain-English run status — never task-table internals.
 *
 * When a session status reports a published snapshot (completed /
 * completed_with_gaps + completed_snapshot_id), this panel invalidates the
 * ["intel_v3","snapshot"] query so the fresh snapshot loads — no
 * navigation, no polling of its own, no infinite spinner.
 */

import { useEffect, useRef } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { cn } from "@/lib/utils";
import { Spinner } from "@/components/ui/Spinner";
import { TrustStatusRow } from "@/components/cards/TrustPrimitives";
import type { AdvisorReadinessModel } from "@/lib/advisor-readiness";
import type { IntelV3SessionStatus } from "@/lib/api";

const FOCUS_RING =
  "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/60";

const PILL_TONE_CLASS: Record<string, string> = {
  Ready: "bg-action-buy/10 text-action-buy border-action-buy/30",
  // Amber valid-but-caveated state (completed with gaps) — not green, not red.
  "Partly Ready": "bg-action-trim/10 text-action-trim border-action-trim/30",
  Updating: "bg-action-trim/10 text-action-trim border-action-trim/30",
  Blocked: "bg-action-sell/10 text-action-sell border-action-sell/30",
  "Needs Research": "bg-surface-elevated text-text-muted border-border",
};

const ACTION_CHIP_CLASS: Record<string, string> = {
  BUY: "action-badge-buy",
  HOLD: "action-badge-hold",
  TRIM: "action-badge-trim",
  SELL: "action-badge-sell",
};

const ANALYSIS_TRUST_TONE: Record<string, string> = {
  healthy: "bg-action-buy/10 text-action-buy border-action-buy/30",
  limited: "bg-action-trim/10 text-action-trim border-action-trim/30",
  blocked: "bg-action-sell/10 text-action-sell border-action-sell/30",
  not_applicable: "bg-surface-elevated text-text-muted border-border",
  unknown: "bg-surface-elevated text-text-muted border-border",
};

const ANALYSIS_TRUST_LABEL: Record<string, string> = {
  healthy: "Analysis trust: Healthy",
  limited: "Analysis trust: Limited",
  blocked: "Analysis trust: Blocked",
  not_applicable: "Analysis trust: N/A",
  unknown: "Analysis trust: Unknown",
};

export function AdvisorReadinessPanel({
  model,
  onRun,
  lastRunResult,
}: {
  model: AdvisorReadinessModel;
  onRun: () => void;
  lastRunResult: IntelV3SessionStatus | null;
}) {
  const queryClient = useQueryClient();
  const refetchedForSnapshotId = useRef<string | null>(null);

  // Automatic snapshot refetch once per published snapshot id.
  useEffect(() => {
    if (!lastRunResult) return;
    const status = lastRunResult.session_status;
    if (status !== "completed" && status !== "completed_with_gaps") return;
    const snapshotId = lastRunResult.completed_snapshot_id ?? null;
    if (!snapshotId) return;
    if (refetchedForSnapshotId.current === snapshotId) return;
    refetchedForSnapshotId.current = snapshotId;
    queryClient.invalidateQueries({ queryKey: ["intel_v3", "snapshot"] });
  }, [lastRunResult, queryClient]);

  const { run } = model;
  const { progress } = run;
  const showTickerProgress = run.state === "running" && progress.totalTickers > 0;

  return (
    <section aria-labelledby="advisor-readiness-heading" className="data-card p-4 space-y-4">
      <div className="flex items-start justify-between gap-3 flex-wrap">
        <div className="space-y-1 min-w-0">
          <h2 id="advisor-readiness-heading" className="section-header">
            Readiness
          </h2>
          <div className="flex items-center gap-2 flex-wrap">
            <span
              className={cn(
                "rounded-full border px-2 py-0.5 text-[11px] font-semibold",
                PILL_TONE_CLASS[model.statusPillLabel] ?? PILL_TONE_CLASS["Needs Research"],
              )}
            >
              {model.statusPillLabel === "Ready" ? "Intel Ready" : model.statusPillLabel}
            </span>
            <span className="text-[11px] text-text-secondary">{model.statusLine}</span>
          </div>
        </div>

        <button
          type="button"
          onClick={onRun}
          disabled={run.buttonBusy}
          className={cn("btn-primary min-h-[40px] shrink-0 flex items-center gap-1.5", FOCUS_RING)}
        >
          {run.buttonBusy && <Spinner className="h-3 w-3" />}
          {run.buttonLabel}
        </button>
      </div>

      {/* Trust rows — honest Unknowns when the snapshot doesn't report a value */}
      <div className="divide-y divide-border/50 border-t border-border/50">
        {model.truthRows.map((row) => (
          <TrustStatusRow
            key={row.key}
            label={row.label}
            status={row.status}
            detail={row.detail}
          />
        ))}
      </div>

      {/* Snapshot metadata. "Holdings decided" — never "certified" alone —
          refers explicitly to session decision coverage, not analysis
          trust. See the Analysis trust section below for the independent
          trust status. */}
      <dl className="grid grid-cols-3 gap-3">
        <div>
          <dt className="metric-label">Snapshot age</dt>
          <dd className="data-value-sm mt-0.5">{model.snapshotAgeLabel ?? "—"}</dd>
        </div>
        <div>
          <dt className="metric-label">Evidence</dt>
          <dd className="text-xs text-text-secondary mt-0.5">
            {model.evidenceFreshnessLabel ?? "Unknown"}
          </dd>
        </div>
        <div>
          <dt className="metric-label" title="Holdings with a persisted decision this run — not an analysis-trust guarantee.">
            Holdings decided
          </dt>
          <dd className="data-value-sm mt-0.5">
            {model.certifiedCount !== null && model.totalCount !== null
              ? `${model.certifiedCount}/${model.totalCount}`
              : "—"}
          </dd>
        </div>
      </dl>

      {/* Analysis trust — independent of session decision coverage above.
          run_trust_contract_v1: a session can have every holding decided
          and still be "blocked" (failed required conflict reviews, no
          source lineage established yet). */}
      {model.runTrust && (
        <div className="border-t border-border/50 pt-3 space-y-2">
          <div className="flex items-center gap-2 flex-wrap">
            <span
              className={cn(
                "rounded-full border px-2 py-0.5 text-[11px] font-semibold",
                ANALYSIS_TRUST_TONE[model.runTrust.overallStatus] ?? ANALYSIS_TRUST_TONE.unknown,
              )}
            >
              {ANALYSIS_TRUST_LABEL[model.runTrust.overallStatus] ?? "Analysis trust: Unknown"}
            </span>
          </div>
          <p className="text-[10px] text-text-muted leading-snug">
            Measures evidence sourcing and conflict-review completeness for this run —
            separate from whether every holding received a decision (above).
          </p>
          <dl className="space-y-1 text-[11px] text-text-secondary">
            <div>{model.runTrust.sessionCoverageLine}</div>
            <div>{model.runTrust.axisCoverageLine}</div>
            <div>{model.runTrust.conflictReviewLine}</div>
            <div>{model.runTrust.sourceLineageLine}</div>
          </dl>
        </div>
      )}

      {/* Action counts derived from current_holdings */}
      <div className="flex items-center gap-2 flex-wrap" aria-label="Holding action counts">
        {(["BUY", "HOLD", "TRIM", "SELL"] as const).map((action) => (
          <span key={action} className={ACTION_CHIP_CLASS[action]}>
            {action.charAt(0) + action.slice(1).toLowerCase()}{" "}
            <span className="font-mono tabular-nums">{model.actionCounts[action]}</span>
          </span>
        ))}
      </div>

      {/* Run progress — polite live region so screen readers hear updates.
          Only the backend's plain-English sentence plus a simple holdings
          count — never task tables, queue metrics, or internal codes. */}
      <div aria-live="polite" className="space-y-1.5 border-t border-border/50 pt-3">
        <p className="text-xs text-text-secondary">{run.nextActionSentence}</p>
        {run.evidenceSummaryLine && (
          <p className="text-[11px] text-text-muted">{run.evidenceSummaryLine}</p>
        )}
        {showTickerProgress && (
          <p className="text-[11px] text-text-muted font-mono tabular-nums">
            {progress.decidedTickers} of {progress.totalTickers} holdings analyzed
          </p>
        )}
      </div>
    </section>
  );
}
