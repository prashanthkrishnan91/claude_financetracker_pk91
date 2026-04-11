"use client";

import { useEffect } from "react";
import { PortfolioSummaryCard } from "@/components/holdings/PortfolioSummaryCard";
import { HoldingsList } from "@/components/holdings/HoldingsList";
import { PortfolioChart } from "@/components/charts/PortfolioChart";
import { useAuth } from "@/lib/auth";
import {
  usePlaidStatus,
  useSnapshots,
  useCreateSnapshot,
  usePortfolioSummary,
} from "@/lib/hooks";

export default function DashboardPage() {
  const { signOut } = useAuth();
  const { data: plaidStatus } = usePlaidStatus();
  const { data: snapshots } = useSnapshots(5);
  const { data: summary } = usePortfolioSummary();
  const createSnapshot = useCreateSnapshot();

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

  return (
    <>
      {/* Top bar */}
      <header className="sticky top-0 z-50 bg-background/80 backdrop-blur-xl border-b border-border px-4 py-3">
        <div className="max-w-7xl mx-auto flex items-center justify-between">
          <h1 className="text-xl font-display text-text-primary">Portfolio</h1>
          <div className="flex items-center gap-3">
            {plaidStatus && (
              <PlaidBadge status={plaidStatus.status} age={plaidStatus.age_hours} />
            )}
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

      <main className="max-w-7xl mx-auto px-4 py-6 space-y-6">
        <PortfolioSummaryCard />

        <section className="card-glass p-4">
          <PortfolioChart />
        </section>

        <section>
          <h2 className="text-lg font-semibold text-text-primary mb-3">
            Holdings
          </h2>
          <HoldingsList />
        </section>
      </main>
    </>
  );
}

function PlaidBadge({
  status,
  age,
}: {
  status: string;
  age?: number;
}) {
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
