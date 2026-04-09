"use client";

import { useQueryClient } from "@tanstack/react-query";
import { cn, formatCurrency, formatPercent } from "@/lib/utils";
import { useDripSummary, useDripPositions, useDripHistory } from "@/lib/hooks";
import { InlineLoader } from "@/components/ui/Spinner";
import { EmptyState } from "@/components/ui/EmptyState";
import { Spinner } from "@/components/ui/Spinner";
import type { DripPosition, DripHistoryEntry } from "@/lib/api";

export default function DripPage() {
  const qc = useQueryClient();
  const { data: summary, isLoading: summaryLoading } = useDripSummary();
  const { data: positions, isLoading: positionsLoading } = useDripPositions();
  const { data: history, isLoading: historyLoading } = useDripHistory();

  const isLoading = summaryLoading || positionsLoading || historyLoading;

  const sortedPositions = [...(positions || [])].sort(
    (a, b) => b.annual_income - a.annual_income
  );

  function handleRefresh() {
    qc.invalidateQueries({ queryKey: ["drip"] });
  }

  return (
    <>
      <header className="sticky top-0 z-50 bg-background/80 backdrop-blur-xl border-b border-border px-4 py-3">
        <div className="max-w-7xl mx-auto flex items-center justify-between">
          <h1 className="text-xl font-display text-text-primary">DRIP</h1>
          <button
            onClick={handleRefresh}
            className="text-xs px-3 py-1.5 rounded-md bg-surface-elevated text-text-secondary hover:text-text-primary border border-border transition-colors flex items-center gap-1.5"
          >
            <RefreshIcon className="w-3 h-3" />
            Refresh
          </button>
        </div>
      </header>

      <main className="max-w-7xl mx-auto px-4 py-6 space-y-6">
        {/* Summary KPI row */}
        {summaryLoading ? (
          <InlineLoader text="Loading DRIP data..." />
        ) : summary ? (
          <>
            <div className="grid grid-cols-3 gap-3">
              <div className="card-glass p-4 text-center space-y-1">
                <p className="text-xs text-text-muted uppercase tracking-wide">Lifetime Earned</p>
                <p className="font-mono text-lg font-semibold text-accent">
                  {formatCurrency(summary.lifetime_earned)}
                </p>
              </div>
              <div className="card-glass p-4 text-center space-y-1">
                <p className="text-xs text-text-muted uppercase tracking-wide">Annual Projection</p>
                <p className="font-mono text-lg font-semibold text-text-primary">
                  {formatCurrency(summary.annual_projection)}
                </p>
              </div>
              <div className="card-glass p-4 text-center space-y-1">
                <p className="text-xs text-text-muted uppercase tracking-wide">Est. Monthly</p>
                <p className="font-mono text-lg font-semibold text-text-primary">
                  {formatCurrency(summary.monthly_estimate)}
                </p>
              </div>
            </div>

            {/* Sub-stat row */}
            <div className="flex flex-wrap items-center gap-3">
              {summary.top_earner && (
                <span className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-full bg-accent/10 border border-accent/20 text-accent text-xs font-semibold">
                  <DropletSmallIcon className="w-3 h-3" />
                  Top Earner: {summary.top_earner}
                </span>
              )}
              <span className="text-xs text-text-muted">
                <span className="font-mono font-semibold text-text-secondary">
                  {summary.positions_with_drip}
                </span>{" "}
                {summary.positions_with_drip === 1 ? "position" : "positions"} earning dividends
              </span>
            </div>
          </>
        ) : (
          <EmptyState title="No DRIP data" description="Import your Robinhood CSV to see dividend data." />
        )}

        {/* Two-column layout */}
        <div className="grid md:grid-cols-2 gap-6">
          {/* Dividend Positions table */}
          <div className="space-y-3">
            <h2 className="text-sm font-semibold text-text-secondary uppercase tracking-wide">
              Dividend Positions
            </h2>
            {positionsLoading ? (
              <InlineLoader text="Loading positions..." />
            ) : sortedPositions.length === 0 ? (
              <EmptyState
                title="No dividend positions"
                description="Import your Robinhood CSV to see dividend positions."
              />
            ) : (
              <div className="card-glass overflow-hidden">
                <div className="overflow-x-auto">
                  <table className="w-full text-xs">
                    <thead>
                      <tr className="border-b border-border text-text-muted">
                        <th className="text-left px-3 py-2 font-medium">Ticker</th>
                        <th className="text-right px-3 py-2 font-medium">Annual</th>
                        <th className="text-right px-3 py-2 font-medium">Yield</th>
                        <th className="text-right px-3 py-2 font-medium">Shares</th>
                        <th className="text-right px-3 py-2 font-medium">DRIP</th>
                        <th className="text-right px-3 py-2 font-medium">DRIP Val</th>
                        <th className="text-right px-3 py-2 font-medium">Ex-Date</th>
                        <th className="text-right px-3 py-2 font-medium">Pay-Date</th>
                      </tr>
                    </thead>
                    <tbody>
                      {sortedPositions.map((pos) => (
                        <DripPositionRow key={pos.ticker} pos={pos} />
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            )}
          </div>

          {/* History section */}
          <div className="space-y-3">
            <h2 className="text-sm font-semibold text-text-secondary uppercase tracking-wide">
              Dividend History
            </h2>
            {historyLoading ? (
              <InlineLoader text="Loading history..." />
            ) : !history || history.length === 0 ? (
              <EmptyState
                title="No dividend history"
                description="Import your Robinhood CSV to see dividend transactions."
              />
            ) : (
              <div className="card-glass divide-y divide-border/50 max-h-[600px] overflow-y-auto">
                {history.map((entry) => (
                  <HistoryRow key={entry.id} entry={entry} />
                ))}
              </div>
            )}
          </div>
        </div>
      </main>
    </>
  );
}

function DripPositionRow({ pos }: { pos: DripPosition }) {
  return (
    <tr className="border-b border-border/50 hover:bg-surface-elevated/50 transition-colors">
      <td className="px-3 py-2">
        <div>
          <span className="font-mono font-semibold text-text-primary">{pos.ticker}</span>
          <p className="text-text-muted truncate max-w-[80px]" title={pos.name}>{pos.name}</p>
        </div>
      </td>
      <td className="px-3 py-2 text-right">
        <span className="font-mono text-accent font-semibold">
          {formatCurrency(pos.annual_income)}
        </span>
      </td>
      <td className="px-3 py-2 text-right font-mono text-text-secondary">
        {pos.yield_pct.toFixed(2)}%
      </td>
      <td className="px-3 py-2 text-right font-mono text-text-secondary">
        {pos.shares.toFixed(4)}
      </td>
      <td className="px-3 py-2 text-right font-mono text-text-secondary">
        {pos.drip_shares.toFixed(4)}
      </td>
      <td className="px-3 py-2 text-right font-mono text-text-secondary">
        {formatCurrency(pos.drip_value)}
      </td>
      <td className="px-3 py-2 text-right text-text-muted">
        {pos.ex_date ? formatDate(pos.ex_date) : "—"}
      </td>
      <td className="px-3 py-2 text-right text-text-muted">
        {pos.pay_date ? formatDate(pos.pay_date) : "—"}
      </td>
    </tr>
  );
}

function HistoryRow({ entry }: { entry: DripHistoryEntry }) {
  return (
    <div className="flex items-center justify-between px-4 py-3 hover:bg-surface-elevated/30 transition-colors">
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2">
          {entry.ticker && (
            <span className="font-mono text-xs font-semibold text-text-primary">
              {entry.ticker}
            </span>
          )}
          {entry.description && (
            <span className="text-xs text-text-muted truncate">
              {entry.description}
            </span>
          )}
        </div>
        <p className="text-xs text-text-muted mt-0.5">
          {formatDate(entry.tx_date)}
        </p>
      </div>
      <span className="font-mono text-sm font-semibold text-accent ml-4 shrink-0">
        +{formatCurrency(entry.amount)}
      </span>
    </div>
  );
}

function formatDate(dateStr: string): string {
  try {
    return new Date(dateStr).toLocaleDateString("en-US", {
      month: "short",
      day: "numeric",
      year: "2-digit",
    });
  } catch {
    return dateStr;
  }
}

function RefreshIcon({ className }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2}>
      <path d="M23 4v6h-6M1 20v-6h6" strokeLinecap="round" strokeLinejoin="round" />
      <path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

function DropletSmallIcon({ className }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="currentColor">
      <path d="M12 2.69l5.66 5.66a8 8 0 1 1-11.31 0z" />
    </svg>
  );
}
