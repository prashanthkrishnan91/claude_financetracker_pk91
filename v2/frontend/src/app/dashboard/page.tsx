"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { PortfolioSummaryCard } from "@/components/holdings/PortfolioSummaryCard";
import { HoldingsList } from "@/components/holdings/HoldingsList";
import { PortfolioChart } from "@/components/charts/PortfolioChart";
import { useAuth } from "@/lib/auth";
import {
  usePlaidStatus,
  useSnapshots,
  useCreateSnapshot,
  usePortfolioSummary,
  useIntelV3Snapshot,
  useDeployV3Plan,
  useAlertCandidates,
} from "@/lib/hooks";
import { DataHealthDrawer } from "@/components/cards/DataHealthDrawer";
import {
  buildTheBrief,
  buildActToday,
  buildRiskPulse,
  buildDeployReady,
  buildWatchtowerSummary,
  buildLearningSlotCaption,
  type ActTodayRow,
} from "@/lib/today-command-center";

export default function DashboardPage() {
  const { signOut } = useAuth();
  const { data: plaidStatus } = usePlaidStatus();
  const { data: snapshots } = useSnapshots(5);
  const { data: summary } = usePortfolioSummary();
  const createSnapshot = useCreateSnapshot();

  const { data: intelSnapshot } = useIntelV3Snapshot();
  const { data: deployPlan } = useDeployV3Plan();
  const { data: alertCandidates } = useAlertCandidates(50);

  const [dataHealthOpen, setDataHealthOpen] = useState(false);

  // Empty string on server; set after mount to avoid locale/hydration mismatch.
  const [todayLabel, setTodayLabel] = useState("");
  useEffect(() => {
    setTodayLabel(
      new Date().toLocaleDateString(undefined, {
        weekday: "long",
        month: "long",
        day: "numeric",
      })
    );
  }, []);

  // Auto-create one snapshot per day when the dashboard loads and the portfolio
  // has positions. Checks whether the most recent snapshot was taken today to
  // avoid creating duplicates on page refreshes.
  useEffect(() => {
    if (
      snapshots === undefined ||
      summary === undefined ||
      summary.positions_count === 0 ||
      createSnapshot.isPending ||
      createSnapshot.isSuccess
    ) {
      return;
    }
    const today = new Date().toDateString();
    const latestSnapshotDate =
      snapshots.length > 0
        ? new Date(snapshots[0].snapshot_at).toDateString()
        : null;
    if (latestSnapshotDate !== today) {
      createSnapshot.mutate();
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [snapshots, summary]);

  // Derive Today modules from existing data — deterministic, no LLM
  const brief = buildTheBrief(intelSnapshot, deployPlan, alertCandidates);
  const actToday = buildActToday(intelSnapshot);
  const riskPulse = buildRiskPulse(intelSnapshot);
  const deployReady = buildDeployReady(deployPlan);
  const watchtowerSummary = buildWatchtowerSummary(alertCandidates);

  return (
    <>
      {/* Top bar */}
      <header className="sticky top-0 z-50 bg-background/80 backdrop-blur-xl border-b border-border px-4 py-3">
        <div className="max-w-7xl mx-auto flex items-center justify-between">
          <div>
            <h1 className="text-xl font-display text-text-primary">Today</h1>
            <p className="text-[10px] uppercase tracking-label text-text-muted opacity-50 leading-none mt-0.5">
              {todayLabel}
            </p>
          </div>
          <div className="flex items-center gap-3">
            {plaidStatus && (
              <PlaidBadge status={plaidStatus.status} age={plaidStatus.age_hours} />
            )}
            <button
              onClick={() => setDataHealthOpen(true)}
              className="text-xs text-text-muted hover:text-text-primary transition-colors"
            >
              <span className="sm:hidden">Health</span>
              <span className="hidden sm:inline">Data Health</span>
            </button>
            <span className="text-xs text-text-muted hidden sm:inline">v2.0</span>
            <button
              onClick={signOut}
              className="text-xs text-text-muted hover:text-danger transition-colors"
            >
              Sign out
            </button>
          </div>
        </div>
      </header>

      <main className="max-w-7xl mx-auto px-4 py-6 space-y-4">

        {/* ── Today fold ───────────────────────────────────────────────────── */}

        {/* The Brief */}
        <section className="card-glass p-5">
          <p className="text-[10px] uppercase tracking-label text-text-muted opacity-60 mb-3">
            The Brief
          </p>
          <div className="space-y-1.5">
            {brief.sentences.map((sentence, i) => (
              <p
                key={i}
                className={`text-sm leading-relaxed ${
                  brief.dataAvailable ? "text-text-secondary" : "text-text-muted italic"
                }`}
              >
                {sentence}
              </p>
            ))}
          </div>
        </section>

        {/* Act Today + Risk Pulse */}
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">

          {/* Act Today */}
          <section className="card-glass p-5">
            <p className="text-[10px] uppercase tracking-label text-text-muted opacity-60 mb-3">
              Act Today
            </p>
            {!intelSnapshot ? (
              <p className="text-sm text-text-muted italic">
                Intel snapshot unavailable — run Intel to generate recommendations.
              </p>
            ) : actToday.allHold && !actToday.hasActionableItems ? (
              <p className="text-sm text-text-secondary">
                All holdings are on Hold — no new actions today.
              </p>
            ) : !actToday.hasActionableItems ? (
              <p className="text-sm text-text-muted italic">
                No actionable recommendations in the current snapshot.
              </p>
            ) : (
              <div className="space-y-0 divide-y divide-border-subtle/30">
                {actToday.rows.map(row => (
                  <ActTodayCard key={row.ticker} row={row} />
                ))}
              </div>
            )}
          </section>

          {/* Risk Pulse */}
          <section className="card-glass p-5">
            <p className="text-[10px] uppercase tracking-label text-text-muted opacity-60 mb-3">
              Risk Pulse
            </p>
            {!intelSnapshot ? (
              <p className="text-sm text-text-muted italic">Intel data unavailable.</p>
            ) : !riskPulse.hasElevatedRisk ? (
              <p className="text-sm text-text-secondary">
                No elevated risk flags in the current snapshot.
              </p>
            ) : (
              <div className="space-y-3">
                {riskPulse.rows.map(row => (
                  <div key={row.ticker} className="flex items-start gap-2.5">
                    <span className="mt-1.5 w-2 h-2 rounded-full bg-warning shrink-0" />
                    <div>
                      <span className="text-sm font-medium text-text-primary">
                        {row.ticker}
                      </span>
                      <span className="text-xs text-text-muted ml-2">{row.riskLevel}</span>
                      {row.riskText && (
                        <p className="text-xs text-text-muted mt-0.5 leading-relaxed">
                          {row.riskText}
                        </p>
                      )}
                    </div>
                  </div>
                ))}
              </div>
            )}
          </section>
        </div>

        {/* Deploy Ready + Watchtower Summary */}
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">

          {/* Deploy Ready */}
          <section className="card-glass p-5">
            <p className="text-[10px] uppercase tracking-label text-text-muted opacity-60 mb-2">
              Deploy Ready
            </p>
            {!deployReady.hasData ? (
              <p className="text-sm text-text-muted italic">
                Deploy plan not yet available — set up targets in the Deploy tab.
              </p>
            ) : (
              <div>
                <p className="text-sm text-text-secondary mb-1">
                  {deployReady.planReadinessLabel}
                </p>
                {deployReady.buyCount > 0 && (
                  <p className="text-xs text-text-muted mb-1">
                    {deployReady.buyCount === 1
                      ? "1 Buy candidate"
                      : `${deployReady.buyCount} Buy candidates`}
                  </p>
                )}
                {deployReady.cashNote && (
                  <p className="text-xs text-text-muted mb-2">{deployReady.cashNote}</p>
                )}
                <Link
                  href="/dashboard/deposits"
                  className="text-xs text-accent hover:underline"
                >
                  Review Deploy plan →
                </Link>
              </div>
            )}
          </section>

          {/* Watchtower Summary */}
          <section className="card-glass p-5">
            <p className="text-[10px] uppercase tracking-label text-text-muted opacity-60 mb-2">
              Watchtower
            </p>
            <p className="text-sm text-text-secondary mb-2">{watchtowerSummary.summaryLine}</p>
            {watchtowerSummary.candidateCount > 0 && (
              <Link
                href="/dashboard/alerts"
                className="text-xs text-accent hover:underline"
              >
                View alerts →
              </Link>
            )}
          </section>
        </div>

        {/* What I Learned Today — Coming-Later chrome (Stage 6E activates) */}
        <section className="card-glass p-5 opacity-50">
          <p className="text-[10px] uppercase tracking-label text-text-muted opacity-60 mb-2">
            What I Learned Today
          </p>
          <p className="text-sm text-text-muted coming-later italic">
            {buildLearningSlotCaption()}
          </p>
        </section>

        {/* ── Portfolio snapshot ────────────────────────────────────────────── */}

        <div className="border-t border-border-subtle/40 pt-4 mt-2">
          <p className="text-[10px] uppercase tracking-label text-text-muted opacity-40 mb-4">
            Portfolio Snapshot
          </p>
        </div>

        <PortfolioSummaryCard />

        <section className="card-glass p-4">
          <PortfolioChart />
        </section>

        <section>
          <h2 className="text-lg font-semibold text-text-primary mb-3">Holdings</h2>
          <HoldingsList />
        </section>
      </main>

      {/* Data Health drawer — mounted only when open so hooks don't run while closed */}
      {dataHealthOpen && <DataHealthDrawer open={dataHealthOpen} onClose={() => setDataHealthOpen(false)} />}
    </>
  );
}

// ── Act Today card ─────────────────────────────────────────────────────────────

const ACTION_CHIP_CLASSES: Record<"BUY" | "TRIM" | "SELL", string> = {
  BUY: "bg-accent/10 text-accent",
  TRIM: "bg-warning/10 text-warning",
  SELL: "bg-danger/10 text-danger",
};

function ActTodayCard({ row }: { row: ActTodayRow }) {
  const [expanded, setExpanded] = useState(false);
  const chipClass = ACTION_CHIP_CLASSES[row.action] ?? "bg-surface-elevated text-text-muted";

  return (
    <div className="py-3 first:pt-0 last:pb-0">
      <div className="flex items-start justify-between gap-2">
        <div className="flex items-center gap-2 min-w-0">
          <span
            className={`text-[10px] font-semibold px-1.5 py-0.5 rounded uppercase tracking-label shrink-0 ${chipClass}`}
          >
            {row.action}
          </span>
          <div className="min-w-0">
            <span className="text-sm font-medium text-text-primary">{row.ticker}</span>
            <span className="text-xs text-text-muted ml-1.5 truncate hidden sm:inline">
              {row.name}
            </span>
          </div>
        </div>
        <span className="text-[10px] text-text-muted shrink-0 mt-0.5 uppercase">
          {row.conviction}
        </span>
      </div>

      {/* Why this matters — expandable, only when data supports it */}
      {row.whyThisMatters && (
        <div className="mt-1.5 ml-8">
          <button
            onClick={() => setExpanded(e => !e)}
            className="text-[10px] text-text-muted hover:text-text-secondary transition-colors"
            aria-expanded={expanded}
          >
            {expanded ? "Hide reason ↑" : "Why this matters ↓"}
          </button>
          {expanded && (
            <p className="text-xs text-text-muted mt-1 leading-relaxed">{row.whyThisMatters}</p>
          )}
        </div>
      )}
    </div>
  );
}

// ── Plaid badge ────────────────────────────────────────────────────────────────

function PlaidBadge({ status, age }: { status: string; age?: number }) {
  const fresh = status === "fresh";
  const never = status === "never_synced";

  return (
    <span
      className={`text-[10px] px-2 py-0.5 rounded-full ${
        fresh
          ? "bg-accent/10 text-accent"
          : never
          ? "bg-surface-elevated text-text-muted"
          : "bg-warning/10 text-warning"
      }`}
    >
      {fresh
        ? `Synced ${age?.toFixed(0)}h ago`
        : never
        ? "Not synced"
        : `Stale (${age?.toFixed(0)}h)`}
    </span>
  );
}
