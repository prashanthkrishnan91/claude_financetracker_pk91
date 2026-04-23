"use client";

import { cn } from "@/lib/utils";
import type {
  CostMetricsPayload,
  ModeDecisionPayload,
} from "@/lib/api";

/**
 * DataQualityBanner — Phase 5/6 run-mode and cost summary strip.
 *
 * Renders a one-line banner above the recommendations list that shows:
 *   * FULL vs DEGRADED badge (with the reason tooltip).
 *   * HIGH / MEDIUM / LOW data-quality label derived from the average
 *     ``data_quality_score`` reported by the run-mode classifier.
 *   * Cost ledger (calls + estimated USD) so operators can watch the
 *     token spend per run without leaving the page.
 */

function qualityBandFromAvg(avg: number): {
  label: "HIGH" | "MEDIUM" | "LOW";
  cls: string;
} {
  if (avg >= 0.75) {
    return { label: "HIGH", cls: "bg-green-500/10 text-green-400 border-green-500/30" };
  }
  if (avg >= 0.5) {
    return {
      label: "MEDIUM",
      cls: "bg-yellow-500/10 text-yellow-400 border-yellow-500/30",
    };
  }
  return { label: "LOW", cls: "bg-red-500/10 text-red-400 border-red-500/30" };
}

export function DataQualityBanner({
  runMode,
  decision,
  cost,
}: {
  runMode: "FULL" | "DEGRADED" | string | null | undefined;
  decision: ModeDecisionPayload | null | undefined;
  cost: CostMetricsPayload | null | undefined;
}) {
  // Only render when at least one field carries signal — keeps the
  // layout clean on pre-Phase-5 runs.
  if (!runMode && !decision && !cost) return null;

  const avg = decision?.avg_quality ?? null;
  const band = avg !== null ? qualityBandFromAvg(avg) : null;
  const isDegraded = runMode === "DEGRADED";
  const llmCalls = cost?.actual_llm_calls ?? cost?.attempted_llm_calls ?? cost?.total_calls ?? 0;
  const llmEnriched = cost?.llm_enriched_cards ?? 0;

  return (
    <section
      className={cn(
        "card-glass rounded-xl border px-4 py-3",
        isDegraded ? "border-yellow-500/30 bg-yellow-500/5" : "border-border"
      )}
    >
      <div className="flex items-center gap-2 flex-wrap">
        <span
          className={cn(
            "text-[10px] px-2 py-0.5 rounded-full border font-semibold uppercase",
            isDegraded
              ? "bg-yellow-500/10 text-yellow-400 border-yellow-500/30"
              : "bg-green-500/10 text-green-400 border-green-500/30"
          )}
        >
          {runMode || "FULL"} mode
        </span>
        {band && (
          <span
            className={cn(
              "text-[10px] px-2 py-0.5 rounded-full border font-semibold uppercase",
              band.cls
            )}
          >
            Data {band.label}
          </span>
        )}
        {decision?.total_tickers !== undefined && (
          <span className="text-xs text-text-muted">
            {decision.total_tickers} tickers
            {decision.insufficient_count > 0 &&
              ` · ${decision.insufficient_count} thin`}
          </span>
        )}
        {cost && (
          <div className="text-xs text-text-muted font-mono ml-auto flex items-center gap-2">
            <span>
              {llmCalls} LLM call{llmCalls === 1 ? "" : "s"} · ${cost.total_cost_usd.toFixed(4)}
            </span>
            <span className="text-[10px] px-1.5 py-0.5 rounded border border-border uppercase">
              {llmEnriched} enriched
            </span>
            {(cost.fallback_cards ?? 0) > 0 && (
              <span className="text-[10px] px-1.5 py-0.5 rounded border border-border uppercase">
                {cost.fallback_cards} fallback
              </span>
            )}
            {(cost.discarded_llm_calls ?? 0) > 0 && (
              <span className="text-[10px] px-1.5 py-0.5 rounded border border-border uppercase">
                {cost.discarded_llm_calls} discarded
              </span>
            )}
            {(cost.reused_cached_cards ?? 0) > 0 && (
              <span className="text-[10px] px-1.5 py-0.5 rounded border border-border uppercase">
                {cost.reused_cached_cards} cached
              </span>
            )}
          </div>
        )}
      </div>
      {isDegraded && decision?.explanation && (
        <p className="text-xs text-yellow-200/80 mt-2 leading-relaxed">
          {decision.explanation}
        </p>
      )}
    </section>
  );
}
