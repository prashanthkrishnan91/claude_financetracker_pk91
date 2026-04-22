"use client";

import { cn } from "@/lib/utils";
import type { AgentRunStatus } from "@/lib/api";

/**
 * AgentProgressTracker — visual pipeline status for an in-flight agent run.
 *
 * Shows a 5-step progress strip (Loading → Sentiment → Technicals →
 * Fundamentals → PM → Complete) keyed off the `current_agent` field on the
 * agent_runs table. Also renders the run summary once finished.
 */

const STEPS: { key: string; label: string; match: (s: string) => boolean }[] = [
  {
    key: "load",
    label: "Loading",
    match: (s) => /loading|queued/i.test(s),
  },
  {
    key: "sent",
    label: "Sentiment",
    match: (s) => /sentiment/i.test(s),
  },
  {
    key: "tech",
    label: "Technicals",
    match: (s) => /technical/i.test(s),
  },
  {
    key: "fund",
    label: "Fundamentals",
    match: (s) => /fundamental/i.test(s),
  },
  {
    key: "pm",
    label: "Portfolio Mgr",
    match: (s) => /portfolio|deliberat|saving|completed/i.test(s),
  },
];

function currentIndex(status: AgentRunStatus | null | undefined): number {
  if (!status) return -1;
  if (status.status === "completed") return STEPS.length - 1;
  if (status.status === "failed") return -1;
  const label = status.current_agent || "";
  for (let i = STEPS.length - 1; i >= 0; i--) {
    if (STEPS[i].match(label)) return i;
  }
  return 0;
}

export function AgentProgressTracker({
  status,
  className,
}: {
  status: AgentRunStatus | null | undefined;
  className?: string;
}) {
  if (!status) return null;
  const active = currentIndex(status);
  const failed = status.status === "failed";
  const done = status.status === "completed";

  return (
    <div className={cn("card-glass border border-border rounded-xl p-4 space-y-3", className)}>
      <div className="flex items-center justify-between gap-3">
        <div className="flex items-center gap-2">
          {!done && !failed && <PulseDot />}
          <p className="text-xs uppercase tracking-wide text-text-muted font-semibold">
            Agent Pipeline
          </p>
        </div>
        <span
          className={cn(
            "text-[10px] px-2 py-0.5 rounded-full font-semibold uppercase border",
            failed
              ? "text-red-400 border-red-500/30 bg-red-500/10"
              : done
              ? "text-green-400 border-green-500/30 bg-green-500/10"
              : "text-accent border-accent/30 bg-accent/10"
          )}
        >
          {failed ? "failed" : done ? "completed" : status.current_agent || "running"}
        </span>
      </div>

      {/* Progress bar */}
      <div className="h-1.5 bg-surface-elevated rounded-full overflow-hidden">
        <div
          className={cn(
            "h-full transition-all duration-500",
            failed ? "bg-red-400" : "bg-accent"
          )}
          style={{ width: `${status.progress_pct}%` }}
        />
      </div>

      {/* Step chips */}
      <div className="grid grid-cols-5 gap-1.5">
        {STEPS.map((step, i) => {
          const state =
            failed ? "idle" :
            done ? "done" :
            i < active ? "done" :
            i === active ? "active" :
            "idle";
          return (
            <div
              key={step.key}
              className={cn(
                "flex flex-col items-center gap-1 px-1.5 py-2 rounded-lg text-[10px] font-semibold text-center transition-colors",
                state === "done" && "bg-green-500/10 text-green-400 border border-green-500/30",
                state === "active" && "bg-accent/10 text-accent border border-accent/30 animate-pulse",
                state === "idle" && "bg-surface-elevated text-text-muted border border-border"
              )}
            >
              <StepIcon state={state} index={i} />
              <span className="truncate w-full">{step.label}</span>
            </div>
          );
        })}
      </div>

      {/* Summary — shown on both completed and failed so the Intel panel
          is NEVER blank. Falls back to a degraded message when the summary
          is missing (shouldn't happen after backend hardening, but belt
          and braces for any legacy in-flight rows). */}
      {done && (
        <p className="text-xs text-text-secondary leading-relaxed pt-1 border-t border-border/50">
          {status.summary || "Analysis completed."}
        </p>
      )}
      {failed && (
        <div className="space-y-1 pt-1 border-t border-red-500/20">
          <p className="text-xs text-text-secondary leading-relaxed">
            {status.summary || "Analysis temporarily unavailable — please retry."}
          </p>
          {status.error_message && (
            <p className="text-[10px] text-red-400/80 font-mono leading-relaxed">
              {status.error_message}
            </p>
          )}
        </div>
      )}
    </div>
  );
}

function PulseDot() {
  return (
    <span className="relative flex h-2 w-2">
      <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-accent opacity-75" />
      <span className="relative inline-flex rounded-full h-2 w-2 bg-accent" />
    </span>
  );
}

function StepIcon({ state, index }: { state: string; index: number }) {
  if (state === "done") {
    return (
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={3} className="w-3 h-3">
        <path d="M5 13l4 4L19 7" strokeLinecap="round" strokeLinejoin="round" />
      </svg>
    );
  }
  return <span className="text-[10px] font-mono">{index + 1}</span>;
}
