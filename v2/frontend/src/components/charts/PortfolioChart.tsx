"use client";

import { useState } from "react";
import { cn, formatCurrency } from "@/lib/utils";
import { useSnapshots } from "@/lib/hooks";
import { InlineLoader } from "@/components/ui/Spinner";
import {
  ResponsiveContainer,
  AreaChart,
  Area,
  XAxis,
  YAxis,
  Tooltip,
  CartesianGrid,
} from "recharts";

const PERIODS = ["1W", "1M", "3M", "6M", "1Y"] as const;
type Period = (typeof PERIODS)[number];

const PERIOD_DAYS: Record<Period, number> = {
  "1W": 7,
  "1M": 30,
  "3M": 90,
  "6M": 180,
  "1Y": 365,
};

export function PortfolioChart() {
  const [period, setPeriod] = useState<Period>("1Y");
  const { data: snapshots, isLoading } = useSnapshots(100);

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

  const hasData = filtered.length > 1;

  return (
    <div className="space-y-4">
      {/* Period selector */}
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

      {/* Chart */}
      <div className="h-[200px] lg:h-[300px]">
        {isLoading ? (
          <InlineLoader text="Loading chart..." />
        ) : !hasData ? (
          <div className="h-full flex items-center justify-center rounded-md bg-surface/50">
            <div className="text-center space-y-1">
              <p className="text-text-muted text-sm">
                Not enough snapshot data yet
              </p>
              <p className="text-text-muted text-xs">
                Snapshots are created when you refresh your portfolio
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
