"use client";

import { useDeployV3Plan } from "@/lib/hooks";
import { isNoSnapshotError, readinessMeta } from "@/lib/deploy-v3-helpers";
import { cn } from "@/lib/utils";
import { InlineLoader } from "@/components/ui/Spinner";

// ── Count row ─────────────────────────────────────────────────────────────────

function CountRow({ label, value, cls }: { label: string; value: number; cls?: string }) {
  if (value === 0) return null;
  return (
    <div className="flex items-center justify-between text-xs py-1 border-b border-border/50 last:border-0">
      <span className="text-text-muted">{label}</span>
      <span className={cn("font-mono font-semibold", cls ?? "text-text-primary")}>{value}</span>
    </div>
  );
}

// ── Panel ─────────────────────────────────────────────────────────────────────

export function DeployV3Panel() {
  const { data, isLoading, isError, error } = useDeployV3Plan();

  if (isLoading) {
    return (
      <section aria-label="Deploy v3 plan readiness" className="card-glass p-4 border border-border/80">
        <p className="text-[11px] font-semibold uppercase tracking-[0.14em] text-accent mb-2">
          Intel v3 Deploy Readiness
        </p>
        <InlineLoader text="Loading deploy readiness…" />
      </section>
    );
  }

  if (isError) {
    const noSnapshot = isNoSnapshotError(error);
    return (
      <section aria-label="Deploy v3 plan readiness" className="card-glass p-4 border border-border/80 space-y-2">
        <p className="text-[11px] font-semibold uppercase tracking-[0.14em] text-accent">
          Intel v3 Deploy Readiness
        </p>
        <p className="text-sm text-text-secondary">
          {noSnapshot
            ? "Run Intel v3 first to see your deploy readiness."
            : "Deploy v3 plan unavailable — check that Intel v3 is enabled."}
        </p>
        {noSnapshot && (
          <p className="text-xs text-text-muted">
            Go to the Intel tab, run Intel v3, then return here.
          </p>
        )}
      </section>
    );
  }

  if (!data) return null;

  const { rollup, source } = data;
  const sizingConnected = source?.sizing_bundle_provided ?? false;
  const meta = readinessMeta(rollup?.plan_readiness_status ?? "not_ready");

  return (
    <section aria-label="Deploy v3 plan readiness" className="card-glass p-4 border border-border/80 space-y-3">
      {/* Header */}
      <div className="flex items-start justify-between gap-2 flex-wrap">
        <p className="text-[11px] font-semibold uppercase tracking-[0.14em] text-accent">
          Intel v3 Deploy Readiness
        </p>
        <span className="text-[10px] px-2 py-0.5 rounded-full border border-border bg-surface-elevated text-text-muted font-semibold uppercase tracking-wide">
          Read-only
        </span>
      </div>

      {/* Readiness status */}
      <p className={cn("text-sm font-semibold leading-snug", meta.cls)}>
        {meta.label}
      </p>

      {/* Counts */}
      {rollup && rollup.total_items > 0 && (
        <div className="bg-surface-elevated/40 border border-border/60 rounded-md px-3 py-2 space-y-0">
          <p className="text-[10px] uppercase tracking-wide text-text-muted font-semibold mb-1.5">
            Items · {rollup.total_items} total
          </p>
          <CountRow
            label="Pending guardrail review"
            value={rollup.pending_count}
            cls="text-emerald-300"
          />
          <CountRow
            label="Blocked — cash constraint"
            value={rollup.blocked_count}
            cls="text-red-400"
          />
          <CountRow
            label="Hold as planned"
            value={rollup.informational_count}
            cls="text-blue-300"
          />
          <CountRow
            label="Suppressed — evidence gaps"
            value={rollup.suppressed_count}
            cls="text-yellow-300"
          />
          <CountRow
            label="Not sized yet"
            value={rollup.not_ready_count}
            cls="text-text-muted"
          />
          {rollup.unknown_count > 0 && (
            <CountRow
              label="Unknown status"
              value={rollup.unknown_count}
              cls="text-text-muted"
            />
          )}
        </div>
      )}

      {/* Source note */}
      <div className="space-y-1.5 pt-1 border-t border-border/50">
        <p className="text-[11px] text-text-muted leading-snug">
          <span className="font-semibold text-text-secondary">Decision authority:</span>{" "}
          Intel v3 policy owns all Buy / Hold / Trim / Sell decisions. Deploy reads Intel output only.
        </p>
        {!sizingConnected && (
          <p className="text-[11px] text-yellow-300/80 leading-snug">
            Exact dollar amounts are not connected yet — no executable trade sizing available.
            Dollar fields shown here are scaffold placeholders only.
          </p>
        )}
      </div>
    </section>
  );
}
