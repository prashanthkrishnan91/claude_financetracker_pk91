"use client";

import { useState } from "react";
import { useParams, useRouter } from "next/navigation";
import { cn, formatCurrency, formatPercent, formatNumber, pnlClass } from "@/lib/utils";
import { usePosition, usePriceHistory } from "@/lib/hooks";
import { PageLoader, InlineLoader } from "@/components/ui/Spinner";
import {
  ResponsiveContainer,
  AreaChart,
  Area,
  XAxis,
  YAxis,
  Tooltip,
  CartesianGrid,
} from "recharts";

const PERIODS = ["1W", "1M", "3M", "6M", "1Y", "5Y"] as const;
type Period = (typeof PERIODS)[number];

export default function PositionDetailPage() {
  const params = useParams();
  const router = useRouter();
  const ticker = (params.ticker as string)?.toUpperCase();
  const [period, setPeriod] = useState<Period>("1Y");

  const { data: position, isLoading: posLoading } = usePosition(ticker);
  const { data: history, isLoading: histLoading } = usePriceHistory(
    ticker,
    period
  );

  if (posLoading) return <PageLoader />;

  const price = position?.current_price ?? position?.avg_cost ?? 0;
  const shares = position?.shares ?? 0;
  const marketValue = shares * price;
  const costBasis = shares * (position?.avg_cost ?? 0);
  const pnl = marketValue - costBasis;
  const pnlPct = costBasis > 0 ? (pnl / costBasis) * 100 : 0;

  const chartData = (history?.data_points || []).map((p) => ({
    date: new Date(p.price_date).toLocaleDateString("en-US", {
      month: "short",
      day: "numeric",
    }),
    price: p.close_price,
  }));

  return (
    <>
      <header className="sticky top-0 z-50 bg-background/80 backdrop-blur-xl border-b border-border px-4 py-3">
        <div className="max-w-7xl mx-auto flex items-center gap-3">
          <button
            onClick={() => router.back()}
            className="text-text-muted hover:text-text-primary transition-colors"
          >
            <svg
              className="w-5 h-5"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth={2}
            >
              <path
                d="M19 12H5M12 19l-7-7 7-7"
                strokeLinecap="round"
                strokeLinejoin="round"
              />
            </svg>
          </button>
          <div>
            <h1 className="text-xl font-display text-text-primary">
              {ticker}
            </h1>
            {position && (
              <p className="text-xs text-text-muted">{position.name}</p>
            )}
          </div>
          {position && (
            <span className="text-xs px-1.5 py-0.5 rounded bg-surface-elevated text-text-muted ml-auto">
              {position.category}
            </span>
          )}
        </div>
      </header>

      <main className="max-w-7xl mx-auto px-4 py-6 space-y-6">
        {/* Price hero */}
        <div>
          <p className="text-3xl font-display text-text-primary">
            {formatCurrency(price)}
          </p>
          <p className={cn("text-sm font-semibold", pnlClass(pnl))}>
            {formatCurrency(pnl)} ({formatPercent(pnlPct)})
          </p>
        </div>

        {/* Chart */}
        <div className="card-glass p-4 space-y-3">
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

          <div className="h-[200px] lg:h-[280px]">
            {histLoading ? (
              <InlineLoader text="Loading chart..." />
            ) : chartData.length > 1 ? (
              <ResponsiveContainer width="100%" height="100%">
                <AreaChart data={chartData}>
                  <defs>
                    <linearGradient
                      id="colorPrice"
                      x1="0"
                      y1="0"
                      x2="0"
                      y2="1"
                    >
                      <stop
                        offset="5%"
                        stopColor="#00e676"
                        stopOpacity={0.3}
                      />
                      <stop
                        offset="95%"
                        stopColor="#00e676"
                        stopOpacity={0}
                      />
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
                    tickFormatter={(v: number) =>
                      v >= 1000 ? `$${(v / 1000).toFixed(0)}k` : `$${v}`
                    }
                    width={55}
                    domain={["auto", "auto"]}
                  />
                  <Tooltip content={<PriceTooltip />} />
                  <Area
                    type="monotone"
                    dataKey="price"
                    stroke="#00e676"
                    strokeWidth={2}
                    fill="url(#colorPrice)"
                    dot={false}
                    activeDot={{ r: 4, fill: "#00e676" }}
                  />
                </AreaChart>
              </ResponsiveContainer>
            ) : (
              <div className="h-full flex items-center justify-center">
                <p className="text-text-muted text-sm">No chart data available</p>
              </div>
            )}
          </div>
        </div>

        {/* Stats grid */}
        {position && (
          <div className="grid grid-cols-2 gap-3">
            <StatCard label="Shares" value={formatNumber(shares, 4)} />
            <StatCard label="Avg Cost" value={formatCurrency(position.avg_cost)} />
            <StatCard label="Market Value" value={formatCurrency(marketValue)} />
            <StatCard label="Cost Basis" value={formatCurrency(costBasis)} />
            <StatCard
              label="P&L"
              value={formatCurrency(pnl)}
              pnl={pnl}
            />
            <StatCard
              label="P&L %"
              value={formatPercent(pnlPct)}
              pnl={pnl}
            />
            {position.lt_eligible && (
              <StatCard label="Tax Status" value="Long-term eligible" />
            )}
            {position.lt_date && (
              <StatCard label="LT Date" value={position.lt_date} />
            )}
            {(position.drip_shares ?? 0) > 0 && (
              <StatCard
                label="DRIP Shares"
                value={formatNumber(position.drip_shares!, 4)}
              />
            )}
            {(position.divs_received ?? 0) > 0 && (
              <StatCard
                label="Dividends"
                value={formatCurrency(position.divs_received!)}
              />
            )}
            <StatCard label="Source" value={position.source} />
          </div>
        )}
      </main>
    </>
  );
}

function StatCard({
  label,
  value,
  pnl,
}: {
  label: string;
  value: string;
  pnl?: number;
}) {
  return (
    <div className="card-glass p-3">
      <p className="text-xs text-text-muted">{label}</p>
      <p
        className={cn(
          "text-sm font-mono font-semibold mt-0.5",
          pnl !== undefined ? pnlClass(pnl) : "text-text-primary"
        )}
      >
        {value}
      </p>
    </div>
  );
}

function PriceTooltip({ active, payload, label }: any) {
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
