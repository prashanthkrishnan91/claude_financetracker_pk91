"use client";

import { useState } from "react";
import { cn, formatCurrency } from "@/lib/utils";
import { useSnapshots, useBackfillSnapshots } from "@/lib/hooks";
import { InlineLoader } from "@/components/ui/Spinner";
import { Spinner } from "@/components/ui/Spinner";
import {
  ResponsiveContainer,
  AreaChart,
  Area,
  XAxis,
  YAxis,
  Tooltip,
  CartesianGrid,
} from "recharts";

const PERIODS = ["1W", "1M", "3M", "6M", "1Y", "ALL"] as const;
type Period = (typeof PERIODS)[number];

const PERIOD_DAYS: Record<Period, number> = {
  "1W": 7,
  "1M": 30,
  "3M": 90,
  "6M": 180,
  "1Y": 365,
  "ALL": 99999,
};

export function PortfolioChart() {
  const [period, setPeriod] = useState<Period>("1Y");
  const [backfillMsg, setBackfillMsg] = useState<string | null>(null);
  const { data: snapshots, isLoading, refetch } = useSnapshots(500);
  const backfill = useBackfillSnapshots();

  // Filter snapshots by period
  const cutoff = new Date();
  cutoff.setDate(cutoff.getDate() - PERIOD_DAYS[period]);

  const filtered = (snapshots || [])
    .filter((s) => new Date(s.snapshot_at) >= cutoff)
    .sort(
      (a, b) =>
        new Date(a.snapshot_at).getTime() - new Date(b.snapshot_at).getTime()
    )
    .map((s) => ({
      date: new Date(s.snapshot_at).toLocaleDateString("en-US", {
        month: "short",
        day: "numeric",
      }),
      value: s.total_equity,
      pnl: s.total_pnl,
    }));

  const hasData = filtered.length >= 1;

  return (
    <div className="space-y-4">
      {/* Period selector + rebuild history button */}
      <div className="flex items-center justify-between gap-2">
        <div className="flex gap-1">
          {PERIODS.map((p) => (
            <button
              key={p}
              onClick={() => setPeriod(p)}
              className={cn(
                "px-3 py-1 text-xs rounded-md transition-colors",
                period === p
                  ? "bg-accent text-background font-semibold"
                  : "text-text-muted hover:text-text-primary hover:bg-surface-elevated"
              )}
            >
              {p}
            </button>
          ))}
        </div>
        <button
          onClick={() => {
            setBackfillMsg(null);
            backfill.mutate(undefined, {
              onSuccess: (data) => {
                refetch();
                setBackfillMsg(
                  data.created > 0
                    ? `Rebuilt ${data.created} historical snapshots`
                    : "History already up to date"
                );
              },
              onError: () => setBackfillMsg("Rebuild failed — try again"),
            });
          }}
          disabled={backfill.isPending}
          title="Rebuild chart history from your transaction data"
          className="flex items-center gap-1.5 px-2.5 py-1 text-[10px] rounded-md border border-border text-text-muted hover:text-text-primary hover:bg-surface-elevated transition-colors disabled:opacity-50 shrink-0"
        >
          {backfill.isPending ? (
            <Spinner className="h-3 w-3" />
          ) : (
            <svg className="w-3 h-3" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2}>
              <path d="M3 12a9 9 0 1 0 9-9 9.75 9.75 0 0 0-6.74 2.74L3 8" strokeLinecap="round" strokeLinejoin="round" />
              <path d="M3 3v5h5" strokeLinecap="round" strokeLinejoin="round" />
            </svg>
          )}
          Rebuild History
        </button>
      </div>
      {backfillMsg && (
        <p className="text-[10px] text-text-muted">{backfillMsg}</p>
      )}

      {/* Chart */}
      <div className="h-[200px] lg:h-[300px]">
        {isLoading ? (
          <InlineLoader text="Loading chart..." />
        ) : !hasData ? (
          <div className="h-full flex items-center justify-center rounded-md bg-surface/50">
            <div className="text-center space-y-1">
              <p className="text-text-muted text-sm">
                No snapshot data yet
              </p>
              <p className="text-text-muted text-xs">
                Your first snapshot is created automatically — reload to see it
              </p>
            </div>
          </div>
        ) : (
          <ResponsiveContainer width="100%" height="100%">
            <AreaChart data={filtered}>
              <defs>
                <linearGradient id="colorValue" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="5%" stopColor="#00e676" stopOpacity={0.3} />
                  <stop offset="95%" stopColor="#00e676" stopOpacity={0} />
                </linearGradient>
              </defs>
              <CartesianGrid
                strokeDasharray="3 3"
                stroke="#21262d"
                vertical={false}
              />
              <XAxis
                dataKey="date"
                tick={{ fill: "#8b949e", fontSize: 10 }}
                axisLine={false}
                tickLine={false}
              />
              <YAxis
                tick={{ fill: "#8b949e", fontSize: 10 }}
                axisLine={false}
                tickLine={false}
                tickFormatter={(v: number) => `$${(v / 1000).toFixed(0)}k`}
                width={50}
              />
              <Tooltip content={<ChartTooltip />} />
              <Area
                type="monotone"
                dataKey="value"
                stroke="#00e676"
                strokeWidth={2}
                fill="url(#colorValue)"
                dot={false}
                activeDot={{ r: 4, fill: "#00e676" }}
              />
            </AreaChart>
          </ResponsiveContainer>
        )}
      </div>
    </div>
  );
}

function ChartTooltip({ active, payload, label }: any) {
  if (!active || !payload?.length) return null;
  return (
    <div className="card-glass px-3 py-2 text-xs">
      <p className="text-text-muted">{label}</p>
      <p className="font-mono text-text-primary font-semibold">
        {formatCurrency(payload[0].value)}
      </p>
    </div>
  );
}
