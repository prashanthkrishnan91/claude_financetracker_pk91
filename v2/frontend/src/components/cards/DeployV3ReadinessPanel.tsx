"use client";

import { useDeployV3Readiness } from "@/lib/hooks";
import { policyStatusLabel } from "@/lib/deploy-v3-helpers";
import { cn } from "@/lib/utils";
import { InlineLoader } from "@/components/ui/Spinner";
import type { DeployV3ReadinessDiagnostic } from "@/lib/api";

// ── Sub-sections ──────────────────────────────────────────────────────────────

function GateRow({
  label,
  ready,
}: {
  label: string;
  ready: boolean;
}) {
  return (
    <div className="flex items-center justify-between text-xs py-0.5">
      <span className="text-text-muted">{label}</span>
      <span
        className={cn(
          "font-semibold",
          ready ? "text-emerald-300" : "text-yellow-300",
        )}
      >
        {ready ? "Ready" : "Not ready"}
      </span>
    </div>
  );
}

function SnapshotSection({ snapshot }: { snapshot: DeployV3ReadinessDiagnostic["snapshot"] }) {
  const statusLabel =
    snapshot.status === "fresh"
      ? "Fresh"
      : snapshot.status === "stale"
        ? "Stale — more than 24 hours old"
        : "Missing";

  const statusCls =
    snapshot.status === "fresh"
      ? "text-emerald-300"
      : "text-yellow-300";

  return (
    <div className="space-y-0.5">
      <p className="text-[10px] uppercase tracking-wide text-text-muted font-semibold">
        Portfolio Snapshot
      </p>
      {!snapshot.present ? (
        <p className="text-xs text-yellow-300">No snapshot — create a fresh portfolio snapshot.</p>
      ) : (
        <>
          <div className="flex items-center justify-between text-xs">
            <span className="text-text-muted">Status</span>
            <span className={cn("font-semibold", statusCls)}>{statusLabel}</span>
          </div>
          {snapshot.age_hours !== null && (
            <div className="flex items-center justify-between text-xs">
              <span className="text-text-muted">Age</span>
              <span className="text-text-secondary font-mono">
                {snapshot.age_hours < 1
                  ? "< 1 hour"
                  : `${snapshot.age_hours.toFixed(1)} hours`}
              </span>
            </div>
          )}
        </>
      )}
    </div>
  );
}

function MarketValueSection({
  mv,
}: {
  mv: DeployV3ReadinessDiagnostic["market_values"];
}) {
  return (
    <div className="space-y-0.5">
      <p className="text-[10px] uppercase tracking-wide text-text-muted font-semibold">
        Market Value Coverage
      </p>
      {mv.all_positions_have_market_value ? (
        <p className="text-xs text-emerald-300">All {mv.position_count} positions have market values.</p>
      ) : (
        <>
          <p className="text-xs text-yellow-300">
            Missing market values for:{" "}
            <span className="font-mono">{mv.uncertified_tickers.join(", ")}</span>
          </p>
          <p className="text-xs text-text-muted">
            Create a fresh snapshot with valid prices.
          </p>
        </>
      )}
    </div>
  );
}

function TargetAllocSection({
  ta,
}: {
  ta: DeployV3ReadinessDiagnostic["target_allocations"];
}) {
  const hasMissing = ta.missing_tickers.length > 0;
  const hasConflicting = ta.conflicting_tickers.length > 0;
  const totalReady =
    ta.target_total_pct !== null &&
    ta.target_total_in_range === true;

  return (
    <div className="space-y-0.5">
      <p className="text-[10px] uppercase tracking-wide text-text-muted font-semibold">
        Target Allocations
      </p>
      {hasConflicting && (
        <p className="text-xs text-red-400">
          Duplicate rows — remove duplicates for:{" "}
          <span className="font-mono">{ta.conflicting_tickers.join(", ")}</span>
        </p>
      )}
      {hasMissing && (
        <p className="text-xs text-yellow-300">
          Missing allocations for:{" "}
          <span className="font-mono">{ta.missing_tickers.join(", ")}</span>
        </p>
      )}
      {ta.target_total_pct !== null && (
        <div className="flex items-center justify-between text-xs">
          <span className="text-text-muted">Total</span>
          <span
            className={cn(
              "font-mono font-semibold",
              totalReady ? "text-emerald-300" : "text-yellow-300",
            )}
          >
            {ta.target_total_pct.toFixed(1)}%
            {!totalReady && " — must be 98–102%"}
          </span>
        </div>
      )}
      {!hasMissing && !hasConflicting && totalReady && (
        <p className="text-xs text-emerald-300">All allocations set and total is in range.</p>
      )}
    </div>
  );
}

function PolicySection({
  policy,
}: {
  policy: DeployV3ReadinessDiagnostic["policy"];
}) {
  return (
    <div className="space-y-0.5">
      <p className="text-[10px] uppercase tracking-wide text-text-muted font-semibold">
        Deploy Policy
      </p>
      <div className="flex items-center justify-between text-xs">
        <span className="text-text-muted">Min. trade setting</span>
        <span
          className={cn(
            "font-semibold",
            policy.minimum_trade_configured ? "text-emerald-300" : "text-yellow-300",
          )}
        >
          {policy.minimum_trade_configured ? "Configured" : "Missing"}
        </span>
      </div>
      <div className="flex items-center justify-between text-xs">
        <span className="text-text-muted">Rounding setting</span>
        <span
          className={cn(
            "font-semibold",
            policy.rounding_policy_configured ? "text-emerald-300" : "text-yellow-300",
          )}
        >
          {policy.rounding_policy_configured ? "Configured" : "Missing"}
        </span>
      </div>
      <p
        className={cn(
          "text-xs",
          policy.policy_valid ? "text-emerald-300" : "text-yellow-300",
        )}
      >
        {policyStatusLabel(policy.policy_status)}
      </p>
    </div>
  );
}

// ── Main panel ────────────────────────────────────────────────────────────────

export function DeployV3ReadinessPanel() {
  const { data, isLoading, isError } = useDeployV3Readiness();

  if (isLoading) {
    return (
      <section
        aria-label="Deploy v3 exact-dollar readiness"
        className="card-glass p-4 border border-border/80 space-y-2"
      >
        <p className="text-[11px] font-semibold uppercase tracking-[0.14em] text-accent">
          Exact-Dollar Readiness
        </p>
        <InlineLoader text="Checking readiness…" />
      </section>
    );
  }

  if (isError || !data) {
    return (
      <section
        aria-label="Deploy v3 exact-dollar readiness"
        className="card-glass p-4 border border-border/80 space-y-2"
      >
        <p className="text-[11px] font-semibold uppercase tracking-[0.14em] text-accent">
          Exact-Dollar Readiness
        </p>
        <p className="text-sm text-text-secondary">
          Readiness diagnostic unavailable — check that Intel v3 is enabled.
        </p>
      </section>
    );
  }

  const isReady = data.exact_dollar_ready;

  return (
    <section
      aria-label="Deploy v3 exact-dollar readiness"
      className="card-glass p-4 border border-border/80 space-y-3"
    >
      {/* Header */}
      <div className="flex items-start justify-between gap-2 flex-wrap">
        <p className="text-[11px] font-semibold uppercase tracking-[0.14em] text-accent">
          Exact-Dollar Readiness
        </p>
        <span className="text-[10px] px-2 py-0.5 rounded-full border border-border bg-surface-elevated text-text-muted font-semibold uppercase tracking-wide">
          Read-only
        </span>
      </div>

      {/* Overall status + next action */}
      <div
        className={cn(
          "rounded-md px-3 py-2 border text-sm font-semibold leading-snug",
          isReady
            ? "border-emerald-700/50 bg-emerald-900/20 text-emerald-300"
            : "border-yellow-700/50 bg-yellow-900/10 text-yellow-300",
        )}
      >
        {data.next_required_action}
      </div>

      {/* Gate summary */}
      <div className="bg-surface-elevated/40 border border-border/60 rounded-md px-3 py-2 space-y-0.5">
        <p className="text-[10px] uppercase tracking-wide text-text-muted font-semibold mb-1">
          Gate status
        </p>
        <GateRow label="Snapshot + market values" ready={data.sizing_values_ready} />
        <GateRow label="Target allocations" ready={data.target_allocation_ready} />
        <GateRow label="Deploy policy" ready={data.policy_ready} />
      </div>

      {/* Detail sections */}
      <div className="space-y-3 pt-1 border-t border-border/50">
        <SnapshotSection snapshot={data.snapshot} />
        {data.snapshot.present && (
          <MarketValueSection mv={data.market_values} />
        )}
        <TargetAllocSection ta={data.target_allocations} />
        <PolicySection policy={data.policy} />
      </div>

      {/* Authority note */}
      <p className="text-[11px] text-text-muted leading-snug pt-1 border-t border-border/50">
        <span className="font-semibold text-text-secondary">Decision authority:</span>{" "}
        Intel v3 policy owns all Buy / Hold / Trim / Sell decisions. Deploy only sizes and validates actions.
      </p>
    </section>
  );
}
