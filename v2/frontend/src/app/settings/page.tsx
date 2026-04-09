"use client";

import { useState } from "react";
import { useAuth } from "@/lib/auth";
import { useSyncPlaid, usePlaidStatus, useRefreshPrices } from "@/lib/hooks";
import { Spinner } from "@/components/ui/Spinner";
import { cn } from "@/lib/utils";
import Link from "next/link";

export default function SettingsPage() {
  const { user, signOut } = useAuth();
  const plaidSync = useSyncPlaid();
  const { data: plaidStatus } = usePlaidStatus();
  const refreshPrices = useRefreshPrices();
  const [forceSync, setForceSync] = useState(false);

  return (
    <div className="min-h-screen pb-20 lg:pb-0">
      <header className="sticky top-0 z-50 bg-background/80 backdrop-blur-xl border-b border-border px-4 py-3">
        <div className="max-w-7xl mx-auto flex items-center justify-between">
          <h1 className="text-xl font-display text-text-primary">Settings</h1>
          <Link
            href="/dashboard"
            className="text-xs text-text-muted hover:text-text-primary"
          >
            Dashboard
          </Link>
        </div>
      </header>

      <main className="max-w-7xl mx-auto px-4 py-6 space-y-6">
        {/* Account */}
        <Section title="Account">
          <div className="flex items-center justify-between">
            <div>
              <p className="text-sm text-text-primary">{user?.email}</p>
              <p className="text-xs text-text-muted">Owner account</p>
            </div>
            <button
              onClick={signOut}
              className="text-xs px-3 py-1.5 rounded-md border border-danger/30 text-danger hover:bg-danger/10 transition-colors"
            >
              Sign out
            </button>
          </div>
        </Section>

        {/* Plaid Sync */}
        <Section title="Plaid / Robinhood Sync">
          {plaidStatus && (
            <div className="space-y-3">
              <div className="flex items-center gap-3">
                <StatusDot
                  status={
                    plaidStatus.status === "fresh"
                      ? "green"
                      : plaidStatus.status === "stale"
                      ? "yellow"
                      : "gray"
                  }
                />
                <div>
                  <p className="text-sm text-text-primary">
                    {plaidStatus.status === "fresh"
                      ? `Synced ${plaidStatus.age_hours?.toFixed(1)}h ago`
                      : plaidStatus.status === "stale"
                      ? `Stale (${plaidStatus.age_hours?.toFixed(1)}h old)`
                      : "Never synced"}
                  </p>
                  {plaidStatus.holdings_count !== undefined &&
                    plaidStatus.holdings_count > 0 && (
                      <p className="text-xs text-text-muted">
                        {plaidStatus.holdings_count} holdings &middot; $
                        {plaidStatus.cash_balance?.toFixed(2)} cash
                      </p>
                    )}
                </div>
              </div>

              <div className="flex items-center gap-3">
                <label className="flex items-center gap-2 text-xs text-text-muted cursor-pointer">
                  <input
                    type="checkbox"
                    checked={forceSync}
                    onChange={(e) => setForceSync(e.target.checked)}
                    className="rounded bg-surface border-border"
                  />
                  Force re-sync (bypass 24h cache)
                </label>
              </div>

              <button
                onClick={() => plaidSync.mutate(forceSync)}
                disabled={plaidSync.isPending}
                className="px-4 py-2 bg-accent text-background font-semibold rounded-lg text-sm hover:bg-accent-hover transition-colors disabled:opacity-50"
              >
                {plaidSync.isPending ? (
                  <span className="flex items-center gap-2">
                    <Spinner className="h-3 w-3" /> Syncing...
                  </span>
                ) : (
                  "Sync Plaid"
                )}
              </button>

              {plaidSync.isSuccess && (
                <p className="text-xs text-accent">
                  {plaidSync.data.message}
                </p>
              )}
              {plaidSync.isError && (
                <p className="text-xs text-danger">
                  Sync failed: {plaidSync.error?.message}
                </p>
              )}
            </div>
          )}
        </Section>

        {/* Price Refresh */}
        <Section title="Price Data">
          <div className="space-y-3">
            <p className="text-xs text-text-muted">
              Fires all price sources concurrently (yfinance, Finnhub, Alpaca,
              CoinGecko). First valid result wins.
            </p>
            <button
              onClick={() => refreshPrices.mutate()}
              disabled={refreshPrices.isPending}
              className="px-4 py-2 bg-surface-elevated text-text-primary font-medium rounded-lg text-sm hover:bg-border transition-colors disabled:opacity-50"
            >
              {refreshPrices.isPending ? (
                <span className="flex items-center gap-2">
                  <Spinner className="h-3 w-3" /> Refreshing...
                </span>
              ) : (
                "Refresh All Prices"
              )}
            </button>
            {refreshPrices.isSuccess && refreshPrices.data && (
              <div className="text-xs text-text-muted space-y-1">
                <p>
                  Fresh: {refreshPrices.data.fresh} / {refreshPrices.data.total}
                </p>
                {refreshPrices.data.sources_used.length > 0 && (
                  <p>
                    Sources: {refreshPrices.data.sources_used.join(", ")}
                  </p>
                )}
              </div>
            )}
          </div>
        </Section>

        {/* CSV Import link */}
        <Section title="Data Import">
          <Link
            href="/dashboard/import"
            className="inline-block px-4 py-2 bg-surface-elevated text-text-primary font-medium rounded-lg text-sm hover:bg-border transition-colors"
          >
            Import Robinhood CSV
          </Link>
          <p className="text-xs text-text-muted mt-2">
            Upload Robinhood transaction exports. SHA-256 fingerprinting
            prevents duplicate imports.
          </p>
        </Section>

        {/* App info */}
        <Section title="About">
          <div className="text-xs text-text-muted space-y-1">
            <p>Portfolio Intelligence Platform v2.0</p>
            <p>FastAPI + Next.js 14 + Supabase + Tailwind CSS</p>
            <p>Concurrent multi-source price engine</p>
          </div>
        </Section>
      </main>
    </div>
  );
}

function Section({
  title,
  children,
}: {
  title: string;
  children: React.ReactNode;
}) {
  return (
    <div className="card-glass p-4 space-y-3">
      <h2 className="text-sm font-semibold text-text-secondary uppercase tracking-wide">
        {title}
      </h2>
      {children}
    </div>
  );
}

function StatusDot({
  status,
}: {
  status: "green" | "yellow" | "gray";
}) {
  return (
    <span
      className={cn(
        "w-2 h-2 rounded-full shrink-0",
        status === "green"
          ? "bg-accent"
          : status === "yellow"
          ? "bg-warning"
          : "bg-text-muted"
      )}
    />
  );
}
