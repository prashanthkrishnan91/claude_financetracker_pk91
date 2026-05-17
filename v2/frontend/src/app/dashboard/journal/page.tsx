"use client";

import { cn } from "@/lib/utils";
import { useDecisionMemoryLogs } from "@/lib/hooks";
import { InlineLoader } from "@/components/ui/Spinner";
import { EmptyState } from "@/components/ui/EmptyState";
import {
  buildJournalEntries,
  JOURNAL_LESSONS_CAPTION,
  JOURNAL_WHAT_I_LEARNED_CAPTION,
} from "@/lib/journal-ledger";
import type { JournalEntry, JournalEvaluationState } from "@/lib/journal-ledger";
import { ComingLaterPanel } from "@/components/cards/IntelV3Primitives";

// ── Evaluation state badge ────────────────────────────────────────────────────

function EvalBadge({ state }: { state: JournalEvaluationState }) {
  const styles: Record<string, string> = {
    pending: "bg-surface-elevated text-text-muted border-border",
    window_open: "bg-accent/10 text-accent border-accent/25",
    ready: "bg-green-500/10 text-green-400 border-green-500/25",
    unavailable: "bg-surface-elevated text-text-muted border-border",
  };
  return (
    <span
      className={cn(
        "inline-flex items-center rounded px-1.5 py-0.5 text-[10px] font-semibold tracking-wide uppercase border",
        styles[state.kind] ?? styles.pending
      )}
      title={state.detail}
    >
      {state.label}
    </span>
  );
}

// ── Decision row ──────────────────────────────────────────────────────────────

function DecisionLine({ d }: { d: JournalEntry["decisions"][number] }) {
  const actionStyles: Record<string, string> = {
    BOUGHT:  "text-action-buy",
    TRIMMED: "text-action-trim",
    SOLD:    "text-action-sell",
    PARTIAL: "text-action-buy",
    REPLACED: "text-action-buy",
    SKIPPED: "text-text-muted",
    WATCHED: "text-text-muted",
    HELD:    "text-text-muted",
  };
  const actionColor = actionStyles[d.actualAction.toUpperCase()] ?? "text-text-secondary";

  return (
    <div className="flex items-center gap-2 py-0.5">
      <span className="text-xs font-semibold text-text-primary font-mono tracking-tight w-16 shrink-0">
        {d.ticker}
      </span>
      <span className={cn("text-xs font-semibold", actionColor)}>{d.actualAction}</span>
      {d.actualAmountFormatted && (
        <span className="text-xs text-text-secondary tabular-nums font-mono">
          {d.actualAmountFormatted}
        </span>
      )}
      {d.isManual && (
        <span className="text-[10px] text-text-muted border border-border rounded px-1 py-0.5 uppercase tracking-wide">
          Manual
        </span>
      )}
    </div>
  );
}

// ── Status badge ──────────────────────────────────────────────────────────────

function StatusBadge({ status }: { status: string }) {
  const styles: Record<string, string> = {
    "Fully executed":     "bg-green-500/10 text-green-400 border-green-500/25",
    "Partially executed": "bg-accent/10 text-accent border-accent/25",
    "Skipped":            "bg-surface-elevated text-text-muted border-border",
    "Draft":              "bg-yellow-500/10 text-yellow-400 border-yellow-500/25",
  };
  return (
    <span
      className={cn(
        "inline-flex items-center rounded px-1.5 py-0.5 text-[10px] font-semibold tracking-wide uppercase border",
        styles[status] ?? "bg-surface-elevated text-text-muted border-border"
      )}
    >
      {status}
    </span>
  );
}

// ── Journal entry card ────────────────────────────────────────────────────────

function EntryCard({ entry }: { entry: JournalEntry }) {
  const date = new Date(entry.createdAt).toLocaleDateString("en-US", {
    month: "long",
    day: "numeric",
    year: "numeric",
  });

  return (
    <div className="rounded-xl border border-border bg-surface overflow-hidden">
      {/* Chapter header */}
      <div className="px-4 pt-3.5 pb-2.5 border-b border-border/60 bg-surface-elevated/30 flex items-baseline gap-3">
        <span className="font-display text-2xl font-light text-accent/60 leading-none select-none">
          {entry.chapterNumeral}
        </span>
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 flex-wrap">
            <time
              className="text-xs font-medium text-text-secondary"
              dateTime={entry.createdAt}
            >
              {date}
            </time>
            <span className="text-[11px] text-text-muted">{entry.sourceLabel}</span>
          </div>
        </div>
        <StatusBadge status={entry.statusLabel} />
      </div>

      {/* Decisions */}
      {entry.decisions.length > 0 ? (
        <div className="px-4 pt-3 pb-2.5 border-b border-border/50">
          <p className="text-[10px] font-semibold uppercase tracking-widest text-text-muted mb-1.5">
            Decisions
          </p>
          <div className="space-y-0.5">
            {entry.decisions.map((d, i) => (
              <DecisionLine key={`${d.ticker}-${i}`} d={d} />
            ))}
          </div>
          {entry.cashDeployedFormatted && (
            <p className="text-xs text-text-muted mt-2">
              Capital deployed:{" "}
              <span className="font-semibold text-text-secondary tabular-nums">
                {entry.cashDeployedFormatted}
              </span>
            </p>
          )}
        </div>
      ) : (
        <div className="px-4 py-3 border-b border-border/50">
          <p className="text-xs text-text-muted italic">No decision rows recorded.</p>
        </div>
      )}

      {/* Notes */}
      {entry.notes?.trim() && (
        <div className="px-4 pt-2.5 pb-2.5 border-b border-border/50">
          <p className="text-[10px] font-semibold uppercase tracking-widest text-text-muted mb-1">
            Notes
          </p>
          <p className="text-xs text-text-secondary leading-relaxed">{entry.notes}</p>
        </div>
      )}

      {/* Evaluation window */}
      <div className="px-4 py-3 flex items-center justify-between gap-3">
        <div className="min-w-0">
          <p className="text-[10px] font-semibold uppercase tracking-widest text-text-muted mb-0.5">
            Outcome evaluation
          </p>
          <p className="text-xs text-text-muted leading-snug">{entry.evaluationState.detail}</p>
        </div>
        <EvalBadge state={entry.evaluationState} />
      </div>
    </div>
  );
}

// ── Page ──────────────────────────────────────────────────────────────────────

export default function JournalPage() {
  const { data: logs, isLoading, error } = useDecisionMemoryLogs(50);
  const entries = buildJournalEntries(logs ?? []);

  return (
    <div className="max-w-2xl mx-auto px-4 py-6 space-y-5">
      {/* Header */}
      <div>
        <p className="text-[10px] font-mono uppercase tracking-[0.14em] text-accent opacity-50">
          Decision Memory
        </p>
        <h1 className="text-xl font-semibold text-text-primary tracking-tight mt-0.5">
          Journal
        </h1>
        <p className="text-xs text-text-muted mt-1">
          A chapter-by-chapter record of the decisions you made — when, why, and what came of them.
        </p>
      </div>

      {/* Timeline */}
      <section aria-label="Decision timeline">
        {isLoading ? (
          <InlineLoader text="Loading journal…" />
        ) : error ? (
          <div className="rounded-xl border border-border bg-surface px-4 py-6 text-center">
            <p className="text-xs text-red-400">
              Could not load journal entries. The backend may be unavailable.
            </p>
          </div>
        ) : entries.length === 0 ? (
          <EmptyState
            title="No journal entries yet"
            description="Journal entries appear here once you save decisions from the Deploy tab. Each entry records what you decided, how much you deployed, and opens an outcome evaluation window."
          />
        ) : (
          <div className="space-y-4">
            {entries.map((entry) => (
              <EntryCard key={entry.id} entry={entry} />
            ))}
          </div>
        )}
      </section>

      {/* Coming-Later: Lessons surface */}
      <section aria-label="Coming later modules" className="space-y-3 pt-1">
        <div className="flex items-center gap-2">
          <span className="flex-1 h-px bg-border-subtle/40" />
          <span className="text-[10px] uppercase tracking-widest text-text-muted opacity-40">
            Coming later
          </span>
          <span className="flex-1 h-px bg-border-subtle/40" />
        </div>
        <ComingLaterPanel
          title="Lessons"
          caption={JOURNAL_LESSONS_CAPTION}
        />
        <ComingLaterPanel
          title="What I learned today"
          caption={JOURNAL_WHAT_I_LEARNED_CAPTION}
        />
      </section>
    </div>
  );
}
