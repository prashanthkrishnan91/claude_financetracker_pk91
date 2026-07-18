"use client";

/**
 * AdvisorReadinessPanel — Section A of the unified Advisor view.
 *
 * Presentational over the pure readiness model from `advisor-readiness.ts`.
 * Shows trust rows, snapshot age / evidence freshness / certified coverage,
 * Buy/Hold/Trim/Sell chips, the Run Intel button (label driven by the run
 * state machine), and an aria-live progress line with honest job numbers.
 *
 * When a run reports snapshot_available_after_run, this panel invalidates
 * the ["intel_v3","snapshot"] query so the fresh snapshot loads — no
 * navigation, no new polling interval, no infinite spinner.
 */

import { useEffect, useRef } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { cn } from "@/lib/utils";
import { Spinner } from "@/components/ui/Spinner";
import { TrustStatusRow } from "@/components/cards/TrustPrimitives";
import type { AdvisorReadinessModel } from "@/lib/advisor-readiness";
import type { IntelV3RunResult } from "@/lib/api";

const FOCUS_RING =
  "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/60";

const PILL_TONE_CLASS: Record<string, string> = {
  Ready: "bg-action-buy/10 text-action-buy border-action-buy/30",
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

export function AdvisorReadinessPanel({
  model,
  onRun,
  lastRunResult,
}: {
  model: AdvisorReadinessModel;
  onRun: () => void;
  lastRunResult: IntelV3RunResult | null;
}) {
  const queryClient = useQueryClient();
  const refetchedForResult = useRef<IntelV3RunResult | null>(null);

  // Automatic snapshot refetch once per run result that produced a snapshot.
  useEffect(() => {
    if (!lastRunResult) return;
    if (!lastRunResult.snapshot_available_after_run) return;
    if (refetchedForResult.current === lastRunResult) return;
    refetchedForResult.current = lastRunResult;
    queryClient.invalidateQueries({ queryKey: ["intel_v3", "snapshot"] });
  }, [lastRunResult, queryClient]);

  const { run } = model;
  const jobs = run.jobs;
  const hasJobNumbers =
    jobs.queued > 0 || jobs.attempted > 0 || jobs.succeeded > 0 || jobs.failed > 0;
  const showProgress = run.state !== "idle" || hasJobNumbers;

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

      {/* Snapshot metadata */}
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
          <dt className="metric-label">Certified</dt>
          <dd className="data-value-sm mt-0.5">
            {model.certifiedCount !== null && model.totalCount !== null
              ? `${model.certifiedCount}/${model.totalCount}`
              : "—"}
          </dd>
        </div>
      </dl>

      {/* Action counts derived from current_holdings */}
      <div className="flex items-center gap-2 flex-wrap" aria-label="Holding action counts">
        {(["BUY", "HOLD", "TRIM", "SELL"] as const).map((action) => (
          <span key={action} className={ACTION_CHIP_CLASS[action]}>
            {action.charAt(0) + action.slice(1).toLowerCase()}{" "}
            <span className="font-mono tabular-nums">{model.actionCounts[action]}</span>
          </span>
        ))}
      </div>

      {/* Run progress — polite live region so screen readers hear updates */}
      <div aria-live="polite" className="space-y-1.5 border-t border-border/50 pt-3">
        <p className="text-xs text-text-secondary">{run.nextActionSentence}</p>
        {showProgress && hasJobNumbers && (
          <p className="text-[11px] text-text-muted font-mono tabular-nums">
            Jobs: {jobs.queued} queued · {jobs.attempted} attempted · {jobs.succeeded}{" "}
            succeeded · {jobs.failed} failed · {jobs.remaining} remaining
          </p>
        )}
        {run.boundedStopReason && (
          <p className="text-[11px] text-text-muted">{run.boundedStopReason}</p>
        )}
        {run.state === "failed" && (
          <button
            type="button"
            onClick={onRun}
            className={cn("btn-secondary min-h-[40px]", FOCUS_RING)}
          >
            Retry Intel run
          </button>
        )}
      </div>
    </section>
  );
}
